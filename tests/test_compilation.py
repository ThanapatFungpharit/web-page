"""Loaded programs retain their syntax and reuse successful dependency linking."""

import importlib
from typing import Any

import pytest
import yaml

from tests.conftest import FakePage, WriteYaml
from web_page import ActionContext, WebPage, utils
from web_page.exceptions import (
    BadActionFile,
    BadActionParams,
    BadParamType,
    HookNotRegistered,
    SelectorsNotLoaded,
)

runner_module = importlib.import_module("web_page.web_page")


async def load_action(
    page: WebPage,
    write_yaml: WriteYaml,
    name: str,
    steps: list,
    params: tuple[str, ...] = (),
) -> None:
    await page.load_action(
        write_yaml(
            f"{name}.yaml",
            yaml.safe_dump(
                {
                    "name": name,
                    "selectors_name": "s",
                    "params": list(params),
                    "steps": steps,
                }
            ),
        )
    )


async def load_selectors(
    page: WebPage, write_yaml: WriteYaml, selectors: dict[str, str] | None = None
) -> None:
    await page.load_selectors(
        write_yaml(
            "s.yaml", yaml.safe_dump({"name": "s", "selectors": selectors or {}})
        )
    )


async def test_loaded_program_needs_no_template_parser_after_cache_eviction(
    web_page: WebPage,
    fake_page: FakePage,
    write_yaml: WriteYaml,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Both manifests exceed the fallback LRU capacity. Every step's syntax must
    # remain available even when none of it survives in that shared cache.
    await load_selectors(
        web_page,
        write_yaml,
        {f"item{i}": f"[data-i='{i}'][id='{{id:string}}']" for i in range(1100)},
    )
    await load_action(
        web_page,
        write_yaml,
        "a",
        [{"get_url": "saved"}]
        + [
            {"fill": {"target": f"item{i}", "value": f"{i}-{{id}}-{{result.saved}}"}}
            for i in range(1100)
        ],
        ("id",),
    )
    utils._compile_template.cache_clear()

    def unexpected_parse(*args):
        pytest.fail("Loaded programs must retain compiled templates")

    monkeypatch.setattr(utils, "_compile_template", unexpected_parse)
    await web_page.run("a", {"id": "first"})
    await web_page.run("a", {"id": "second"})
    assert fake_page.calls[0] == (
        "fill",
        "[data-i='0'][id='first']",
        "0-first-about:blank",
    )
    assert fake_page.calls[-1] == (
        "fill",
        "[data-i='1099'][id='second']",
        "1099-second-about:blank",
    )


async def test_repeated_runs_reuse_static_plan_but_validate_new_values(
    web_page: WebPage,
    fake_page: FakePage,
    write_yaml: WriteYaml,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await load_selectors(web_page, write_yaml, {"item": "#{id}"})
    await load_action(
        web_page,
        write_yaml,
        "a",
        [{"fill": {"target": "item", "value": "{id}"}}],
        ("id",),
    )
    await web_page.run("a", {"id": "first"})

    def unexpected_analysis(*args):
        pytest.fail("A linked action must not repeat static analysis")

    monkeypatch.setattr(runner_module, "walk", unexpected_analysis)
    monkeypatch.setattr(runner_module, "_check_results", unexpected_analysis)
    await web_page.run("a", {"id": "second"})
    with pytest.raises(BadActionParams):
        await web_page.run("a", {})
    invalid: Any = {"id": 1}
    with pytest.raises(BadParamType):
        await web_page.run("a", invalid)
    assert fake_page.calls == [
        ("fill", "#first", "first"),
        ("fill", "#second", "second"),
    ]


async def test_failed_link_recovers_when_dependencies_load(
    web_page: WebPage, fake_page: FakePage, write_yaml: WriteYaml
) -> None:
    async def ready(ctx: ActionContext) -> str:
        return "ready"

    await load_action(
        web_page, write_yaml, "a", [{"call": {"hook": "ready", "as": "state"}}]
    )
    with pytest.raises(SelectorsNotLoaded):
        await web_page.run("a")
    await load_selectors(web_page, write_yaml)
    with pytest.raises(HookNotRegistered):
        await web_page.run("a")
    web_page.register("ready", ready)
    assert await web_page.run("a") == {"state": "ready"}
    assert await web_page.run("a") == {"state": "ready"}
    assert fake_page.calls == []


async def test_shared_child_is_linked_once_and_runs_with_fresh_contexts(
    web_page: WebPage, write_yaml: WriteYaml, monkeypatch: pytest.MonkeyPatch
) -> None:
    await load_selectors(web_page, write_yaml)
    await load_action(web_page, write_yaml, "child", [{"get_url": "url"}])
    await load_action(
        web_page,
        write_yaml,
        "parent",
        [
            {"run": {"action": "child", "results": {"url": f"url{i}"}}}
            for i in range(20)
        ],
    )
    analyzed = 0
    original = runner_module._check_results

    def count_analysis(*args):
        nonlocal analyzed
        analyzed += 1
        return original(*args)

    monkeypatch.setattr(runner_module, "_check_results", count_analysis)
    expected = {f"url{i}": "about:blank" for i in range(20)}
    assert await web_page.run("parent") == expected
    assert await web_page.run("parent") == expected
    assert analyzed == 2


async def test_cached_link_detects_recursion_through_hook(
    web_page: WebPage, fake_page: FakePage, write_yaml: WriteYaml
) -> None:
    follow = False

    async def enter_child(ctx: ActionContext) -> None:
        if follow:
            assert ctx.run_action is not None
            await ctx.run_action("child", {})

    web_page.register("enter", enter_child)
    await load_selectors(web_page, write_yaml)
    await load_action(web_page, write_yaml, "parent", [{"call": "enter"}])
    await load_action(
        web_page,
        write_yaml,
        "child",
        [{"goto": "https://example.test"}, {"run": "parent"}],
    )
    await web_page.run("child")
    fake_page.calls.clear()
    follow = True
    with pytest.raises(BadActionFile, match="parent -> child -> parent"):
        await web_page.run("parent")
    assert fake_page.calls == []


async def test_selector_state_is_not_shared_between_runs(
    web_page: WebPage, write_yaml: WriteYaml
) -> None:
    async def change_selector(ctx: ActionContext) -> None:
        assert isinstance(ctx.sel, utils.SelectorTemplates)
        assert ctx.sel["item"] == "#original"
        assert isinstance(ctx.sel.raw, dict)
        ctx.sel.raw["item"] = "#temporary"

    web_page.register("change", change_selector)
    await load_selectors(web_page, write_yaml, {"item": "#original"})
    await load_action(web_page, write_yaml, "a", [{"call": "change"}])
    await web_page.run("a")
    await web_page.run("a")

"""Regression coverage for validation, data flow and bounded workflows."""

import asyncio
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from playwright._impl._glob import glob_to_regex_pattern

from tests.conftest import (
    FakeDownload,
    FakeFileChooser,
    FakeLocator,
    FakePage,
    WriteYaml,
)
from web_page import ActionContext, WebPage
from web_page.exceptions import (
    ActionFailed,
    ActionNotLoaded,
    BadActionFile,
    BadActionParams,
    BadParamType,
    BadResultValue,
    BadSelectorsFile,
    HookNotRegistered,
)
from web_page.manifest import read_manifest
from web_page.steps import _glob, parse_steps, run_steps
from web_page.utils import SelectorTemplates, fill


async def load_action(
    wrapper: WebPage,
    write_yaml: WriteYaml,
    steps: list,
    *,
    name: str = "a",
    params: tuple[str, ...] = (),
    selectors: dict | None = None,
) -> None:
    if selectors is not None:
        await wrapper.load_selectors(
            write_yaml("s.yaml", yaml.safe_dump({"name": "s", "selectors": selectors}))
        )
    await wrapper.load_action(
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


@pytest.mark.parametrize("value", [None, 42, True, {}, (), bytearray(b"x")])
async def test_bad_param_types_fail_before_navigation(
    web_page: WebPage, fake_page: FakePage, write_yaml: WriteYaml, value: Any
) -> None:
    await load_action(
        web_page,
        write_yaml,
        [{"goto": "https://example.test"}, {"click": "item"}],
        params=("id",),
        selectors={"item": "#{id}"},
    )
    with pytest.raises(BadParamType):
        await web_page.run("a", {"id": value})
    assert fake_page.calls == []


@pytest.mark.parametrize(
    "raw",
    [
        {"click": {"target": "a", "buton": "right"}},
        {"wait_for": {"target": "a", "sttae": "detached"}},
        {"fill": {"target": "a", "value": "x", "unknown": 1}},
        {"get_text": {"target": "a", "as": "text", "exlude": "icons"}},
        {
            "get_attribute": {
                "target": "a",
                "as": "attr",
                "attribute": "id",
                "unknown": 1,
            }
        },
        {"upload": {"click": "a", "files": [], "unknown": 1}},
        {"download": {"target": "a", "to": "out", "unknown": 1}},
        {"if": {"visible": {"target": "a", "timout": 5}, "then": []}},
        {"call": {"hook": "a", "unknown": 1}},
        {"run": {"action": "a", "unknown": 1}},
        {"repeat": {"times": 2, "steps": [], "unknown": 1}},
        {"retry": {"attempts": 2, "steps": [], "unknown": 1}},
    ],
)
def test_unknown_step_fields_are_rejected(raw: dict) -> None:
    with pytest.raises(BadActionFile, match="unknown fields"):
        parse_steps([raw], [])


@pytest.mark.parametrize(
    "body",
    [
        "name: a\nname: b\n",
        "steps:\n  - click: safe\n    click: destructive\n",
        "selectors: {item: '#safe', item: '#other'}\n",
        "steps:\n  - fill: {<<: {target: safe, target: other, value: x}}\n",
        "base: &base {target: a}\nmerged: {<<: *base, <<: *base}\n",
    ],
)
async def test_duplicate_yaml_keys_are_rejected(
    write_yaml: WriteYaml, body: str
) -> None:
    with pytest.raises(BadActionFile, match="duplicate key"):
        await read_manifest(write_yaml("duplicate.yaml", body), BadActionFile, "Action")


async def test_yaml_merge_override_is_still_supported(write_yaml: WriteYaml) -> None:
    manifest = await read_manifest(
        write_yaml(
            "merge.yaml",
            "base: &base {target: a, value: x}\nmerged: {<<: *base, value: y}\n",
        ),
        BadActionFile,
        "Action",
    )
    assert manifest["merged"] == {"target": "a", "value": "y"}


async def test_repeated_yaml_merge_aliases_keep_explicit_overrides(
    write_yaml: WriteYaml,
) -> None:
    manifest = await read_manifest(
        write_yaml(
            "aliases.yaml",
            "base: &base {<<: {target: a, value: x}, value: y}\nfirst: {<<: *base}\nsecond: {<<: *base}\n",
        ),
        BadActionFile,
        "Action",
    )
    assert (
        manifest["base"]
        == manifest["first"]
        == manifest["second"]
        == {"target": "a", "value": "y"}
    )


@pytest.mark.parametrize("kind", ["action", "selectors"])
async def test_unknown_manifest_fields_are_rejected(
    web_page: WebPage, write_yaml: WriteYaml, kind: str
) -> None:
    value = (
        {"name": "a", "selectors": {}, "typo": True}
        if kind == "selectors"
        else {"name": "a", "selectors_name": [], "steps": [], "typo": True}
    )
    loader = web_page.load_selectors if kind == "selectors" else web_page.load_action
    with pytest.raises(
        BadSelectorsFile if kind == "selectors" else BadActionFile,
        match="Unknown manifest fields",
    ):
        await loader(write_yaml("bad.yaml", yaml.safe_dump(value)))


@pytest.mark.parametrize(
    ("pattern", "url"),
    [
        ("**/{report,export}.csv", "https://example.test/export.csv"),
        ("**/a/**/report.csv", "https://example.test/a/report.csv"),
        ("**/a/**/report.csv", "https://example.test/a/b/report.csv"),
        ("**/report.csv", "report.csv"),
        ("https://*/a", "https://example.test/b/a"),
        (r"**/file\*.csv", "https://example.test/file*.csv"),
        ("**/{report,export}.csv", "https://example.test/other.csv"),
    ],
)
def test_url_globs_match_playwright(pattern: str, url: str) -> None:
    assert bool(_glob(pattern).fullmatch(url)) == bool(
        re.fullmatch(glob_to_regex_pattern(pattern), url)
    )


async def test_wait_accepts_common_optional_mapping(fake_page: FakePage) -> None:
    await run_steps(
        parse_steps([{"wait": {"ms": 10, "optional": True}}], []),
        ActionContext(page=fake_page, sel={}, params={}),
    )
    assert fake_page.calls == [("wait_for_timeout", "10")]


@pytest.mark.parametrize(
    "files",
    [
        "missing.txt",
        {"path": "missing.txt"},
        {"path": "missing.txt", "name": "renamed.txt"},
        {"param": "paths"},
    ],
)
async def test_missing_upload_fails_before_trigger(
    fake_page: FakePage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, files: Any
) -> None:
    monkeypatch.chdir(tmp_path)
    ctx = ActionContext(
        page=fake_page, sel={"add": "#add"}, params={"paths": ["missing.txt"]}
    )
    with pytest.raises(FileNotFoundError):
        await run_steps(
            parse_steps([{"upload": {"click": "add", "files": files}}], ["paths"]), ctx
        )
    assert fake_page.calls == []


@pytest.mark.parametrize("phase", ["trigger", "save", "chooser"])
async def test_file_timeout_cancels_every_phase(
    fake_page: FakePage, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    completed = []

    async def slow(*args, **kwargs):
        await asyncio.sleep(1)
        completed.append(True)

    if phase == "trigger":
        monkeypatch.setattr(FakeLocator, "click", slow)
    elif phase == "save":
        monkeypatch.setattr(FakeDownload, "save_as", slow)
    else:
        monkeypatch.setattr(FakeFileChooser, "set_files", slow)
    raw = (
        {"upload": {"click": "a", "files": [], "timeout": 20, "optional": True}}
        if phase == "chooser"
        else {
            "download": {
                "target": "a",
                "to": "unused",
                "as": "saved",
                "timeout": 20,
                "optional": True,
            }
        }
    )
    ctx = ActionContext(page=fake_page, sel={"a": "#a"}, params={}, timeout_errors=())
    await asyncio.wait_for(run_steps(parse_steps([raw], []), ctx), timeout=0.5)
    assert completed == []
    if phase != "chooser":
        assert ctx.results == {"saved": None}


def test_download_cannot_read_its_own_future_result() -> None:
    with pytest.raises(BadActionFile, match="no earlier step saves"):
        parse_steps(
            [
                {
                    "download": {
                        "steps": [{"if": {"result": "saved", "then": []}}],
                        "to": "out",
                        "as": "saved",
                    }
                }
            ],
            [],
        )


def test_download_destination_can_use_trigger_result() -> None:
    parse_steps(
        [
            {
                "download": {
                    "steps": [{"get_url": "destination"}],
                    "to": "{result.destination}",
                    "as": "saved",
                }
            }
        ],
        [],
    )


async def test_hook_outputs_feed_conditions_templates_and_selectors(
    web_page: WebPage, fake_page: FakePage, write_yaml: WriteYaml
) -> None:
    async def pick(ctx: ActionContext):
        ctx.results["model"] = "a.b"
        return "chosen"

    web_page.register("pick", pick)
    await load_action(
        web_page,
        write_yaml,
        [
            {"call": {"hook": "pick", "as": "label", "results": ["model"]}},
            {
                "if": {
                    "equals": {"result": "model", "value": "a.b"},
                    "then": [{"fill": {"target": "item", "value": "{result.label}"}}],
                }
            },
        ],
        selectors={"item": "#{result.model:css}"},
    )
    assert await web_page.run("a") == {"model": "a.b", "label": "chosen"}
    assert fake_page.calls == [("fill", r"#a\.b", "chosen")]


async def test_selector_result_order_is_validated_before_navigation(
    web_page: WebPage, fake_page: FakePage, write_yaml: WriteYaml
) -> None:
    await load_action(
        web_page,
        write_yaml,
        [{"goto": "https://example.test"}, {"click": "item"}, {"get_url": "id"}],
        selectors={"item": "#{result.id}"},
    )
    with pytest.raises(BadActionFile, match="no earlier step saves"):
        await web_page.run("a")
    assert fake_page.calls == []


@pytest.mark.parametrize("value", [None, ["a"], {"a": "b"}])
def test_non_string_results_are_not_silently_formatted(value: Any) -> None:
    with pytest.raises(BadResultValue):
        fill("{result.saved}", {}, {"saved": value})


async def test_repeat_and_poll_use_latest_results(fake_page: FakePage) -> None:
    count = 0

    async def poll(ctx: ActionContext):
        nonlocal count
        count += 1
        return str(count)

    ctx = ActionContext(page=fake_page, sel={}, params={}, hooks={"poll": poll})
    raw = [
        {
            "repeat": {
                "times": 5,
                "interval": 1,
                "steps": [{"call": {"hook": "poll", "as": "count"}}],
                "until": {"equals": {"result": "count", "value": "3"}},
            }
        }
    ]
    await run_steps(parse_steps(raw, []), ctx)
    assert count == 3 and ctx.results == {"count": "3"}


async def test_repeat_exhaustion_reports_results(fake_page: FakePage) -> None:
    ctx = ActionContext(page=fake_page, sel={}, params={})
    with pytest.raises(ActionFailed, match="after 2 iterations") as error:
        await run_steps(
            parse_steps(
                [
                    {
                        "repeat": {
                            "times": 2,
                            "steps": [{"get_url": "url"}],
                            "until": {"equals": {"result": "url", "value": "never"}},
                        }
                    }
                ],
                [],
            ),
            ctx,
        )
    assert error.value.results == {"url": "about:blank"}


@pytest.mark.parametrize("error_type", [TimeoutError, ActionFailed])
async def test_retry_restarts_result_state(
    fake_page: FakePage, error_type: type[Exception]
) -> None:
    calls = 0

    async def flaky(ctx: ActionContext):
        nonlocal calls
        calls += 1
        assert "partial" not in ctx.results
        if calls == 1:
            ctx.results["partial"] = "stale"
            raise (
                ActionFailed("retry", ctx.results)
                if error_type is ActionFailed
                else TimeoutError()
            )
        return "ok"

    ctx = ActionContext(page=fake_page, sel={}, params={}, hooks={"flaky": flaky})
    await run_steps(
        parse_steps(
            [
                {
                    "retry": {
                        "attempts": 2,
                        "steps": [{"call": {"hook": "flaky", "as": "status"}}],
                    }
                }
            ],
            [],
        ),
        ctx,
    )
    assert calls == 2 and ctx.results == {"status": "ok"}


async def test_retry_does_not_hide_programming_errors(fake_page: FakePage) -> None:
    calls = 0

    async def broken(ctx):
        nonlocal calls
        calls += 1
        raise ValueError("broken")

    with pytest.raises(ValueError, match="broken"):
        await run_steps(
            parse_steps(
                [{"retry": {"attempts": 3, "steps": [{"call": "broken"}]}}], []
            ),
            ActionContext(page=fake_page, sel={}, params={}, hooks={"broken": broken}),
        )
    assert calls == 1


@pytest.mark.parametrize("count", [0, -1, True, 1.5, "2"])
@pytest.mark.parametrize(
    ("kind", "field"), [("repeat", "times"), ("retry", "attempts")]
)
def test_workflows_require_finite_integer_counts(
    kind: str, field: str, count: Any
) -> None:
    with pytest.raises(BadActionFile, match="positive integer"):
        parse_steps([{kind: {field: count, "steps": []}}], [])


async def test_nested_actions_share_outer_lock_and_export_results(
    web_page: WebPage, fake_page: FakePage, write_yaml: WriteYaml
) -> None:
    await load_action(
        web_page,
        write_yaml,
        [{"fill": {"target": "item", "value": "{text}"}}, {"get_url": "url"}],
        name="child",
        params=("text",),
        selectors={"item": "#item"},
    )
    await load_action(
        web_page,
        write_yaml,
        [
            {
                "run": {
                    "action": "child",
                    "params": {"text": "{text}"},
                    "results": {"url": "child_url"},
                }
            },
            {"fill": {"target": "item", "value": "{text}"}},
        ],
        params=("text",),
    )
    results = await asyncio.wait_for(
        asyncio.gather(*(web_page.run("a", {"text": str(i)}) for i in range(3))),
        timeout=1,
    )
    assert all(r == {"child_url": "about:blank"} for r in results)
    assert [call[2] for call in fake_page.calls] == ["0", "0", "1", "1", "2", "2"]


async def test_nested_dependencies_are_checked_before_first_step(
    web_page: WebPage, fake_page: FakePage, write_yaml: WriteYaml
) -> None:
    await load_action(
        web_page, write_yaml, [{"call": "missing_hook"}], name="child", selectors={}
    )
    await load_action(
        web_page, write_yaml, [{"goto": "https://example.test"}, {"run": "child"}]
    )
    with pytest.raises(HookNotRegistered):
        await web_page.run("a")
    assert fake_page.calls == []


async def test_recursive_actions_are_rejected(
    web_page: WebPage, fake_page: FakePage, write_yaml: WriteYaml
) -> None:
    await load_action(web_page, write_yaml, [{"run": "child"}], selectors={})
    await load_action(web_page, write_yaml, [{"run": "a"}], name="child")
    with pytest.raises(BadActionFile, match="Recursive action calls"):
        await web_page.run("a")
    assert fake_page.calls == []


async def test_unknown_child_is_rejected(
    web_page: WebPage, fake_page: FakePage, write_yaml: WriteYaml
) -> None:
    await load_action(
        web_page,
        write_yaml,
        [{"goto": "https://example.test"}, {"run": "missing"}],
        selectors={},
    )
    with pytest.raises(ActionNotLoaded):
        await web_page.run("a")
    assert fake_page.calls == []


@pytest.mark.parametrize("values", [{}, {"wrong": "value"}])
async def test_child_param_names_checked_before_navigation(
    web_page: WebPage, fake_page: FakePage, write_yaml: WriteYaml, values: dict
) -> None:
    await load_action(
        web_page, write_yaml, [], name="child", params=("required",), selectors={}
    )
    await load_action(
        web_page,
        write_yaml,
        [
            {"goto": "https://example.test"},
            {"run": {"action": "child", "params": values}},
        ],
    )
    with pytest.raises(BadActionParams):
        await web_page.run("a")
    assert fake_page.calls == []


async def test_unknown_child_output_checked_before_navigation(
    web_page: WebPage, fake_page: FakePage, write_yaml: WriteYaml
) -> None:
    await load_action(web_page, write_yaml, [], name="child", selectors={})
    await load_action(
        web_page,
        write_yaml,
        [
            {"goto": "https://example.test"},
            {"run": {"action": "child", "results": {"unknown": "saved"}}},
        ],
    )
    with pytest.raises(BadActionFile, match="does not save results"):
        await web_page.run("a")
    assert fake_page.calls == []


async def test_optional_child_initializes_outputs(
    web_page: WebPage, write_yaml: WriteYaml
) -> None:
    async def timeout(ctx):
        raise TimeoutError()

    web_page.register("timeout", timeout)
    await load_action(
        web_page,
        write_yaml,
        [{"call": "timeout"}, {"get_url": "url"}],
        name="child",
        selectors={},
    )
    await load_action(
        web_page,
        write_yaml,
        [{"run": {"action": "child", "results": {"url": "saved"}, "optional": True}}],
    )
    assert await web_page.run("a") == {"saved": None}


async def test_optional_hook_initializes_all_outputs(fake_page: FakePage) -> None:
    async def timeout(ctx):
        ctx.results["side"] = "partial"
        raise TimeoutError()

    ctx = ActionContext(page=fake_page, sel={}, params={}, hooks={"timeout": timeout})
    await run_steps(
        parse_steps(
            [
                {
                    "call": {
                        "hook": "timeout",
                        "results": ["side"],
                        "as": "returned",
                        "optional": True,
                    }
                }
            ],
            [],
        ),
        ctx,
    )
    assert ctx.results == {"side": None, "returned": None}


@pytest.mark.parametrize("declaration", [{"as": "value"}, {"results": ["value"]}])
async def test_invalid_hook_outputs_fail_clearly(
    fake_page: FakePage, declaration: dict
) -> None:
    async def hook(ctx):
        ctx.results["value"] = {"invalid": True}
        return 42

    ctx = ActionContext(page=fake_page, sel={}, params={}, hooks={"hook": hook})
    with pytest.raises(BadResultValue):
        await run_steps(
            parse_steps([{"call": {"hook": "hook", **declaration}}], []), ctx
        )


async def test_retry_exhausts_exact_attempt_count(fake_page: FakePage) -> None:
    count = 0

    async def timeout(ctx):
        nonlocal count
        count += 1
        raise TimeoutError("still unavailable")

    ctx = ActionContext(page=fake_page, sel={}, params={}, hooks={"timeout": timeout})
    with pytest.raises(TimeoutError, match="still unavailable"):
        await run_steps(
            parse_steps(
                [{"retry": {"attempts": 3, "steps": [{"call": "timeout"}]}}], []
            ),
            ctx,
        )
    assert count == 3


async def test_repeat_clears_branch_outputs_between_iterations(
    fake_page: FakePage,
) -> None:
    count = 0

    async def advance(ctx):
        nonlocal count
        count += 1
        return str(count)

    ctx = ActionContext(page=fake_page, sel={}, params={}, hooks={"advance": advance})
    await run_steps(
        parse_steps(
            [
                {
                    "repeat": {
                        "times": 2,
                        "steps": [
                            {"call": {"hook": "advance", "as": "count"}},
                            {
                                "if": {
                                    "equals": {"result": "count", "value": "1"},
                                    "then": [{"get_url": "first_iteration"}],
                                }
                            },
                        ],
                    }
                }
            ],
            [],
        ),
        ctx,
    )
    assert ctx.results == {"count": "2"}


def test_selector_mapping_tracks_available_values() -> None:
    params, results = {"id": "a", "blob": b"data"}, {}
    selectors = SelectorTemplates(
        {"static": "#{id}", "future": "#{result.id}", "bytes": "#{blob}"},
        params,
        results,
    )
    assert dict(selectors) == {"static": "#a"}
    assert "future" not in selectors
    with pytest.raises(KeyError):
        _ = selectors["bytes"]
    results["id"] = "b"
    assert selectors["future"] == "#b"
    assert "future" in selectors

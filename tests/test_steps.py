from typing import cast

import pytest
from playwright.async_api import Page

from tests.conftest import FakePage
from web_page.exceptions import BadActionFile
from web_page.steps import Click, Fill, Goto, Step, WaitFor, parse_steps
from web_page.types import ActionContext


def test_parse_steps_builds_each_kind() -> None:
    raw = [
        {"goto": "https://example.com/{q}"},
        {"fill": {"target": "search", "value": "{q}"}},
        {"click": "submit"},
        {"wait_for": "results"},
    ]
    assert parse_steps(raw, ["q"]) == (
        Goto("https://example.com/{q}"),
        Fill("search", "{q}"),
        Click("submit"),
        WaitFor("results"),
    )


def test_parse_steps_empty() -> None:
    assert parse_steps([], []) == ()


@pytest.mark.parametrize("raw", [None, "click: x", {"click": "x"}])
def test_parse_steps_not_a_list(raw: object) -> None:
    with pytest.raises(BadActionFile, match="should be a list"):
        parse_steps(raw, [])


@pytest.mark.parametrize(
    "item", ["click", {}, {"click": "a", "wait_for": "b"}, ["click", "a"]]
)
def test_parse_steps_item_not_single_key_mapping(item: object) -> None:
    with pytest.raises(BadActionFile, match="single-key mapping"):
        parse_steps([item], [])


@pytest.mark.parametrize("kind", ["hover", 1, None])
def test_parse_steps_unknown_kind(kind: object) -> None:
    with pytest.raises(BadActionFile, match="Unknown step"):
        parse_steps([{kind: "x"}], [])


@pytest.mark.parametrize("kind", ["goto", "click", "wait_for"])
def test_string_steps_reject_non_string(kind: str) -> None:
    with pytest.raises(BadActionFile, match="needs a string value"):
        parse_steps([{kind: 1}], [])


def test_goto_rejects_undeclared_param() -> None:
    with pytest.raises(BadActionFile, match="undeclared params"):
        parse_steps([{"goto": "https://example.com/{q}"}], [])


@pytest.mark.parametrize(
    "value",
    ["search", {"value": "x"}, {"target": 1, "value": "x"}],
)
def test_fill_rejects_bad_target(value: object) -> None:
    with pytest.raises(BadActionFile, match="'target' and 'value'"):
        parse_steps([{"fill": value}], [])


@pytest.mark.parametrize("template", [None, 1])
def test_fill_rejects_non_string_value(template: object) -> None:
    with pytest.raises(BadActionFile, match="needs a string value"):
        parse_steps([{"fill": {"target": "search", "value": template}}], [])


def test_fill_rejects_undeclared_param() -> None:
    with pytest.raises(BadActionFile, match="undeclared params"):
        parse_steps([{"fill": {"target": "search", "value": "{q}"}}], [])


@pytest.mark.parametrize(
    ("step", "key"),
    [(Goto("u"), None), (Click("a"), "a"), (Fill("b", "v"), "b"), (WaitFor("c"), "c")],
)
def test_selector_key(step: Step, key: str | None) -> None:
    assert step.selector_key == key


def test_duplicate_kind_is_rejected() -> None:
    with pytest.raises(TypeError, match="already defined"):

        class Again(Click, kind="click"):
            pass


async def test_steps_run_against_page(fake_page: FakePage) -> None:
    ctx = ActionContext(
        page=cast(Page, fake_page),
        sel={"search": "#q", "submit": "button", "results": ".r"},
        params={"q": "cats"},
    )
    for step in (
        Goto("https://example.com/?q={q}"),
        Fill("search", "{q} and dogs"),
        Click("submit"),
        WaitFor("results"),
    ):
        await step.run(ctx)

    assert fake_page.calls == [
        ("goto", "https://example.com/?q=cats", "domcontentloaded"),
        ("fill", "#q", "cats and dogs"),
        ("click", "button"),
        ("wait_for", ".r", "visible"),
    ]

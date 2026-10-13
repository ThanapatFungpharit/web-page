import pytest

from tests.conftest import FakePage
from web_page.exceptions import BadActionFile
from web_page.steps import (
    Check,
    Click,
    Fill,
    GetAllTexts,
    GetAttribute,
    GetText,
    GetUrl,
    GetValue,
    Goto,
    Hover,
    Press,
    Select,
    Step,
    TargetStep,
    Uncheck,
    Wait,
    WaitFor,
    WaitForUrl,
    parse_steps,
)
from web_page.types import ActionContext


def test_parse_steps_builds_each_kind() -> None:
    raw = [
        {"goto": "https://example.com/{q}"},
        {"fill": {"target": "search", "value": "{q}"}},
        {"click": "submit"},
        {"wait_for": "results"},
        {"hover": "menu"},
        {"check": "agree"},
        {"uncheck": "newsletter"},
        {"select": {"target": "country", "value": "{q}"}},
        {"press": {"target": "search", "key": "Enter"}},
        {"wait_for_url": "**/results?q={q}"},
        {"wait": 250},
        {"get_url": "url"},
        {"get_text": {"target": "results", "as": "first"}},
        {"get_all_texts": {"target": "results", "as": "all"}},
        {"get_value": {"target": "search", "as": "typed"}},
        {"get_attribute": {"target": "link", "attribute": "href", "as": "href"}},
    ]
    assert parse_steps(raw, ["q"]) == (
        Goto("https://example.com/{q}"),
        Fill("search", "{q}"),
        Click("submit"),
        WaitFor("results"),
        Hover("menu"),
        Check("agree"),
        Uncheck("newsletter"),
        Select("country", "{q}"),
        Press("search", "Enter"),
        WaitForUrl("**/results?q={q}"),
        Wait(250),
        GetUrl("url"),
        GetText("results", "first"),
        GetAllTexts("results", "all"),
        GetValue("search", "typed"),
        GetAttribute("link", "href", "href"),
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


@pytest.mark.parametrize("kind", ["drag", 1, None])
def test_parse_steps_unknown_kind(kind: object) -> None:
    with pytest.raises(BadActionFile, match="Unknown step"):
        parse_steps([{kind: "x"}], [])


@pytest.mark.parametrize(
    "kind", ["goto", "click", "wait_for", "hover", "check", "uncheck", "wait_for_url"]
)
def test_string_steps_reject_non_string(kind: str) -> None:
    with pytest.raises(BadActionFile, match="needs a string value"):
        parse_steps([{kind: 1}], [])


@pytest.mark.parametrize("kind", ["goto", "wait_for_url"])
def test_url_steps_reject_undeclared_param(kind: str) -> None:
    with pytest.raises(BadActionFile, match="undeclared params"):
        parse_steps([{kind: "https://example.com/{q}"}], [])


@pytest.mark.parametrize("kind", ["fill", "select"])
@pytest.mark.parametrize(
    "value",
    ["search", {"value": "x"}, {"target": 1, "value": "x"}],
)
def test_target_value_steps_reject_bad_target(kind: str, value: object) -> None:
    with pytest.raises(BadActionFile, match="'target' and 'value'"):
        parse_steps([{kind: value}], [])


@pytest.mark.parametrize("kind", ["fill", "select"])
@pytest.mark.parametrize("template", [None, 1])
def test_target_value_steps_reject_non_string_value(
    kind: str, template: object
) -> None:
    with pytest.raises(BadActionFile, match="needs a string value"):
        parse_steps([{kind: {"target": "search", "value": template}}], [])


@pytest.mark.parametrize("kind", ["fill", "select"])
def test_target_value_steps_reject_undeclared_param(kind: str) -> None:
    with pytest.raises(BadActionFile, match="undeclared params"):
        parse_steps([{kind: {"target": "search", "value": "{q}"}}], [])


@pytest.mark.parametrize(
    "value", ["search", {"key": "Enter"}, {"target": 1, "key": "Enter"}]
)
def test_press_rejects_bad_target(value: object) -> None:
    with pytest.raises(BadActionFile, match="'target' and 'key'"):
        parse_steps([{"press": value}], [])


@pytest.mark.parametrize("key", [None, 13])
def test_press_rejects_non_string_key(key: object) -> None:
    with pytest.raises(BadActionFile, match="needs a string value"):
        parse_steps([{"press": {"target": "search", "key": key}}], [])


def test_press_key_is_not_a_template() -> None:
    assert parse_steps([{"press": {"target": "search", "key": "Shift+{"}}], []) == (
        Press("search", "Shift+{"),
    )


@pytest.mark.parametrize("ms", [0, 1.5, 1000])
def test_wait_accepts_non_negative_numbers(ms: float) -> None:
    assert parse_steps([{"wait": ms}], []) == (Wait(ms),)


@pytest.mark.parametrize("ms", [-1, True, "100", None, float("inf"), float("nan")])
def test_wait_rejects_bad_duration(ms: object) -> None:
    with pytest.raises(BadActionFile, match="non-negative number of milliseconds"):
        parse_steps([{"wait": ms}], [])


@pytest.mark.parametrize("kind", ["get_text", "get_all_texts", "get_value"])
@pytest.mark.parametrize("value", ["results", {"as": "x"}, {"target": 1, "as": "x"}])
def test_retrieval_steps_reject_bad_target(kind: str, value: object) -> None:
    with pytest.raises(BadActionFile, match="'target' and 'as'"):
        parse_steps([{kind: value}], [])


@pytest.mark.parametrize("kind", ["get_text", "get_all_texts", "get_value"])
@pytest.mark.parametrize("name", [None, 1, ""])
def test_retrieval_steps_reject_bad_result_name(kind: str, name: object) -> None:
    with pytest.raises(BadActionFile, match="string value|non-empty result name"):
        parse_steps([{kind: {"target": "results", "as": name}}], [])


@pytest.mark.parametrize("name", [None, 1, ""])
def test_get_url_rejects_bad_result_name(name: object) -> None:
    with pytest.raises(BadActionFile, match="string value|non-empty result name"):
        parse_steps([{"get_url": name}], [])


def test_get_attribute_rejects_bad_mapping() -> None:
    with pytest.raises(BadActionFile, match="'target', 'attribute' and 'as'"):
        parse_steps([{"get_attribute": "link"}], [])


@pytest.mark.parametrize("attribute", [None, 1])
def test_get_attribute_rejects_non_string_attribute(attribute: object) -> None:
    with pytest.raises(BadActionFile, match="needs a string value"):
        parse_steps(
            [{"get_attribute": {"target": "a", "attribute": attribute, "as": "x"}}],
            [],
        )


def test_duplicate_result_name_is_rejected() -> None:
    with pytest.raises(BadActionFile, match="result 'x'.*already saves"):
        parse_steps(
            [{"get_url": "x"}, {"get_text": {"target": "results", "as": "x"}}], []
        )


@pytest.mark.parametrize(
    ("step", "name"),
    [
        (Click("a"), None),
        (Goto("u"), None),
        (GetUrl("u"), "u"),
        (GetText("a", "t"), "t"),
        (GetAttribute("a", "h", "href"), "h"),
    ],
)
def test_result_name(step: Step, name: str | None) -> None:
    assert step.result_name == name


@pytest.mark.parametrize(
    ("step", "key"),
    [
        (Goto("u"), None),
        (WaitForUrl("u"), None),
        (Wait(1), None),
        (Click("a"), "a"),
        (Fill("b", "v"), "b"),
        (WaitFor("c"), "c"),
        (Hover("d"), "d"),
        (Check("e"), "e"),
        (Uncheck("f"), "f"),
        (Select("g", "v"), "g"),
        (Press("h", "Enter"), "h"),
        (GetUrl("u"), None),
        (GetText("i", "t"), "i"),
        (GetAttribute("j", "h", "href"), "j"),
    ],
)
def test_selector_key(step: Step, key: str | None) -> None:
    assert step.selector_key == key


def test_duplicate_kind_is_rejected() -> None:
    with pytest.raises(TypeError, match="already defined"):

        class Again(Click, kind="click"):
            pass


def test_base_without_kind_is_not_registered() -> None:
    assert not hasattr(TargetStep, "kind")
    with pytest.raises(BadActionFile, match="Unknown step"):
        parse_steps([{"TargetStep": "x"}], [])


async def test_steps_run_against_page(fake_page: FakePage) -> None:
    ctx = ActionContext(
        page=fake_page,
        sel={"search": "#q", "submit": "button", "results": ".r"},
        params={"q": "cats"},
    )
    for step in (
        Goto("https://example.com/?q={q}"),
        Fill("search", "{q} and dogs"),
        Press("search", "Enter"),
        Click("submit"),
        WaitForUrl("**/?q={q}"),
        WaitFor("results"),
        Hover("results"),
        Select("search", "{q}"),
        Check("submit"),
        Uncheck("submit"),
        Wait(50),
    ):
        await step.run(ctx)

    assert fake_page.calls == [
        ("goto", "https://example.com/?q=cats", "domcontentloaded"),
        ("fill", "#q", "cats and dogs"),
        ("press", "#q", "Enter"),
        ("click", "button"),
        ("wait_for_url", "**/?q=cats", "domcontentloaded"),
        ("wait_for", ".r", "visible"),
        ("hover", ".r"),
        ("select_option", "#q", "cats"),
        ("check", "button"),
        ("uncheck", "button"),
        ("wait_for_timeout", "50"),
    ]


async def test_retrieval_steps_save_results(fake_page: FakePage) -> None:
    ctx = ActionContext(
        page=fake_page,
        sel={"search": "#q", "results": ".r", "link": "a"},
        params={},
    )
    for step in (
        Goto("https://example.com/"),
        GetUrl("url"),
        GetText("results", "first"),
        GetAllTexts("results", "all"),
        GetValue("search", "typed"),
        GetAttribute("link", "href", "href"),
        GetAttribute("link", "missing", "missing"),
    ):
        await step.run(ctx)

    assert ctx.results == {
        "url": "https://example.com/",
        "first": "text of .r",
        "all": ["text 1 of .r", "text 2 of .r"],
        "typed": "value of #q",
        "href": "href of a",
        "missing": None,
    }

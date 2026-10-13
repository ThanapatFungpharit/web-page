from pathlib import Path

import pytest

from tests.conftest import FakePage
from web_page import FilePayload
from web_page.exceptions import BadActionFile
from web_page.steps import (
    Call,
    Check,
    Click,
    Condition,
    Download,
    ElementIs,
    FilesParam,
    Fill,
    GetAllTexts,
    GetAttribute,
    GetText,
    GetUrl,
    GetValue,
    Goto,
    Hover,
    If,
    ParamEquals,
    ParamIsSet,
    Press,
    Select,
    Step,
    TargetStep,
    Uncheck,
    Upload,
    UploadFile,
    UrlIs,
    Wait,
    WaitFor,
    WaitForUrl,
    parse_steps,
    run_steps,
    walk,
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


def test_press_key_is_a_template() -> None:
    raw = [
        {"press": {"target": "search", "key": "{modifier}+A"}},
        {"press": {"target": "search", "key": "Shift+{{"}},
    ]
    assert parse_steps(raw, ["modifier"]) == (
        Press("search", "{modifier}+A"),
        Press("search", "Shift+{{"),
    )
    with pytest.raises(BadActionFile, match="undeclared params"):
        parse_steps([{"press": {"target": "search", "key": "{x}"}}], [])


async def test_press_fills_the_key(fake_page: FakePage) -> None:
    ctx = ActionContext(page=fake_page, sel={"q": "#q"}, params={"m": "Control"})
    await run_steps((Press("q", "{m}+A"), Press("q", "Shift+{{")), ctx)
    assert fake_page.calls == [("press", "#q", "Control+A"), ("press", "#q", "Shift+{")]


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
    ("step", "keys"),
    [
        (Goto("u"), ()),
        (WaitForUrl("u"), ()),
        (Wait(1), ()),
        (Click("a"), ("a",)),
        (Fill("b", "v"), ("b",)),
        (WaitFor("c"), ("c",)),
        (Hover("d"), ("d",)),
        (Check("e"), ("e",)),
        (Uncheck("f"), ("f",)),
        (Select("g", "v"), ("g",)),
        (Press("h", "Enter"), ("h",)),
        (GetUrl("u"), ()),
        (GetText("i", "t"), ("i",)),
        (GetAttribute("j", "h", "href"), ("j",)),
    ],
)
def test_selector_keys(step: Step, keys: tuple[str, ...]) -> None:
    assert step.selector_keys == keys


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


# File steps


def test_parse_file_steps() -> None:
    raw = [
        {"upload": {"target": "avatar", "files": "{dir}/me.png"}},
        {"upload": {"target": "docs", "files": ["a.pdf", "{dir}/b.pdf"]}},
        {"upload": {"target": "docs", "files": []}},
        {"download": {"target": "export", "to": "{dir}/"}},
        {"download": {"target": "export", "to": "out.csv", "as": "report"}},
    ]
    assert parse_steps(raw, ["dir"]) == (
        Upload("avatar", ("{dir}/me.png",)),
        Upload("docs", ("a.pdf", "{dir}/b.pdf")),
        Upload("docs", ()),
        Download("export", "{dir}/"),
        Download("export", "out.csv", "report"),
    )


@pytest.mark.parametrize(
    "value", ["avatar", {"files": "a"}, {"target": 1, "files": "a"}]
)
def test_upload_rejects_bad_target(value: object) -> None:
    with pytest.raises(BadActionFile, match="'files' and one of|needs a string value"):
        parse_steps([{"upload": value}], [])


def test_parse_upload_file_mappings() -> None:
    raw = [
        {"upload": {"target": "a", "files": {"path": "{dir}/x.pdf"}}},
        {"upload": {"target": "a", "files": {"path": "x.pdf", "name": "{n}.pdf"}}},
        {
            "upload": {
                "target": "a",
                "files": [
                    "y.pdf",
                    {"data": "blob", "name": "b.bin", "mime_type": "text/plain"},
                ],
            }
        },
    ]
    assert parse_steps(raw, ["dir", "n", "blob"]) == (
        Upload("a", ("{dir}/x.pdf",)),
        Upload("a", (UploadFile(path="x.pdf", name="{n}.pdf"),)),
        Upload(
            "a",
            ("y.pdf", UploadFile(data="blob", name="b.bin", mime_type="text/plain")),
        ),
    )


@pytest.mark.parametrize(
    "files",
    [
        None,
        1,
        ["a", 1],
        {"a": "b"},
        {},
        {"path": "a", "data": "d", "name": "n"},
        {"path": "a", "extra": "x"},
    ],
)
def test_upload_rejects_bad_files(files: object) -> None:
    with pytest.raises(BadActionFile, match="path or a mapping"):
        parse_steps([{"upload": {"target": "avatar", "files": files}}], ["d"])


@pytest.mark.parametrize(
    ("file", "match"),
    [
        ({"data": "d"}, "needs a 'name'"),
        ({"data": "other", "name": "n"}, "undeclared param 'other'"),
        ({"data": 1, "name": "n"}, "needs a string value"),
        ({"path": "a", "name": "{other}"}, "undeclared params"),
        ({"path": "a", "mime_type": 1}, "needs a string value"),
    ],
)
def test_upload_rejects_bad_file_mapping(file: object, match: str) -> None:
    with pytest.raises(BadActionFile, match=match):
        parse_steps([{"upload": {"target": "avatar", "files": file}}], ["d"])


def test_upload_rejects_undeclared_param() -> None:
    with pytest.raises(BadActionFile, match="undeclared params"):
        parse_steps([{"upload": {"target": "avatar", "files": ["{dir}/a"]}}], [])


@pytest.mark.parametrize("value", ["export", {"to": "a"}, {"target": 1, "to": "a"}])
def test_download_rejects_bad_target(value: object) -> None:
    with pytest.raises(BadActionFile, match="'to' and one of|needs a string value"):
        parse_steps([{"download": value}], [])


@pytest.mark.parametrize("to", [None, 1])
def test_download_rejects_non_string_path(to: object) -> None:
    with pytest.raises(BadActionFile, match="needs a string value"):
        parse_steps([{"download": {"target": "export", "to": to}}], [])


def test_download_rejects_undeclared_param() -> None:
    with pytest.raises(BadActionFile, match="undeclared params"):
        parse_steps([{"download": {"target": "export", "to": "{dir}/"}}], [])


@pytest.mark.parametrize("name", [1, ""])
def test_download_rejects_bad_result_name(name: object) -> None:
    with pytest.raises(BadActionFile, match="string value|non-empty result name"):
        parse_steps([{"download": {"target": "export", "to": "a", "as": name}}], [])


def test_download_result_name_clashes_with_retrieval() -> None:
    with pytest.raises(BadActionFile, match="result 'x'.*already saves"):
        parse_steps(
            [{"get_url": "x"}, {"download": {"target": "e", "to": "a", "as": "x"}}],
            [],
        )


@pytest.mark.parametrize(
    ("step", "keys", "name"),
    [
        (Upload("a", ("f",)), ("a",), None),
        (Upload(None, ("f",), click="b"), ("b",), None),
        (Upload(None, ("f",), steps=(Click("c"),)), (), None),
        (Download("b", "f"), ("b",), None),
        (Download("c", "f", "r"), ("c",), "r"),
        (Download(None, "f", steps=(Click("d"),)), (), None),
    ],
)
def test_file_step_keys(step: Step, keys: tuple[str, ...], name: str | None) -> None:
    assert step.selector_keys == keys
    assert step.result_name == name


async def test_upload_runs_against_page(fake_page: FakePage) -> None:
    ctx = ActionContext(page=fake_page, sel={"docs": "#docs"}, params={"dir": "d"})
    for step in (Upload("docs", ("{dir}/a.pdf", "b.pdf")), Upload("docs", ())):
        await step.run(ctx)
    assert fake_page.calls == [
        ("set_input_files", "#docs", "d/a.pdf", "b.pdf"),
        ("set_input_files", "#docs"),
    ]


async def test_upload_sends_payloads_with_new_names(
    fake_page: FakePage, tmp_path: Path
) -> None:
    original = tmp_path / "scan_0001.pdf"
    original.write_bytes(b"%PDF")
    plain = tmp_path / "notes.txt"
    plain.write_bytes(b"hi")
    ctx = ActionContext(
        page=fake_page,
        sel={"docs": "#docs"},
        params={"dir": str(tmp_path), "who": "ann", "blob": b"\x00\x01", "text": "é"},
    )
    await Upload(
        "docs",
        (
            UploadFile(path="{dir}/scan_0001.pdf", name="{who}.pdf"),
            "{dir}/notes.txt",
            UploadFile(data="blob", name="raw.bin"),
            UploadFile(data="text", name="t", mime_type="text/plain"),
        ),
    ).run(ctx)

    assert fake_page.calls == [
        (
            "set_input_files",
            "#docs",
            "ann.pdf application/pdf b'%PDF'",
            "notes.txt text/plain b'hi'",
            "raw.bin application/octet-stream b'\\x00\\x01'",
            "t text/plain b'\\xc3\\xa9'",
        )
    ]
    # The original file is only read: same name, same contents, no copies.
    assert original.read_bytes() == b"%PDF"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["notes.txt", "scan_0001.pdf"]


def test_upload_templates_and_data_params() -> None:
    step = Upload(
        "a",
        (
            "{dir}/x",
            UploadFile(path="{dir}/y", name="{n}", mime_type="{m}"),
            UploadFile(data="blob", name="b"),
        ),
    )
    assert step.templates == ("{dir}/x", "{dir}/y", "{n}", "{m}", "b")
    assert step.data_params == {"blob"}


async def test_download_clicks_inside_expect_download(
    fake_page: FakePage, tmp_path: Path
) -> None:
    ctx = ActionContext(
        page=fake_page, sel={"export": "#export"}, params={"dir": str(tmp_path)}
    )
    await Download("export", "{dir}/out/report.csv", "report").run(ctx)

    path = str(tmp_path / "out" / "report.csv")
    assert fake_page.calls == [
        ("expect_download",),
        ("click", "#export"),
        ("download started",),
        ("save_as", path),
    ]
    assert ctx.results == {"report": path}


@pytest.mark.parametrize("to", ["{dir}", "{dir}/new/"])
async def test_download_into_directory_uses_suggested_name(
    fake_page: FakePage, tmp_path: Path, to: str
) -> None:
    ctx = ActionContext(
        page=fake_page, sel={"export": "#export"}, params={"dir": str(tmp_path)}
    )
    await Download("export", to, "report").run(ctx)

    saved = Path(fake_page.calls[-1][1])
    assert saved.name == "report.csv"
    assert ctx.results == {"report": str(saved.resolve())}


async def test_download_without_result_saves_nothing(
    fake_page: FakePage, tmp_path: Path
) -> None:
    ctx = ActionContext(page=fake_page, sel={"export": "#export"}, params={})
    await Download("export", str(tmp_path / "a.csv")).run(ctx)
    assert ctx.results == {}


# Step options


def test_parse_options() -> None:
    raw = [
        {"click": {"target": "save", "timeout": 500, "optional": True}},
        {"click": {"target": "save"}},
        {"goto": {"url": "https://example.com", "timeout": 1000}},
        {"get_url": {"as": "url", "optional": False}},
        {"fill": {"target": "q", "value": "x", "timeout": 0}},
    ]
    assert parse_steps(raw, []) == (
        Click("save", timeout=500, optional=True),
        Click("save"),
        Goto("https://example.com", timeout=1000),
        GetUrl("url"),
        Fill("q", "x", timeout=0),
    )


@pytest.mark.parametrize(
    "step",
    [
        {"wait": {"ms": 1, "timeout": 5}},
        {"get_url": {"as": "u", "timeout": 5}},
        {"get_all_texts": {"target": "a", "as": "t", "timeout": 5}},
        {"call": {"hook": "h", "timeout": 5}},
        {"if": {"param": "p", "then": [], "timeout": 5}},
    ],
)
def test_timeout_rejected_where_nothing_waits(step: dict[str, object]) -> None:
    with pytest.raises(BadActionFile, match="does not take a 'timeout'"):
        parse_steps([step], ["p"])


@pytest.mark.parametrize("timeout", [-1, True, "5", None])
def test_bad_timeout(timeout: object) -> None:
    with pytest.raises(BadActionFile, match="non-negative number"):
        parse_steps([{"click": {"target": "a", "timeout": timeout}}], [])


@pytest.mark.parametrize("optional", ["yes", 1, None])
def test_bad_optional(optional: object) -> None:
    with pytest.raises(BadActionFile, match="'optional' to be true or false"):
        parse_steps([{"click": {"target": "a", "optional": optional}}], [])


async def test_timeout_is_passed_to_the_page(fake_page: FakePage) -> None:
    ctx = ActionContext(page=fake_page, sel={"a": "#a"}, params={})
    await run_steps(
        (Goto("u", timeout=1), Click("a", timeout=2), GetText("a", "t", timeout=3)),
        ctx,
    )
    assert fake_page.calls == [
        ("goto", "u", "domcontentloaded", "timeout=1"),
        ("click", "#a", "timeout=2"),
        ("inner_text", "#a", "timeout=3"),
    ]


async def test_optional_step_is_skipped_on_timeout(fake_page: FakePage) -> None:
    fake_page.missing.add("#gone")
    ctx = ActionContext(page=fake_page, sel={"gone": "#gone", "a": "#a"}, params={})
    await run_steps(
        (
            Click("gone", optional=True),
            GetText("gone", "text", optional=True),
            Click("a"),
        ),
        ctx,
    )
    assert fake_page.calls == [("click", "#a")]
    # A skipped retrieval still saves its name.
    assert ctx.results == {"text": None}


async def test_required_step_still_fails_on_timeout(fake_page: FakePage) -> None:
    fake_page.missing.add("#gone")
    ctx = ActionContext(page=fake_page, sel={"gone": "#gone"}, params={})
    with pytest.raises(TimeoutError):
        await run_steps((Click("gone"),), ctx)


async def test_optional_only_catches_timeout_errors(fake_page: FakePage) -> None:
    ctx = ActionContext(page=fake_page, sel={}, params={})
    with pytest.raises(KeyError):
        await run_steps((Click("unknown", optional=True),), ctx)


# Text without icons


def test_parse_exclude() -> None:
    raw = [
        {"get_text": {"target": "a", "as": "t", "exclude": "mat-icon"}},
        {"get_all_texts": {"target": "a", "as": "all", "exclude": ".icon"}},
    ]
    assert parse_steps(raw, []) == (
        GetText("a", "t", "mat-icon"),
        GetAllTexts("a", "all", ".icon"),
    )


def test_exclude_must_be_a_selector_key() -> None:
    with pytest.raises(BadActionFile, match="needs a string value"):
        parse_steps([{"get_text": {"target": "a", "as": "t", "exclude": 1}}], [])


async def test_exclude_reads_text_with_script(fake_page: FakePage) -> None:
    ctx = ActionContext(page=fake_page, sel={"a": "#a", "i": ".icon"}, params={})
    await run_steps(
        (GetText("a", "one", "i", timeout=5), GetAllTexts("a", "all", "i")), ctx
    )
    assert ctx.results == {
        "one": "text of #a without .icon",
        "all": ["text 1 of #a without .icon", "text 2 of #a without .icon"],
    }
    assert fake_page.calls == [("evaluate", "#a", ".icon", "timeout=5")]


def test_get_attribute_keeps_attribute_and_name_apart() -> None:
    raw = [{"get_attribute": {"target": "a", "attribute": "href", "as": "link"}}]
    assert parse_steps(raw, []) == (GetAttribute("a", "link", "href"),)


# Upload through a file chooser, and files chosen at run time


def test_parse_upload_sources_and_files_param() -> None:
    raw = [
        {"upload": {"click": "add", "files": {"param": "paths"}}},
        {
            "upload": {
                "steps": [{"click": "menu"}, {"click": "add"}],
                "files": ["a.txt", {"param": "paths"}],
                "timeout": 10,
            }
        },
    ]
    assert parse_steps(raw, ["paths"]) == (
        Upload(None, (FilesParam("paths"),), click="add"),
        Upload(
            None,
            ("a.txt", FilesParam("paths")),
            steps=(Click("menu"), Click("add")),
            timeout=10,
        ),
    )


@pytest.mark.parametrize(
    "value",
    [
        {"target": "a", "click": "b", "files": []},
        {"click": "b"},
        {"files": []},
    ],
)
def test_upload_needs_exactly_one_source(value: object) -> None:
    with pytest.raises(BadActionFile, match="one of 'target', 'click' or 'steps'"):
        parse_steps([{"upload": value}], [])


def test_files_param_must_be_declared() -> None:
    with pytest.raises(BadActionFile, match="undeclared param 'paths'"):
        parse_steps([{"upload": {"target": "a", "files": {"param": "paths"}}}], [])


def test_upload_list_params() -> None:
    step = Upload("a", ("{dir}/x", FilesParam("paths"), UploadFile(data="d", name="n")))
    assert step.list_params == {"paths"}
    assert step.data_params == {"d"}
    assert step.templates == ("{dir}/x", "n")


async def test_upload_through_file_chooser(fake_page: FakePage) -> None:
    ctx = ActionContext(
        page=fake_page,
        sel={"add": "#add", "menu": "#menu"},
        params={"paths": ["a.txt", "b.txt"]},
    )
    await run_steps(
        (
            Upload(None, (FilesParam("paths"),), click="add", timeout=9),
            Upload(None, ("c.txt",), steps=(Click("menu"), Click("add"))),
        ),
        ctx,
    )
    assert fake_page.calls == [
        ("expect_file_chooser", "timeout=9"),
        ("click", "#add"),
        ("file_chooser started",),
        ("set_files", "a.txt", "b.txt"),
        ("expect_file_chooser",),
        ("click", "#menu"),
        ("click", "#add"),
        ("file_chooser started",),
        ("set_files", "c.txt"),
    ]


async def test_files_param_mixes_paths_and_payloads(
    fake_page: FakePage, tmp_path: Path
) -> None:
    (tmp_path / "a.txt").write_bytes(b"A")
    payload: FilePayload = {"name": "b.bin", "mimeType": "x/y", "buffer": b"B"}
    ctx = ActionContext(
        page=fake_page,
        sel={"docs": "#docs"},
        params={"files": [str(tmp_path / "a.txt"), payload], "one": "c.txt"},
    )
    await run_steps(
        (
            Upload("docs", (FilesParam("files"),)),
            Upload("docs", (FilesParam("one"),)),
        ),
        ctx,
    )
    assert fake_page.calls == [
        ("set_input_files", "#docs", "a.txt text/plain b'A'", "b.bin x/y b'B'"),
        ("set_input_files", "#docs", "c.txt"),
    ]


# Download through steps


def test_parse_download_steps() -> None:
    raw = [
        {
            "download": {
                "steps": [{"click": "menu"}, {"click": "quality"}],
                "to": "out/",
                "as": "file",
                "timeout": 120000,
            }
        }
    ]
    assert parse_steps(raw, []) == (
        Download(
            None,
            "out/",
            "file",
            steps=(Click("menu"), Click("quality")),
            timeout=120000,
        ),
    )


def test_download_needs_exactly_one_source() -> None:
    with pytest.raises(BadActionFile, match="one of 'target' or 'steps'"):
        parse_steps([{"download": {"target": "a", "steps": [], "to": "x"}}], [])


async def test_download_runs_steps_inside_expect_download(
    fake_page: FakePage, tmp_path: Path
) -> None:
    ctx = ActionContext(page=fake_page, sel={"menu": "#menu", "hd": "#hd"}, params={})
    step = Download(
        None,
        str(tmp_path / "a.mp4"),
        steps=(Click("menu"), Click("hd")),
        timeout=120000,
    )
    await run_steps((step,), ctx)
    assert fake_page.calls == [
        ("expect_download", "timeout=120000"),
        ("click", "#menu"),
        ("click", "#hd"),
        ("download started",),
        ("save_as", str(tmp_path / "a.mp4")),
    ]


# if


def test_parse_if_conditions() -> None:
    raw = [
        {"if": {"visible": "dialog", "then": [{"click": "close"}]}},
        {
            "if": {
                "hidden": {"target": "spinner", "timeout": 100},
                "then": [],
                "else": [{"wait": 1}],
            }
        },
        {"if": {"exists": {"target": "row"}, "then": []}},
        {"if": {"param": "q", "then": []}},
        {"if": {"equals": {"param": "q", "value": "{q}p"}, "then": []}},
        {"if": {"url": "**/trash/{q}", "then": [], "optional": True}},
    ]
    assert parse_steps(raw, ["q"]) == (
        If(ElementIs("dialog", "visible"), (Click("close"),)),
        If(ElementIs("spinner", "hidden", 100), (), (Wait(1),)),
        If(ElementIs("row", "attached"), ()),
        If(ParamIsSet("q"), ()),
        If(ParamEquals("q", "{q}p"), ()),
        If(UrlIs("**/trash/{q}"), (), optional=True),
    )


@pytest.mark.parametrize(
    ("value", "match"),
    [
        ({"visible": "a"}, "needs a mapping with 'then'"),
        ({"then": []}, "one condition"),
        ({"visible": "a", "param": "q", "then": []}, "one condition"),
        ({"nope": "a", "then": []}, "one condition"),
        ({"param": "other", "then": []}, "undeclared param 'other'"),
        ({"equals": {"param": "q"}, "then": []}, "'param' and 'value'"),
        ({"equals": {"param": "q", "value": "{x}"}, "then": []}, "undeclared"),
        ({"url": "**/{x}", "then": []}, "undeclared params"),
        ({"url": "**/{q.x}", "then": []}, "not a plain"),
        ({"visible": {"target": "a", "timeout": -1}, "then": []}, "non-negative"),
        ({"param": "q", "then": "click"}, "should be a list"),
        ({"param": "q", "then": [{"nope": 1}]}, "Unknown step"),
    ],
)
def test_if_rejects_bad_value(value: object, match: str) -> None:
    with pytest.raises(BadActionFile, match=match):
        parse_steps([{"if": value}], ["q"])


def test_if_branches_may_share_result_names() -> None:
    raw = [
        {
            "if": {
                "param": "q",
                "then": [{"get_url": "where"}],
                "else": [{"get_text": {"target": "a", "as": "where"}}],
            }
        }
    ]
    parse_steps(raw, ["q"])


def test_nested_result_names_clash_with_outer_ones() -> None:
    raw: list[object] = [
        {"get_url": "where"},
        {"if": {"param": "q", "then": [{"get_url": "where"}]}},
    ]
    with pytest.raises(BadActionFile, match="result 'where'"):
        parse_steps(raw, ["q"])
    raw.reverse()
    with pytest.raises(BadActionFile, match="result 'where'"):
        parse_steps(raw, ["q"])


@pytest.mark.parametrize(
    ("condition", "branch"),
    [
        (ElementIs("shown", "visible"), "then"),
        (ElementIs("gone", "visible"), "else"),
        (ElementIs("gone", "hidden"), "then"),
        (ElementIs("shown", "hidden"), "else"),
        (ElementIs("shown", "attached"), "then"),
        (ElementIs("gone", "attached"), "else"),
        (ElementIs("shown", "visible", 5), "then"),
        (ElementIs("gone", "visible", 5), "else"),
        (ElementIs("gone", "hidden", 5), "then"),
        (ParamIsSet("full"), "then"),
        (ParamIsSet("empty"), "else"),
        (ParamEquals("full", "{full}"), "then"),
        (ParamEquals("full", "other"), "else"),
        (UrlIs("https://example.com/media/42"), "then"),
        (UrlIs("**/media/*"), "then"),
        (UrlIs("**/media/{id}"), "then"),
        (UrlIs("*/media/42"), "else"),
        (UrlIs("**/media"), "else"),
        (UrlIs("**/trash/*"), "else"),
        (UrlIs("https://example.com/media/4?"), "else"),
    ],
)
async def test_if_runs_one_branch(
    fake_page: FakePage, condition: Condition, branch: str
) -> None:
    fake_page.missing.add("#gone")
    fake_page.url = "https://example.com/media/42"
    ctx = ActionContext(
        page=fake_page,
        sel={"shown": "#shown", "gone": "#gone", "a": "#then", "b": "#else"},
        params={"full": "x", "empty": "", "id": "42"},
    )
    await If(condition, (Click("a"),), (Click("b"),)).run(ctx)
    assert fake_page.calls[-1] == ("click", f"#{branch}")


def test_if_keys_and_templates() -> None:
    step = If(ElementIs("a", "visible"), (Click("b"),), (Click("c"),))
    assert step.selector_keys == ("a",)
    assert step.branches == ((Click("b"),), (Click("c"),))
    assert If(ParamEquals("q", "{q}"), ()).templates == ("{q}",)
    assert If(ParamIsSet("q"), ()).selector_keys == ()
    assert If(UrlIs("**/{q}"), ()).templates == ("**/{q}",)


def test_walk_visits_nested_steps() -> None:
    steps = (
        Click("a"),
        If(
            ParamIsSet("q"),
            (Click("inner"),),
            (Download(None, "x", steps=(Click("d"),)),),
        ),
    )
    assert [step.selector_keys for step in walk(steps)] == [
        ("a",),
        (),
        ("inner",),
        (),
        ("d",),
    ]


# call


def test_parse_call() -> None:
    assert parse_steps(
        [{"call": "pick"}, {"call": {"hook": "pick", "optional": True}}], []
    ) == (Call("pick"), Call("pick", optional=True))


@pytest.mark.parametrize("value", ["", 1, None])
def test_call_rejects_bad_name(value: object) -> None:
    with pytest.raises(BadActionFile, match="string value|non-empty hook name"):
        parse_steps([{"call": value}], [])


async def test_call_runs_hook_with_context(fake_page: FakePage) -> None:
    async def pick(ctx: ActionContext) -> None:
        await ctx.page.locator(ctx.sel["a"]).click()
        ctx.results["picked"] = str(ctx.params["model"])

    ctx = ActionContext(
        page=fake_page, sel={"a": "#a"}, params={"model": "m"}, hooks={"pick": pick}
    )
    await run_steps((Call("pick"),), ctx)
    assert fake_page.calls == [("click", "#a")]
    assert ctx.results == {"picked": "m"}
    assert Call("pick").hook_name == "pick"

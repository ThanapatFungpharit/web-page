from pathlib import Path

import pytest

from tests.conftest import FakePage
from web_page.exceptions import BadActionFile
from web_page.steps import (
    Check,
    Click,
    Download,
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
    Upload,
    UploadFile,
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
    with pytest.raises(BadActionFile, match="'target' and 'files'"):
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
    with pytest.raises(BadActionFile, match="'target' and 'to'"):
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
    ("step", "key", "name"),
    [
        (Upload("a", ("f",)), "a", None),
        (Download("b", "f"), "b", None),
        (Download("c", "f", "r"), "c", "r"),
    ],
)
def test_file_step_keys(step: Step, key: str, name: str | None) -> None:
    assert step.selector_key == key
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
        ("downloaded",),
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

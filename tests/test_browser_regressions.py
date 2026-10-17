import asyncio

import pytest
from playwright.async_api import Error, async_playwright

from web_page import ActionContext
from web_page.steps import parse_steps, run_steps
from web_page.utils import SelectorTemplates, fill

pytestmark = pytest.mark.browser


@pytest.fixture
async def browser_page():
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch()
        except Error as e:
            pytest.skip(f"Chromium unavailable: {e.message.splitlines()[0]}")
        try:
            page = await browser.new_page()
            page.set_default_timeout(3000)
            yield page
        finally:
            await browser.close()


async def test_exclude_respects_visibility_and_visible_descendants(
    browser_page,
) -> None:
    await browser_page.set_content(
        '<div id="text">Visible<span style="visibility:hidden">Secret<b style="visibility:visible">Shown</b></span><i>icon</i>End</div>'
    )
    ctx = ActionContext(
        page=browser_page, sel={"text": "#text", "absent": ".absent"}, params={}
    )
    await run_steps(
        parse_steps(
            [
                {"get_text": {"target": "text", "as": "raw"}},
                {"get_text": {"target": "text", "exclude": "absent", "as": "filtered"}},
                {"get_all_texts": {"target": "text", "exclude": "absent", "as": "all"}},
            ],
            [],
        ),
        ctx,
    )
    assert ctx.results == {
        "raw": "VisibleShowniconEnd",
        "filtered": "VisibleShowniconEnd",
        "all": ["VisibleShowniconEnd"],
    }


@pytest.mark.parametrize(
    "value",
    [
        "line1\nline2",
        "line1\rline2",
        "line1\fline2",
        'it\'s "quoted" \\ 1',
        "tab\tvalue",
    ],
)
async def test_css_string_escape_matches_control_characters(
    browser_page, value: str
) -> None:
    await browser_page.set_content('<div id="item"></div>')
    await browser_page.locator("#item").evaluate(
        '(el, value) => el.setAttribute("data-value", value)', value
    )
    selector = fill('[data-value="{value:string}"]', {"value": value})
    assert await browser_page.locator(selector).count() == 1


async def test_role_escape_preserves_role_parser_semantics(browser_page) -> None:
    value = "line1\nline2 ' \" \\ "
    await browser_page.set_content('<button id="item">Click</button>')
    await browser_page.locator("#item").evaluate(
        '(el, value) => el.setAttribute("aria-label", value)', value
    )
    assert (
        await browser_page.locator(
            fill('role=button[name="{value:role}"]', {"value": value})
        ).count()
        == 1
    )


async def test_get_all_texts_can_wait_for_multiple_matches(browser_page) -> None:
    await browser_page.set_content('<div id="list"></div>')
    ctx = ActionContext(page=browser_page, sel={"items": ".item"}, params={})
    await run_steps(
        parse_steps([{"get_all_texts": {"target": "items", "as": "snapshot"}}], []), ctx
    )
    assert ctx.results == {"snapshot": []}
    await browser_page.evaluate(
        'setTimeout(() => document.querySelector("#list").innerHTML = \'<p class="item">A</p><p class="item">B</p>\', 75)'
    )
    await run_steps(
        parse_steps(
            [{"get_all_texts": {"target": "items", "as": "waited", "timeout": 1000}}],
            [],
        ),
        ctx,
    )
    assert ctx.results["waited"] == ["A", "B"]


async def test_get_all_texts_optional_timeout_returns_none(browser_page) -> None:
    await browser_page.set_content("<p>empty</p>")
    ctx = ActionContext(page=browser_page, sel={"items": ".item"}, params={})
    await asyncio.wait_for(
        run_steps(
            parse_steps(
                [
                    {
                        "get_all_texts": {
                            "target": "items",
                            "as": "items",
                            "timeout": 75,
                            "optional": True,
                        }
                    }
                ],
                [],
            ),
            ctx,
        ),
        timeout=1,
    )
    assert ctx.results == {"items": None}


async def test_file_timeout_cancels_missing_trigger_in_chromium(browser_page) -> None:
    await browser_page.set_content("<p>no trigger</p>")
    ctx = ActionContext(page=browser_page, sel={"missing": "#missing"}, params={})
    await asyncio.wait_for(
        run_steps(
            parse_steps(
                [
                    {
                        "download": {
                            "target": "missing",
                            "to": "unused.csv",
                            "as": "saved",
                            "timeout": 100,
                            "optional": True,
                        }
                    }
                ],
                [],
            ),
            ctx,
        ),
        timeout=1,
    )
    assert ctx.results == {"saved": None}


async def test_missing_upload_never_clicks_chooser_trigger(
    browser_page, tmp_path
) -> None:
    await browser_page.set_content(
        '<button id="add" onclick="window.clicks++">Add</button><script>window.clicks=0</script>'
    )
    ctx = ActionContext(page=browser_page, sel={"add": "#add"}, params={})
    for file in (
        str(tmp_path / "missing.txt"),
        {"path": str(tmp_path / "missing.txt"), "name": "renamed.txt"},
    ):
        with pytest.raises(FileNotFoundError):
            await run_steps(
                parse_steps([{"upload": {"click": "add", "files": file}}], []), ctx
            )
    assert await browser_page.evaluate("window.clicks") == 0


@pytest.mark.parametrize("pattern", ["**/{{report,export}}.csv", "**/a/**/report.csv"])
async def test_url_condition_matches_wait_for_url_in_chromium(
    browser_page, pattern: str
) -> None:
    await browser_page.route(
        "https://example.test/**", lambda route: route.fulfill(body="<p>OK</p>")
    )
    await browser_page.goto("https://example.test/a/report.csv")
    ctx = ActionContext(page=browser_page, sel={}, params={})
    await run_steps(
        parse_steps(
            [
                {"wait_for_url": {"url": pattern, "timeout": 500}},
                {
                    "if": {
                        "url": pattern,
                        "then": [{"get_url": "matched"}],
                        "else": [{"fail": "glob disagrees"}],
                    }
                },
            ],
            [],
        ),
        ctx,
    )
    assert ctx.results == {"matched": "https://example.test/a/report.csv"}


async def test_retrieved_values_drive_later_selectors_and_input(browser_page) -> None:
    await browser_page.set_content(
        '<input id="source" value="destination"><input id="destination">'
    )
    results = {}
    ctx = ActionContext(
        page=browser_page,
        sel=SelectorTemplates(
            {"source": "#source", "destination": "#{result.target:css}"}, {}, results
        ),
        params={},
        results=results,
    )
    await run_steps(
        parse_steps(
            [
                {"get_value": {"target": "source", "as": "target"}},
                {"fill": {"target": "destination", "value": "{result.target}"}},
            ],
            [],
        ),
        ctx,
    )
    assert await browser_page.locator("#destination").input_value() == "destination"

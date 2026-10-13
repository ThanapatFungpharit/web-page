from pathlib import Path

import pytest
from playwright.async_api import Error, async_playwright

from tests.conftest import WriteYaml
from web_page import WebPage

pytestmark = pytest.mark.browser

HTML = """\
<!doctype html>
<input id="q">
<button id="go" onclick="
  const r = document.createElement('p');
  r.className = 'result';
  r.textContent = 'You searched: ' + document.getElementById('q').value;
  document.body.append(r);
">Search</button>
"""


async def test_search_action_in_chromium(tmp_path: Path, write_yaml: WriteYaml) -> None:
    page_path = tmp_path / "index.html"
    page_path.write_text(HTML, encoding="utf-8")

    selectors = write_yaml(
        "selectors.yaml",
        """\
        name: demo
        selectors:
          search: "#q"
          submit: "#go"
          result: ".result"
        """,
    )
    action = write_yaml(
        "action.yaml",
        """\
        name: search
        selectors_name: demo
        params: [url, query]
        steps:
          - goto: "{url}"
          - fill: {target: search, value: "{query}"}
          - click: submit
          - wait_for: result
        """,
    )

    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch()
        except Error as e:
            pytest.skip(f"Chromium is not installed: {e.message.splitlines()[0]}")
        try:
            page = await browser.new_page()
            web_page = WebPage(page)
            await web_page.load_selectors(selectors)
            await web_page.load_action(action)
            await web_page.run("search", {"url": page_path.as_uri(), "query": "cats"})
            assert await page.locator(".result").text_content() == "You searched: cats"
        finally:
            await browser.close()

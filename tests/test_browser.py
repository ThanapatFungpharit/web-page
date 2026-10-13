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


FORM_HTML = """\
<!doctype html>
<select id="size"><option>small</option><option>large</option></select>
<label><input id="gift" type="checkbox"> Gift</label>
<label><input id="news" type="checkbox" checked> Newsletter</label>
<span id="help" onmouseover="this.textContent = 'hovered'">?</span>
<input id="note" onkeydown="
  if (event.key !== 'Enter') return;
  location.hash = 'sent';
  const r = document.createElement('p');
  r.className = 'summary';
  r.textContent = [
    document.getElementById('size').value,
    document.getElementById('gift').checked,
    document.getElementById('news').checked,
    document.getElementById('help').textContent,
    this.value,
  ].join(' ');
  document.body.append(r);
">
"""


async def test_form_action_in_chromium(tmp_path: Path, write_yaml: WriteYaml) -> None:
    page_path = tmp_path / "form.html"
    page_path.write_text(FORM_HTML, encoding="utf-8")

    selectors = write_yaml(
        "selectors.yaml",
        """\
        name: form
        selectors:
          size: "#size"
          gift: "#gift"
          news: "#news"
          help: "#help"
          note: "#note"
          summary: ".summary"
          option: "#size option"
        """,
    )
    action = write_yaml(
        "action.yaml",
        """\
        name: order
        selectors_name: form
        params: [url, size]
        steps:
          - goto: "{url}"
          - select: {target: size, value: "{size}"}
          - check: gift
          - uncheck: news
          - hover: help
          - fill: {target: note, value: "thanks"}
          - press: {target: note, key: Enter}
          - wait_for_url: "**#sent"
          - wait: 10
          - wait_for: summary
          - get_text: {target: summary, as: summary}
          - get_value: {target: note, as: note}
          - get_attribute: {target: news, attribute: type, as: news_type}
          - get_all_texts: {target: option, as: options}
          - get_url: url
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
            url = page_path.as_uri()
            assert await web_page.run("order", {"url": url, "size": "large"}) == {
                "summary": "large true false hovered thanks",
                "note": "thanks",
                "news_type": "checkbox",
                "options": ["small", "large"],
                "url": f"{url}#sent",
            }
        finally:
            await browser.close()

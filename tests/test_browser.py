from pathlib import Path

import pytest
from playwright.async_api import Error, async_playwright

from tests.conftest import WriteYaml
from web_page import ActionContext, WebPage

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


FILES_HTML = """\
<!doctype html>
<input id="docs" type="file" multiple onchange="
  const r = document.createElement('p');
  r.className = 'names';
  r.textContent = [...this.files].map(f => f.name).join(',');
  document.body.append(r);
">
<a id="export" href="data:text/csv,a%2Cb" download="report.csv">Export</a>
"""


async def test_file_action_in_chromium(tmp_path: Path, write_yaml: WriteYaml) -> None:
    page_path = tmp_path / "files.html"
    page_path.write_text(FILES_HTML, encoding="utf-8")
    for name in ("a.txt", "b.txt"):
        (tmp_path / name).write_text(name, encoding="utf-8")

    selectors = write_yaml(
        "selectors.yaml",
        """\
        name: files
        selectors:
          docs: "#docs"
          names: ".names"
          export: "#export"
        """,
    )
    action = write_yaml(
        "action.yaml",
        """\
        name: files
        selectors_name: files
        params: [url, dir]
        steps:
          - goto: "{url}"
          - upload: {target: docs, files: ["{dir}/a.txt", "{dir}/b.txt"]}
          - get_text: {target: names, as: names}
          - download: {target: export, to: "{dir}/out/", as: report}
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
            results = await web_page.run(
                "files", {"url": page_path.as_uri(), "dir": str(tmp_path)}
            )
        finally:
            await browser.close()

    report = tmp_path / "out" / "report.csv"
    assert results == {"names": "a.txt,b.txt", "report": str(report.resolve())}
    assert report.read_text(encoding="utf-8") == "a,b"


UPLOAD_HTML = """\
<!doctype html>
<input id="docs" type="file" multiple onchange="
  Promise.all([...this.files].map(async f => f.name + '=' + await f.text()))
    .then(parts => {
      const r = document.createElement('p');
      r.className = 'names';
      r.textContent = parts.join(',');
      document.body.append(r);
    });
">
"""


async def test_renamed_and_data_upload_in_chromium(
    tmp_path: Path, write_yaml: WriteYaml
) -> None:
    page_path = tmp_path / "upload.html"
    page_path.write_text(UPLOAD_HTML, encoding="utf-8")
    original = tmp_path / "scan_0001.txt"
    original.write_text("scan", encoding="utf-8")

    selectors = write_yaml(
        "selectors.yaml",
        """\
        name: upload
        selectors:
          docs: "#docs"
          names: ".names"
        """,
    )
    action = write_yaml(
        "action.yaml",
        """\
        name: upload
        selectors_name: upload
        params: [url, dir, who, blob]
        steps:
          - goto: "{url}"
          - upload:
              target: docs
              files:
                - {path: "{dir}/scan_0001.txt", name: "{who}.txt"}
                - {data: blob, name: generated.csv}
          - get_text: {target: names, as: names}
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
            results = await web_page.run(
                "upload",
                {
                    "url": page_path.as_uri(),
                    "dir": str(tmp_path),
                    "who": "ann",
                    "blob": b"a;b",
                },
            )
        finally:
            await browser.close()

    assert results == {"names": "ann.txt=scan,generated.csv=a;b"}
    assert original.read_text(encoding="utf-8") == "scan"
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "action.yaml",
        "scan_0001.txt",
        "selectors.yaml",
        "upload.html",
    ]


MEDIA_HTML = """\
<!doctype html>
<style>.material-icons { font-family: monospace; }</style>
<button id="add">Add media</button>
<input id="picker" type="file" multiple hidden onchange="
  const r = document.createElement('p');
  r.className = 'names';
  r.textContent = [...this.files].map(f => f.name).join(',');
  document.body.append(r);
">
<script>
  document.getElementById('add').onclick =
    () => document.getElementById('picker').click();
</script>
<div class="card" data-id="7">
  <button class="more" onclick="this.nextElementSibling.hidden = false">
    <i class="material-icons">more_vert</i>
  </button>
  <div class="menu" hidden>
    <a role="menuitem" href="data:text/plain,low" download="low.txt">720p</a>
    <a role="menuitem" href="data:text/plain,high" download="high.txt">1080p</a>
  </div>
</div>
<div class="setting"><i class="material-icons">crop_16_9</i> Landscape <b>16:9</b></div>
<div class="setting"><i class="material-icons">crop_portrait</i>Portrait<br>9:16</div>
"""


async def test_dynamic_media_action_in_chromium(
    tmp_path: Path, write_yaml: WriteYaml
) -> None:
    page_path = tmp_path / "media.html"
    page_path.write_text(MEDIA_HTML, encoding="utf-8")
    for name in ("a.txt", "b.txt"):
        (tmp_path / name).write_text(name, encoding="utf-8")

    selectors = write_yaml(
        "selectors.yaml",
        """\
        name: media
        selectors:
          add: "#add"
          names: ".names"
          menu: ".card[data-id='{media_id}'] .more"
          quality: ".card[data-id='{media_id}'] >> role=menuitem[name='{quality}']"
          settings: ".setting"
          dialog: "#dialog"
          icons: ".material-icons"
        """,
    )
    action = write_yaml(
        "action.yaml",
        """\
        name: media
        selectors_name: media
        params: [url, paths, media_id, quality, dir]
        steps:
          - goto: "{url}"
          - click: {target: dialog, timeout: 200, optional: true}
          - if:
              visible: dialog
              then: [{click: dialog}]
          - upload: {click: add, files: {param: paths}, timeout: 5000}
          - get_text: {target: names, as: names}
          - download:
              steps:
                - click: menu
                - click: quality
              to: "{dir}/out/"
              timeout: 120000
              as: video
          - get_all_texts: {target: settings, exclude: icons, as: settings}
          - get_all_texts: {target: settings, as: raw}
          - call: count_cards
        """,
    )

    async def count_cards(ctx: ActionContext) -> None:
        ctx.results["cards"] = str(await ctx.page.locator(".card").count())

    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch()
        except Error as e:
            pytest.skip(f"Chromium is not installed: {e.message.splitlines()[0]}")
        try:
            page = await browser.new_page()
            web_page = WebPage(page)
            web_page.register("count_cards", count_cards)
            await web_page.load_selectors(selectors)
            await web_page.load_action(action)
            results = await web_page.run(
                "media",
                {
                    "url": page_path.as_uri(),
                    "paths": [str(tmp_path / "a.txt"), str(tmp_path / "b.txt")],
                    "media_id": "7",
                    "quality": "1080p",
                    "dir": str(tmp_path),
                },
            )
        finally:
            await browser.close()

    video = tmp_path / "out" / "high.txt"
    raw = results.pop("raw")
    assert results == {
        "names": "a.txt,b.txt",
        "video": str(video.resolve()),
        "settings": ["Landscape 16:9", "Portrait 9:16"],
        "cards": "1",
    }
    assert video.read_text(encoding="utf-8") == "high"
    # Without `exclude`, the icon names are part of the text.
    assert isinstance(raw, list)
    assert "crop_16_9" in raw[0]

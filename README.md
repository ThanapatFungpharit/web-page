# web-page

Script a [Playwright](https://playwright.dev/python/) page, or any drop-in replacement
for one, with YAML instead of code.

```yaml
steps:
  - goto: "https://example.com/search"
  - fill: {target: search, value: "{query}"}
  - press: {target: search, key: Enter}
  - wait_for: results
  - get_all_texts: {target: result_titles, as: titles}
```

```python
results = await page.run("search", {"query": "playwright"})
results["titles"]  # ["First result", "Second result", ...]
```

There are two kinds of YAML file:

- A **selectors manifest** says *where* things are: it gives short names to
  selectors, such as `search: "input[name=q]"`.
- An **action manifest** says *what to do*: an ordered list of steps that use those
  names, with `{placeholders}` for the values you pass in.

When markup changes, you update the selectors and leave the actions alone. Every
manifest is fully validated when it is loaded, and every `run` call is checked before
the first step touches the page, so mistakes show up as clear errors instead of a
half-finished browser session.

## Contents

- [Requirements](#requirements)
- [Installation](#installation)
- [Quick start](#quick-start)
- [The `WebPage` API](#the-webpage-api)
- [Using another browser library](#using-another-browser-library)
- [Manifests](#manifests)
- [Steps](#steps)
- [Retrieving data](#retrieving-data)
- [Validation and errors](#validation-and-errors)
- [Adding a step](#adding-a-step)
- [Development](#development)
- [Project layout](#project-layout)

## Requirements

- Python 3.14+
- [uv](https://docs.astral.sh/uv/)

- An async page object: Playwright's, or one with the same interface (see
  [Using another browser library](#using-another-browser-library))

## Installation

`web-page` does not depend on any browser library itself. To install it with
Playwright:

```bash
uv add "web-page[playwright]"
```

```bash
uv run playwright install chromium
```

If you use a drop-in replacement for Playwright, install plain `web-page` and that
library instead.

## Quick start

**1. Name the elements you need** in `selectors/search_page.yaml`:

```yaml
name: search_page
selectors:
  search: "input[name=q]"
  results: "#results"
  result_titles: "#results h3"
```

**2. Describe the action** in `actions/search.yaml`:

```yaml
name: search
selectors_name: search_page
params: [query]
steps:
  - goto: "https://example.com/search"
  - fill: {target: search, value: "{query}"}
  - press: {target: search, key: Enter}
  - wait_for: results
  - get_all_texts: {target: result_titles, as: titles}
```

**3. Load both and run the action** on a Playwright page:

```python
import asyncio

from playwright.async_api import async_playwright

from web_page import WebPage


async def main() -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = WebPage(await browser.new_page())

        await page.load_selectors("selectors/search_page.yaml")
        await page.load_action("actions/search.yaml")

        results = await page.run("search", {"query": "playwright"})
        print(results["titles"])

        await browser.close()


asyncio.run(main())
```

## The `WebPage` API

`WebPage` wraps a page object that you already have. It does not launch or close the
browser; that stays your job.

| Method                                | Description                                                                  |
| ------------------------------------- | ---------------------------------------------------------------------------- |
| `WebPage(page)`                       | Wrap an async page that satisfies `web_page.Page`.                           |
| `await load_selectors(path)`          | Read and validate a selectors manifest, and register it under its `name`.    |
| `await load_action(path)`             | Read and validate an action manifest, and register it under its `name`.      |
| `await run(action_name, params=None)` | Run a loaded action. Returns `dict[str, str \| list[str] \| None]`.           |

About `run`:

- `params` maps each name in the action's `params` to a string (or `bytes`, for an
  [`upload` `data`](#file-steps) param). You must give exactly the
  declared names: a missing or extra name raises `BadActionParams`. For an action with
  no params, leave the argument out.
- The return value holds whatever the action's [retrieval steps](#retrieving-data)
  saved. It is `{}` if the action has none.
- Actions and selectors can be loaded in any order. They are only matched up when
  `run` is called.
- Calls to `run` on the same `WebPage` take turns under a lock, so their steps never
  interleave.

## Using another browser library

`WebPage` accepts any object that matches the `web_page.Page` protocol. That includes
Playwright's async `Page`, and forks or wrappers with the same API such as
[Patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python). The protocol
is structural, so you don't subclass anything. Your type checker will tell you if a
page object doesn't fit.

The protocols in [`protocols.py`](src/web_page/protocols.py) list only what the
built-in steps call:

| Object                  | Members used                                                                   |
| ----------------------- | ------------------------------------------------------------------------------ |
| `Page`                  | `url`, `goto`, `wait_for_url`, `wait_for_timeout`, `locator`, `expect_download` |
| `Locator` (`locator()`) | `click`, `hover`, `check`, `uncheck`, `wait_for`, `fill`, `select_option`, `press`, `inner_text`, `all_inner_texts`, `input_value`, `get_attribute`, `set_input_files` (paths, or `FilePayload` dicts of `name`, `mimeType` and `buffer`) |
| `Download`              | `suggested_filename`, `save_as`                                                |

The steps call these with Playwright's argument names and semantics: for example,
`goto(url, wait_until="domcontentloaded")` and `wait_for(state="visible")`. The
`download` step uses `expect_download()` as Playwright does:
`async with page.expect_download() as info`, then `await info.value`. The steps also
expect a locator to be strict, as Playwright's are. Selector strings are passed through
as written, so selector syntax is whatever your library supports.

## Manifests

A manifest is a YAML file with a `.yaml` or `.yml` suffix whose top level is a mapping.

### Selectors manifest

| Key         | Type             | Description                                          |
| ----------- | ---------------- | ---------------------------------------------------- |
| `name`      | `str`            | Unique name that actions refer to.                   |
| `selectors` | `dict[str, str]` | Maps a selector key to a selector string.            |

The values can be any selector your page library accepts. For Playwright, that means
CSS, `text=…`, `xpath=…`, `role=button[name="Save"]` and so on.

### Action manifest

| Key              | Type               | Required | Description                                        |
| ---------------- | ------------------ | -------- | -------------------------------------------------- |
| `name`           | `str`              | yes      | Unique name passed to `WebPage.run`.               |
| `selectors_name` | `str \| list[str]` | yes      | Selectors manifest(s) the steps look keys up in.   |
| `params`         | `str \| list[str]` | no       | Param names the caller must supply. Default: none. |
| `steps`          | `list`             | yes      | Ordered list of single-key step mappings.          |

If `selectors_name` lists several manifests, they are merged in order, and when two of
them define the same key, **the later one wins**. That makes it easy to share a common
manifest and override a few keys for one page:

```yaml
selectors_name: [site_header, checkout_page]
```

### Templates

`goto`, `wait_for_url`, `fill`, `select`, and the paths in `upload` and `download`
take **templates**. These use `str.format`
syntax, so `{query}` is replaced with the `query` param. Each placeholder must be
declared in `params`, which is checked when the action is loaded. To get a literal
brace, write `{{` or `}}`.

## Steps

Each step is a mapping with a single key: the step name, then its value.

### Page steps

These act on the page itself.

| Step           | Value                         | Effect                                                         |
| -------------- | ----------------------------- | -------------------------------------------------------------- |
| `goto`         | URL template                  | `page.goto(url, wait_until="domcontentloaded")`                |
| `wait_for_url` | URL template (globs allowed)  | `page.wait_for_url(url, wait_until="domcontentloaded")`        |
| `wait`         | milliseconds (number ≥ 0)     | `page.wait_for_timeout(ms)`. Use `wait_for` instead when you can. |

### Element steps

These act on the element that a selector key points to.

| Step       | Value                                | Effect                                             |
| ---------- | ------------------------------------ | -------------------------------------------------- |
| `click`    | selector key                         | Clicks the element.                                |
| `hover`    | selector key                         | Hovers over the element.                           |
| `check`    | selector key                         | Checks a checkbox or radio button.                 |
| `uncheck`  | selector key                         | Unchecks a checkbox.                               |
| `wait_for` | selector key                         | Waits until the element is visible.                |
| `fill`     | `{target: <key>, value: <template>}` | Fills the element with the formatted value.        |
| `select`   | `{target: <key>, value: <template>}` | Selects the `<option>` with that value or label.   |
| `press`    | `{target: <key>, key: <key name>}`   | Presses a key or chord, e.g. `Enter`, `Control+A`. |

Element steps use locators (see
[Playwright's](https://playwright.dev/python/docs/locators)), so they wait for the element to be actionable and are **strict**. If a selector matches
more than one element, the step fails rather than picking one. `press` key names are
not templates and are used exactly as written, so `Shift+{` works.

```yaml
steps:
  - select: {target: size, value: "{size}"}
  - check: gift
  - fill: {target: note, value: "{note}"}
  - press: {target: note, key: Enter}
  - wait_for_url: "**/thanks"
```

### File steps

These move files between the page and the local file system.

| Step       | Value                                                     | Effect                                                            |
| ---------- | --------------------------------------------------------- | ----------------------------------------------------------------- |
| `upload`   | `{target: <key>, files: <file> \| [<file>, ...]}`         | Sets the files of an `<input type=file>`. `[]` clears it.         |
| `download` | `{target: <key>, to: <path>, as: <name>}` (`as` optional) | Clicks the element, waits for the download it starts, and saves it. |

Paths are templates. A relative path is resolved against the current working directory.

Each `upload` file is a path, or a mapping that sets what the page receives:

| Key         | Value                                                                   |
| ----------- | ----------------------------------------------------------------------- |
| `path`      | Template: a local file to read.                                         |
| `data`      | Param name: the file's contents, as `bytes` or a `str` (sent as UTF-8). |
| `name`      | Template: the filename the page sees. Required with `data`.             |
| `mime_type` | Template: the file's type. Default: guessed from `name`.                |

Give exactly one of `path` and `data`. With `name` or `mime_type`, the file is read into
memory and handed to the page as a payload under the new name, so the file on disk is
never renamed, copied or changed, and no temporary file is written. With `data`, there
is no file at all: pass the contents from Python as the param's value. A `data` param
is the only kind that may be `bytes`, and only if no template uses it; otherwise `run`
raises `BytesParamNotAllowed` before the first step.

```yaml
params: [dir, user, report]
steps:
  - upload:
      target: attachments
      files:
        - "{dir}/invoice.pdf"                                   # sent as invoice.pdf
        - {path: "{dir}/scan_0001.pdf", name: "{user}-id.pdf"}  # renamed on upload
        - {data: report, name: report.csv}                      # no file on disk
```

```python
await page.run("attach", {"dir": "/tmp/work", "user": "ann", "report": csv_bytes})
```

Playwright can't mix paths and payloads in one call, so when an `upload` has any
mapping with `data`, `name` or `mime_type`, its plain paths are read into payloads too,
keeping their own names.

For `download`, `to` is either a file path or a directory. If it names an existing
directory, or ends in `/`, the file is saved there under the name the browser suggests
(from the link's `download` attribute or the `Content-Disposition` header). Missing
parent directories are created. With `as`, the absolute path of the saved file is
returned among the [results](#retrieving-data).

```yaml
params: [dir]
steps:
  - upload: {target: attachments, files: ["{dir}/invoice.pdf", "{dir}/receipt.pdf"]}
  - click: submit
  - download: {target: export_csv, to: "{dir}/exports/", as: export}
```

```python
results = await page.run("export", {"dir": "/tmp/work"})
results["export"]  # "/tmp/work/exports/report.csv"
```

## Retrieving data

Retrieval steps save a value under the name given in `as`. `WebPage.run` returns all
the saved values together in a dict:

| Step            | Value                                            | Saves                                                      |
| --------------- | ------------------------------------------------ | ---------------------------------------------------------- |
| `get_text`      | `{target: <key>, as: <name>}`                    | The element's visible text (`str`).                        |
| `get_all_texts` | `{target: <key>, as: <name>}`                    | Visible text of every matching element (`list[str]`).      |
| `get_value`     | `{target: <key>, as: <name>}`                    | The current value of an input, textarea or select (`str`). |
| `get_attribute` | `{target: <key>, attribute: <attr>, as: <name>}` | The attribute's value, or `None` if it is absent.          |
| `get_url`       | result name                                      | The page's current URL (`str`).                            |

```yaml
name: read_results
selectors_name: search_page
steps:
  - wait_for: results
  - get_text: {target: first_title, as: title}
  - get_all_texts: {target: result_titles, as: titles}
  - get_attribute: {target: first_link, attribute: href, as: link}
  - get_url: url
```

```python
results = await page.run("read_results")
# {"title": "...", "titles": [...], "link": "/...", "url": "https://..."}
```

`get_text`, `get_value` and `get_attribute` are strict, like `click`: the selector must
match exactly one element. Use `get_all_texts` for lists. A
[`download`](#file-steps) step with `as` also saves a result: the saved file's path. No
two steps in an action may save under the same name; this is checked when the action is
loaded.

## Validation and errors

Problems are caught as early as possible:

| When        | What is checked                                                                                                  |
| ----------- | ---------------------------------------------------------------------------------------------------------------- |
| **On load** | File suffix and YAML syntax, manifest shape and types, unknown steps, malformed templates, undeclared params, duplicate result names, and names already loaded. |
| **On run**  | That the action is loaded, that params match exactly, that only `upload` data params are `bytes`, that every selectors manifest is loaded, and that every selector key exists. All of this happens **before the first step runs**. |

Errors raised by the page library while a step runs, such as a timeout, are passed through
unchanged.

Every error raised by this package derives from `web_page.exceptions.WebPageError`:

```text
WebPageError
├── BadManifest
│   ├── BadActionFile
│   │   └── BadActionName
│   └── BadSelectorsFile
│       └── BadSelectorsName
├── AlreadyLoaded
│   ├── ActionAlreadyLoaded
│   └── SelectorsAlreadyLoaded
├── NotLoaded
│   ├── ActionNotLoaded
│   └── SelectorsNotLoaded
├── BadActionParams
├── BytesParamNotAllowed
└── UnknownSelectorKey
```

Run-time errors keep their details as attributes, so you can handle them without
parsing the message:

```python
from web_page.exceptions import BadActionParams, UnknownSelectorKey

try:
    await page.run("search", {})
except BadActionParams as e:
    print(e.missing)  # ("query",)
except UnknownSelectorKey as e:
    print(e.unknown, e.selectors_names)
```

## Adding a step

To add a step, subclass `Step` and give it a `kind`; defining the class registers it.
Most steps act on one element, so they can extend one of these bases:

| Base               | Manifest value                | You get                                 |
| ------------------ | ----------------------------- | --------------------------------------- |
| `TargetStep`       | selector key                  | `self.target`                           |
| `TargetValueStep`  | `{target, value}` (template)  | `self.target`, `self.value`             |
| `TargetResultStep` | `{target, as}`                | `self.target`, `self.result`            |

These bases already handle parsing, validation, `selector_key` and `result_name`, so
only `run` is left to write:

```python
from dataclasses import dataclass

from web_page.steps import TargetStep
from web_page.types import ActionContext


@dataclass(frozen=True)
class DoubleClick(TargetStep, kind="dblclick"):
    async def run(self, ctx: ActionContext) -> None:
        await ctx.page.locator(ctx.sel[self.target]).dblclick()
```

Inside `run`, `ctx.page` is the page object, `ctx.sel` maps selector keys to
selectors, `ctx.params` holds the param values, and `ctx.results` collects saved
values.

`ctx.page` is typed as the `web_page.Page` protocol, which only lists the methods the
built-in steps use. If your step calls something else, such as `dblclick` above, add
that method to the protocols in [`protocols.py`](src/web_page/protocols.py). That keeps
the type checker happy and documents what a page library must provide.

For a step whose value has a different shape, subclass `Step` directly and implement
`parse`, `selector_key` and `run`. See `Goto` or `Wait` in
[`steps.py`](src/web_page/steps.py) for examples. A step that saves a result also
overrides `result_name` (see `GetUrl`). A class declared without a `kind`, like
`TargetStep`, is a shared base and is not registered as a step. Registering the same
`kind` twice raises `TypeError`.

## Development

Install the dev dependencies. These include Playwright, which the browser tests need:

```bash
uv sync
```

Run the tests:

```bash
uv run pytest
```

The unit tests use a fake page and need no browser. `tests/test_browser.py`, marked
`browser`, drives real headless Chromium and is skipped if Chromium is not installed.
To run only the fast tests:

```bash
uv run pytest -m "not browser"
```

To run formatting, linting, type checking and the tests together:

```bash
uv run uvs check
```

## Project layout

```text
src/web_page/
├── web_page.py    # WebPage: load manifests and run actions
├── steps.py       # Step base classes, built-in steps, parse_steps
├── protocols.py   # Page and Locator protocols the steps drive
├── manifest.py    # Reading and validating YAML manifests
├── exceptions.py  # Error hierarchy
├── types.py       # Type aliases, Action, ActionContext
├── utils.py       # Shape checks and template placeholder validation
└── constants.py
tests/
├── conftest.py    # FakePage and shared fixtures
├── test_utils.py
├── test_manifest.py
├── test_steps.py
├── test_web_page.py
└── test_browser.py
```

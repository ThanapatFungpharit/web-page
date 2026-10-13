# web-page

Drive a [Playwright](https://playwright.dev/python/) page with actions written in YAML.

You describe *where* things are on a page in a **selectors** manifest and *what to do*
in an **action** manifest. `WebPage` loads both, validates everything up front, and runs
the action's steps against a live `Page`.

## Requirements

- Python 3.14+
- [uv](https://docs.astral.sh/uv/)

## Installation

```bash
uv sync
```

```bash
uv run playwright install chromium
```

## Quick start

`selectors/search_page.yaml`:

```yaml
name: search_page
selectors:
  search: "input[name=q]"
  submit: "button[type=submit]"
  results: "#results"
```

`actions/search.yaml`:

```yaml
name: search
selectors_name: search_page
params: [query]
steps:
  - goto: "https://example.com/search?q={query}"
  - fill:
      target: search
      value: "{query}"
  - click: submit
  - wait_for: results
```

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
        await page.run("search", {"query": "playwright"})

        await browser.close()


asyncio.run(main())
```

## Manifests

Manifests are YAML files with a `.yaml` or `.yml` suffix whose top level is a mapping.

### Selectors manifest

| Key         | Type             | Description                                          |
| ----------- | ---------------- | ---------------------------------------------------- |
| `name`      | `str`            | Unique name that actions refer to.                   |
| `selectors` | `dict[str, str]` | Maps a selector key to a Playwright selector string. |

### Action manifest

| Key              | Type               | Required | Description                                         |
| ---------------- | ------------------ | -------- | --------------------------------------------------- |
| `name`           | `str`              | yes      | Unique name passed to `WebPage.run`.                |
| `selectors_name` | `str \| list[str]` | yes      | Selectors manifest(s) the steps look keys up in.    |
| `params`         | `str \| list[str]` | no       | Param names the caller must supply. Default: none.  |
| `steps`          | `list`             | yes      | Ordered list of single-key step mappings.           |

When `selectors_name` lists several manifests, they are merged in order and **later
ones override earlier ones** on key clashes.

### Steps

| Step       | Value                               | Effect                                                |
| ---------- | ----------------------------------- | ----------------------------------------------------- |
| `goto`     | URL template                        | `page.goto(url, wait_until="domcontentloaded")`       |
| `click`    | selector key                        | Clicks the element.                                   |
| `fill`     | `{target: <key>, value: <template>}` | Fills the element with the formatted value.          |
| `wait_for` | selector key                        | Waits until the element is visible.                   |

Templates use `str.format` syntax (`{query}`), and every placeholder must be declared in
`params`; this is checked when the action is loaded.

## Validation and errors

Problems are caught as early as possible:

- **On load**, manifests are checked for shape, types, unknown steps, malformed
  templates and undeclared params. Loading the same `name` twice is an error.
- **On run**, params, selector manifests and selector keys are all checked **before the
  first step runs**, so a bad call never leaves the page half-driven.
- Concurrent `run` calls on the same `WebPage` are serialized with a lock, so their
  steps never interleave.

Every error derives from `web_page.exceptions.WebPageError`:

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
└── UnknownSelectorKey
```

## Adding a step

Subclass `Step` with a `kind`; defining the class registers it:

```python
from dataclasses import dataclass
from typing import Self

from web_page.steps import Step, _selector_key
from web_page.types import ActionContext, ParamNames, RawYaml, SelectorKey


@dataclass(frozen=True)
class Hover(Step, kind="hover"):
    target: SelectorKey

    @classmethod
    def parse(cls, value: RawYaml, params: ParamNames) -> Self:
        return cls(_selector_key(value, cls.kind))

    @property
    def selector_key(self) -> SelectorKey:
        return self.target

    async def run(self, ctx: ActionContext) -> None:
        await ctx.page.locator(ctx.sel[self.target]).hover()
```

## Development

```bash
uv run pytest
```

Unit tests run against a fake page and need no browser. `tests/test_browser.py` (marked
`browser`) drives real headless Chromium and is skipped if it is not installed. To run
only the fast tests:

```bash
uv run pytest -m "not browser"
```

Formatting, linting, type checking and tests together:

```bash
uv run uvs check
```

## Project layout

```text
src/web_page/
├── web_page.py    # WebPage: load manifests and run actions
├── steps.py       # Step base class, built-in steps, parse_steps
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

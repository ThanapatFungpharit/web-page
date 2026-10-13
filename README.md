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
- [Conditions and hooks](#conditions-and-hooks)
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
| `WebPage(page, timeout_errors=None)`  | Wrap an async page that satisfies `web_page.Page`.                           |
| `await load_selectors(path)`          | Read and validate a selectors manifest, and register it under its `name`.    |
| `await load_action(path)`             | Read and validate an action manifest, and register it under its `name`.      |
| `register(name, hook)`                | Make an async function runnable from manifests as `- call: <name>`. See [hooks](#hooks). |
| `await run(action_name, params=None)` | Run a loaded action. Returns `dict[str, str \| list[str] \| None]`.           |

`timeout_errors` lists the exceptions that count as a timeout for
[`optional`](#step-options) steps and waited-for [conditions](#if). The default,
Python's `TimeoutError` plus Playwright's when Playwright is installed, suits
Playwright. Set it if your page library raises something else.

About `run`:

- `params` maps each name in the action's `params` to a string. There are two
  exceptions: `bytes` for an [`upload` `data`](#file-steps) param, and a list of files
  for an [`upload` `{param: …}`](#files-chosen-at-run-time) param. You must give exactly the
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
| `Page`                  | `url`, `keyboard` (its `press`), `goto`, `wait_for_url`, `wait_for_timeout`, `locator`, `expect_download`, `expect_file_chooser` |
| `Locator` (`locator()`) | `click`, `hover`, `check`, `uncheck`, `wait_for`, `fill`, `select_option`, `press`, `inner_text`, `all_inner_texts`, `input_value`, `get_attribute`, `set_input_files` (paths, or `FilePayload` dicts of `name`, `mimeType` and `buffer`), `is_visible`, `count`, `evaluate`, `evaluate_all` |
| `Download`              | `suggested_filename`, `save_as`                                                |
| `FileChooser`           | `set_files`                                                                    |

The steps call these with Playwright's argument names and semantics: for example,
`goto(url, wait_until="domcontentloaded")` and `wait_for(state="visible")`. They pass
`timeout=` only when a manifest sets one, so a library without that argument still
works for actions that don't use it. The same goes for `click`'s `button=`. The `download` and `upload` steps use
`expect_download()` and `expect_file_chooser()` as Playwright does:
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

Selectors are [templates](#templates) too, so they can point at an element that is
only known at run time, such as one media item or one menu entry:

```yaml
name: library
selectors:
  card: "[data-media-id='{media_id}']"
  card_menu: "[data-media-id='{media_id}'] button[aria-label='More']"
  menu_item: "role=menuitem[name='{item}']"
  quality: "[data-media-id='{media_id}'] >> role=menuitem[name='{quality}']"
```

`run` fills each selector in with the action's params. A selector that a step uses may
only refer to params that the action declares. Otherwise `run` raises
`UnknownSelectorParam` before the first step. Because braces mean placeholders, write
a literal brace as `{{` or `}}`.

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

One rule decides what is a **template**: every value that is sent to the page or the
file system is one, and every selector is one. Names are never templates, because
they refer to something in a manifest or in your code. Names include selector keys,
param names, `as` result names, hook names and `get_attribute`'s attribute name.

| Templates                                                                                       | Names (used as written)                                        |
| ----------------------------------------------------------------------------------------------- | -------------------------------------------------------------- |
| `goto` and `wait_for_url` URLs, `fill` and `select` values, `press` keys                         | `target`, `click` and `exclude` selector keys                  |
| `upload` paths, `name` and `mime_type`; `download` `to`                                         | `params`; `data`, `param` and `equals.param` references        |
| `if` conditions `url` and `equals.value`; `fail` messages                                       | `as`, `result` and `equals.result`, `call` hook names, `get_attribute` `attribute` |
| every selector in a selectors manifest                                                          |                                                                |

A template's `{name}` placeholders are replaced with the params of that name, and `{{`
and `}}` are literal braces, so the `Shift+{` key is written `Shift+{{`. Only plain
`{name}` placeholders are allowed, optionally with an escape (below). `str.format`
extras such as `{}`, `{0}`, `{name.attr}`, `{name[0]}`, `{name!r}` and `{name:>8}` are
rejected when the manifest is loaded, since they would otherwise fail, or quietly do
something odd, mid-run.

#### Escaping inserted values

A value inserted into a selector is read as selector syntax, so a quote, dot or
bracket in it can break the selector or match the wrong element. Write
`{name:<escape>}` to insert the value so that it matches itself literally:

| Escape     | Use it inside                                      | Example                                   |
| ---------- | -------------------------------------------------- | ----------------------------------------- |
| `string`   | Quotes: a CSS string, or Playwright's `[name='…']` | `"role=menuitem[name='{item:string}']"`   |
| `css`      | A CSS identifier, such as an id or class name      | `"#{media_id:css}"`                       |
| `regex`    | A regex, such as `text=/…/` or `[name=/…/]`        | `"role=button[name=/^{label:regex}$/]"`   |

`string` puts a backslash before `\`, `'` and `"`. `css` is the browser's
`CSS.escape`; it also works inside a CSS string, but not inside Playwright's
`[name='…']`, which doesn't read CSS's `\31 `-style escapes, so use `string` there.
`regex` escapes the regex specials and `/`. Escapes work in any template.

Every placeholder in an action must be declared in its `params`, which is checked when
the action is loaded. Placeholders in a selector are checked when an action that uses
it runs, because selectors and actions are loaded separately.

## Steps

Each step is a mapping with a single key: the step name, then its value.

### Step options

Any step can also take these keys. A step whose value is a plain string uses a mapping
form to carry them: `click: save` becomes `click: {target: save, timeout: 2000}`,
`goto: url` becomes `goto: {url: …}`, and `get_url: name` becomes `get_url: {as: …}`.

| Option     | Value             | Effect                                                                                       |
| ---------- | ----------------- | -------------------------------------------------------------------------------------------- |
| `timeout`  | milliseconds ≥ 0  | Overrides the page's default timeout for this step's waits. For `download` and a file-chooser `upload`, it bounds the wait for the download or chooser to start. |
| `optional` | `true` / `false`  | If the step times out, skip it instead of failing the action. A skipped retrieval saves `None`. |

`optional` only catches timeouts (see `timeout_errors` in
[the `WebPage` API](#the-webpage-api)). Any other error still fails the action. Steps
that never wait on the page reject `timeout`: `wait`, `get_url`, `get_all_texts`,
`if`, `call`, `fail` and `press` without a `target`.

```yaml
steps:
  - click: {target: dismiss_snackbar, timeout: 2000, optional: true}
  - wait_for: {target: results, timeout: 30000}
```

### Page steps

These act on the page itself.

| Step           | Value                         | Effect                                                         |
| -------------- | ----------------------------- | -------------------------------------------------------------- |
| `goto`         | URL template                  | `page.goto(url, wait_until="domcontentloaded")`                |
| `wait_for_url` | URL template (globs allowed)  | `page.wait_for_url(url, wait_until="domcontentloaded")`        |
| `wait`         | milliseconds (number ≥ 0)     | `page.wait_for_timeout(ms)`. Use `wait_for` instead when you can. |
| `press`        | `{key: <template>}`           | `page.keyboard.press(key)`: presses a key wherever the focus is, e.g. `Escape` to close an overlay. |

### Element steps

These act on the element that a selector key points to.

| Step       | Value                                | Effect                                             |
| ---------- | ------------------------------------ | -------------------------------------------------- |
| `click`    | selector key, or `{target: <key>, button: left \| right \| middle}` | Clicks the element; `button: right` opens its context menu. |
| `hover`    | selector key                         | Hovers over the element.                           |
| `check`    | selector key                         | Checks a checkbox or radio button.                 |
| `uncheck`  | selector key                         | Unchecks a checkbox.                               |
| `wait_for` | selector key, or `{target: <key>, state: <state>}` | Waits until the element is visible, or reaches `state`. |
| `fill`     | `{target: <key>, value: <template>}` | Fills the element with the formatted value.        |
| `select`   | `{target: <key>, value: <template>}` | Selects the `<option>` with that value or label.   |
| `press`    | `{target: <key>, key: <template>}`   | Presses a key or chord, e.g. `Enter`, `Control+A`. |

`wait_for`'s `state` is one of:

| State      | Waits until the element is                              |
| ---------- | ------------------------------------------------------- |
| `visible`  | visible (the default)                                   |
| `hidden`   | hidden or gone from the DOM, e.g. a dialog that closed  |
| `attached` | in the DOM, visible or not                              |
| `detached` | gone from the DOM, e.g. a deleted row                   |

Element steps use locators (see
[Playwright's](https://playwright.dev/python/docs/locators)), so they wait for the element to be actionable and are **strict**. If a selector matches
more than one element, the step fails rather than picking one. Like any template, a
`press` key writes a literal brace doubled: `Shift+{{`.

```yaml
steps:
  - select: {target: size, value: "{size}"}
  - check: gift
  - fill: {target: note, value: "{note}"}
  - press: {target: note, key: Enter}
  - wait_for_url: "**/thanks"
```

```yaml
params: [media_id]
steps:
  - click: {target: card, button: right}     # "[data-media-id='{media_id:string}']"
  - click: move_to_bin
  - wait_for: {target: card, state: detached, timeout: 10000}
  - press: {key: Escape}                      # close any menu left open
```

### File steps

These move files between the page and the local file system.

| Step       | Value                                                     | Effect                                                            |
| ---------- | --------------------------------------------------------- | ----------------------------------------------------------------- |
| `upload`   | `{target \| click \| steps, files: <file> \| [<file>, ...]}` | Gives files to the page. See below.                            |
| `download` | `{target \| steps, to: <path>, as: <name>}` (`as` optional) | Clicks the element (or runs the steps), waits for the download that starts, and saves it. |

Paths are templates. A relative path is resolved against the current working directory.

`upload` takes exactly one of these, which decides how the files reach the page:

| Key      | Value         | How                                                                                 |
| -------- | ------------- | ----------------------------------------------------------------------------------- |
| `target` | selector key  | Sets the files of that `<input type=file>` directly. `files: []` clears it.         |
| `click`  | selector key  | Clicks the element and gives the files to the file chooser it opens.                |
| `steps`  | list of steps | Runs the steps, such as opening a menu and then clicking "Upload", and gives the files to the file chooser they open. |

Use `click` or `steps` when the page has no file input you can reach, or when it only
accepts files through its own button. The files are read before anything is clicked,
so a missing file fails before the page changes.

Each `upload` file is a path, a `{param: <name>}` mapping (see
[below](#files-chosen-at-run-time)), or a mapping that sets what the page receives:

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
payload (a mapping with `data`, `name` or `mime_type`, or a payload in a files param),
its plain paths are read into payloads too, keeping their own names.

#### Files chosen at run time

When the number of files isn't known until run time, use `{param: <name>}` in `files`.
Then pass a list as that param's value. Each item is a path string or a `FilePayload`
dict (`name`, `mimeType`, `buffer`). A single path string also works. `run` checks the
list's items before the first step. A list is only accepted for a param used this way
and not in any template; any other list raises `ListParamNotAllowed`.

```yaml
params: [paths]
steps:
  - upload: {click: add_media_button, files: {param: paths}, timeout: 10000}
```

```python
await page.run("add_media", {"paths": ["/tmp/a.png", "/tmp/b.png"]})
```

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

When starting the download takes more than one click, such as picking a quality from
a menu, give `steps` instead of `target`. They run inside the wait for the download.
`timeout` sets how long to wait for the download to start:

```yaml
params: [media_id, quality, dir]
steps:
  - download:
      steps:
        - click: card_menu  # "[data-media-id='{media_id}'] button[aria-label='More']"
        - click: quality    # "... >> role=menuitem[name='{quality}']"
      to: "{dir}/"
      timeout: 120000
      as: video
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
two steps in an action may save under the same name, except in the two branches of
one `if`, since only one of them runs. This is checked when the action is loaded.

### Text without icons

Some pages render icons as ligature text, such as `<i class="material-icons">crop_16_9</i>`.
`get_text` would then return `crop_16_9 Landscape`. Give `get_text` or `get_all_texts`
an `exclude` selector key, and elements matching that selector are left out of the
text. Like any selector, it lives in the selectors manifest:

```yaml
# selectors
icons: "mat-icon, .material-icons, .material-symbols-outlined, .google-symbols"
```

```yaml
# action
- get_all_texts: {target: aspect_options, exclude: icons, as: aspects}
  # ["Landscape 16:9", "Portrait 9:16"]
```

The `exclude` selector must be plain CSS, since the page tests each element against
it with `Element.matches`. Playwright-only syntax such as `role=` or `>>` won't work
there. With `exclude`, the text is read by a script on the live page, which is never changed.
Hidden (`display: none`) elements are skipped, and runs of whitespace, line breaks
included, become one space.

## Conditions and hooks

### `if`

`if` runs one of two step lists depending on what is true at run time. It takes `then`,
an optional `else`, and exactly one condition:

| Condition     | Value                                             | True when                                         |
| ------------- | ------------------------------------------------- | ------------------------------------------------- |
| `visible`     | selector key, or `{target: <key>, timeout: <ms>}` | The element is visible.                           |
| `hidden`      | same                                              | The element is hidden or absent.                  |
| `exists`      | same                                              | The element is in the DOM, visible or not.        |
| `param`       | param name                                        | The param is not empty.                           |
| `result`      | result name                                       | An earlier step saved the result, and it is not empty or `None`. |
| `equals`      | `{param \| result: <name>, value: <template>}`    | The param, or saved result, equals the formatted value. |
| `url`         | URL template, with globs                          | The page's whole URL matches. `**` matches anything and `*` anything but `/`. |

Without `timeout`, an element condition checks the page once, right away. With
`timeout`, it waits up to that long for the state and is false if the state never
comes.

A `result` condition, or `equals` with `result`, must name a result that an earlier
step saves, which is checked when the action is loaded. If that step didn't run,
because it was in the other branch of an `if` or was a skipped `optional` step, the
result counts as unset.

```yaml
params: [media_id]
steps:
  - if:
      visible: {target: trash_tab, timeout: 2000}
      then:
        - click: trash_tab
  - if:
      exists: card          # "[data-media-id='{media_id}']"
      then:
        - click: card_menu
        - click: restore
      else:
        - get_url: not_found_at
```

### `fail`

`fail: <message template>` stops the action with `ActionFailed`. Put it in an `if` to
turn a state of the page into an error. The exception's `message` is the formatted
message, and its `results` holds whatever the action had saved by then, so you can
read, for example, the text of an error banner:

```yaml
params: [prompt]
steps:
  - click: generate
  - if:
      visible: {target: quota_banner, timeout: 3000}
      then:
        - get_text: {target: quota_banner, as: reason}
        - fail: "Generation for {prompt} was refused"
  - get_attribute: {target: status, attribute: data-state, as: state}
  - if:
      equals: {result: state, value: failed}
      then: [{fail: "Generation failed"}]
```

```python
from web_page.exceptions import ActionFailed

try:
    await page.run("generate", {"prompt": "a cat"})
except ActionFailed as e:
    print(e.message, e.results.get("reason"))
```

### Hooks

Some logic doesn't belong in YAML. Register an async function and run it with `call`:

```python
from web_page import ActionContext


async def pick_model(ctx: ActionContext) -> None:
    menu = ctx.page.locator(ctx.sel["model_menu"])
    await menu.click()
    ...
    ctx.results["model"] = await menu.inner_text()


page.register("pick_model", pick_model)
```

```yaml
steps:
  - click: settings
  - call: pick_model
```

The hook gets the running action's `ActionContext`. `ctx.page` is the page, `ctx.sel`
holds the selectors filled in with this run's params, `ctx.params` holds the params,
and the hook may add to `ctx.results`. `call: {hook: pick_model, optional: true}`
skips the hook if it raises a timeout error. `run` raises `HookNotRegistered` before
the first step if any hook the action calls isn't registered.

Hooks suit anything that loops or keeps state between checks, which steps don't do:
polling until an upload or generation finishes, a page script that snapshots a
whole grid at once, or remembering whether a confirmation dialog was submitted.

## Validation and errors

Problems are caught as early as possible:

| When        | What is checked                                                                                                  |
| ----------- | ---------------------------------------------------------------------------------------------------------------- |
| **On load** | File suffix and YAML syntax, manifest shape and types, unknown steps, malformed templates (selectors included), undeclared params, bad options and conditions, duplicate result names, results read before any step saves them, and names already loaded. Nested steps are checked the same way. |
| **On run**  | That the action is loaded, that params match exactly, that every selectors manifest is loaded, that every selector key exists, that selectors use only declared params, that only `upload` data params are `bytes` and only files params are lists (with valid items), and that every called hook is registered. All of this happens **before the first step runs**. |

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
│   ├── SelectorsAlreadyLoaded
│   └── HookAlreadyRegistered
├── NotLoaded
│   ├── ActionNotLoaded
│   ├── SelectorsNotLoaded
│   └── HookNotRegistered
├── BadActionParams
├── BytesParamNotAllowed
├── ListParamNotAllowed
├── BadFilesParam
├── UnknownSelectorParam
├── UnknownSelectorKey
└── ActionFailed
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

These bases already handle parsing, validation, `selector_keys` and `result_name`, so
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
selectors (already filled in with the params), `ctx.params` holds the param values,
and `ctx.results` collects saved values.

Every step gets the [`timeout` and `optional` options](#step-options) for free.
`optional` is handled for you. To honour `timeout`, pass `**self._timeout` to the page
call, which is empty when no timeout is set:
`await locator.dblclick(**self._timeout)`. A step that never waits on the page sets
the class attribute `takes_timeout = False`, so a manifest giving it a `timeout` is
rejected.

`ctx.page` is typed as the `web_page.Page` protocol, which only lists the methods the
built-in steps use. If your step calls something else, such as `dblclick` above, add
that method to the protocols in [`protocols.py`](src/web_page/protocols.py). That keeps
the type checker happy and documents what a page library must provide.

For a step whose value has a different shape, subclass `Step` directly and implement
`parse` and `run`, plus `selector_keys` if it uses any selectors. See `Goto` or `Wait` in
[`steps.py`](src/web_page/steps.py) for examples. A step that saves a result also
overrides `result_name` (see `GetUrl`). A step that holds nested steps parses them
with `parse_step_list`, runs them with `run_steps`, and returns them from
`branches`, so that load- and run-time checks see them (see `If` and `Download`).
Follow the [template rule](#templates) for new fields. Check a template with
`web_page.utils.check_placeholders` in `parse`, fill it in with `web_page.utils.fill`
in `run`, and list it in `templates`, so `run` knows its params must be strings. A
class declared without a `kind`, like
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

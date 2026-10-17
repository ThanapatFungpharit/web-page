import asyncio
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import FakePage, WriteYaml
from web_page import ActionContext, FilePayload, WebPage
from web_page.exceptions import (
    ActionAlreadyLoaded,
    ActionNotLoaded,
    BadActionFile,
    BadActionName,
    BadActionParams,
    BadFilesParam,
    BadSelectorsFile,
    BadSelectorsName,
    BytesParamNotAllowed,
    HookAlreadyRegistered,
    HookNotRegistered,
    ListParamNotAllowed,
    SelectorsAlreadyLoaded,
    SelectorsNotLoaded,
    UnknownSelectorKey,
    UnknownSelectorParam,
    WebPageError,
)

SELECTORS = """\
    name: search_page
    selectors:
      search: "#q"
      submit: "button[type=submit]"
      results: ".results"
"""

ACTION = """\
    name: search
    selectors_name: search_page
    params: [query]
    steps:
      - goto: "https://example.com/?q={query}"
      - fill:
          target: search
          value: "{query}"
      - click: submit
      - wait_for: results
"""


@pytest.fixture
async def loaded(web_page: WebPage, write_yaml: WriteYaml) -> WebPage:
    await web_page.load_selectors(write_yaml("selectors.yaml", SELECTORS))
    await web_page.load_action(write_yaml("action.yaml", ACTION))
    return web_page


# load_selectors


async def test_load_selectors_twice(web_page: WebPage, write_yaml: WriteYaml) -> None:
    path = write_yaml("s.yaml", SELECTORS)
    await web_page.load_selectors(path)
    with pytest.raises(SelectorsAlreadyLoaded) as exc:
        await web_page.load_selectors(path)
    assert exc.value.selectors_name == "search_page"


async def test_load_selectors_missing_name(
    web_page: WebPage, write_yaml: WriteYaml
) -> None:
    path = write_yaml("s.yaml", "selectors: {a: b}\n")
    with pytest.raises(BadSelectorsFile, match="missing required key 'name'"):
        await web_page.load_selectors(path)


async def test_load_selectors_bad_name(
    web_page: WebPage, write_yaml: WriteYaml
) -> None:
    path = write_yaml("s.yaml", "name: 1\nselectors: {a: b}\n")
    with pytest.raises(BadSelectorsName):
        await web_page.load_selectors(path)


@pytest.mark.parametrize("selectors", ["[a, b]", "{a: 1}", "'a'"])
async def test_load_selectors_bad_selectors(
    web_page: WebPage, write_yaml: WriteYaml, selectors: str
) -> None:
    path = write_yaml("s.yaml", f"name: s\nselectors: {selectors}\n")
    with pytest.raises(BadSelectorsFile, match=r"dict\[str, str\]"):
        await web_page.load_selectors(path)


# load_action


async def test_load_action_twice(web_page: WebPage, write_yaml: WriteYaml) -> None:
    path = write_yaml("a.yaml", ACTION)
    await web_page.load_action(path)
    with pytest.raises(ActionAlreadyLoaded) as exc:
        await web_page.load_action(path)
    assert exc.value.action_name == "search"


async def test_load_action_bad_name(web_page: WebPage, write_yaml: WriteYaml) -> None:
    path = write_yaml("a.yaml", "name: [x]\nselectors_name: s\nsteps: []\n")
    with pytest.raises(BadActionName):
        await web_page.load_action(path)


@pytest.mark.parametrize("missing", ["name", "selectors_name", "steps"])
async def test_load_action_missing_key(
    web_page: WebPage, write_yaml: WriteYaml, missing: str
) -> None:
    fields = {"name": "a", "selectors_name": "s", "steps": "[]"}
    del fields[missing]
    path = write_yaml("a.yaml", "".join(f"{k}: {v}\n" for k, v in fields.items()))
    with pytest.raises(BadActionFile, match=f"missing required key '{missing}'"):
        await web_page.load_action(path)


async def test_load_action_bad_params(web_page: WebPage, write_yaml: WriteYaml) -> None:
    path = write_yaml("a.yaml", "name: a\nselectors_name: s\nparams: 1\nsteps: []\n")
    with pytest.raises(BadActionFile, match="Action params"):
        await web_page.load_action(path)


async def test_failed_load_does_not_register(
    web_page: WebPage, write_yaml: WriteYaml
) -> None:
    bad = write_yaml("bad.yaml", "name: a\nselectors_name: s\nsteps: [{nope: x}]\n")
    with pytest.raises(BadActionFile):
        await web_page.load_action(bad)
    good = write_yaml("good.yaml", "name: a\nselectors_name: s\nsteps: []\n")
    await web_page.load_action(good)


# run


async def test_run(loaded: WebPage, fake_page: FakePage) -> None:
    assert await loaded.run("search", {"query": "cats"}) == {}
    assert fake_page.calls == [
        ("goto", "https://example.com/?q=cats", "domcontentloaded"),
        ("fill", "#q", "cats"),
        ("click", "button[type=submit]"),
        ("wait_for", ".results", "visible"),
    ]


async def test_run_without_params(
    web_page: WebPage, fake_page: FakePage, write_yaml: WriteYaml
) -> None:
    await web_page.load_selectors(write_yaml("s.yaml", SELECTORS))
    await web_page.load_action(
        write_yaml(
            "a.yaml", "name: a\nselectors_name: search_page\nsteps: [{click: submit}]\n"
        )
    )
    await web_page.run("a")
    assert fake_page.calls == [("click", "button[type=submit]")]


async def test_run_returns_results(web_page: WebPage, write_yaml: WriteYaml) -> None:
    await web_page.load_selectors(write_yaml("s.yaml", SELECTORS))
    await web_page.load_action(
        write_yaml(
            "a.yaml",
            """\
            name: scrape
            selectors_name: search_page
            params: [query]
            steps:
              - goto: "https://example.com/?q={query}"
              - get_url: url
              - get_value: {target: search, as: typed}
              - get_all_texts: {target: results, as: rows}
            """,
        )
    )
    assert await web_page.run("scrape", {"query": "cats"}) == {
        "url": "https://example.com/?q=cats",
        "typed": "value of #q",
        "rows": ["text 1 of .results", "text 2 of .results"],
    }
    # Each run starts with fresh results.
    results = await web_page.run("scrape", {"query": "dogs"})
    assert results["url"] == "https://example.com/?q=dogs"


async def test_run_checks_retrieval_selector_keys(
    web_page: WebPage, write_yaml: WriteYaml
) -> None:
    await web_page.load_selectors(write_yaml("s.yaml", SELECTORS))
    await web_page.load_action(
        write_yaml(
            "a.yaml",
            "name: a\nselectors_name: search_page\n"
            "steps: [{get_text: {target: nope, as: t}}]\n",
        )
    )
    with pytest.raises(UnknownSelectorKey):
        await web_page.run("a")


async def test_run_unknown_action(loaded: WebPage) -> None:
    with pytest.raises(ActionNotLoaded) as exc:
        await loaded.run("nope")
    assert exc.value.loaded == ("search",)


@pytest.mark.parametrize(
    ("params", "missing", "unexpected"),
    [
        (None, ("query",), ()),
        ({}, ("query",), ()),
        ({"query": "x", "extra": "y"}, (), ("extra",)),
        ({"other": "y"}, ("query",), ("other",)),
    ],
)
async def test_run_bad_params(
    loaded: WebPage,
    fake_page: FakePage,
    params: dict[str, str] | None,
    missing: tuple[str, ...],
    unexpected: tuple[str, ...],
) -> None:
    with pytest.raises(BadActionParams) as exc:
        await loaded.run("search", params)
    assert exc.value.missing == missing
    assert exc.value.unexpected == unexpected
    assert fake_page.calls == []


async def test_run_selectors_not_loaded(
    web_page: WebPage, fake_page: FakePage, write_yaml: WriteYaml
) -> None:
    await web_page.load_action(write_yaml("a.yaml", ACTION))
    with pytest.raises(SelectorsNotLoaded) as exc:
        await web_page.run("search", {"query": "x"})
    assert exc.value.selectors_name == "search_page"
    assert fake_page.calls == []


async def test_run_unknown_selector_key(
    web_page: WebPage, fake_page: FakePage, write_yaml: WriteYaml
) -> None:
    await web_page.load_selectors(
        write_yaml("s.yaml", "name: s\nselectors: {a: '#a'}\n")
    )
    await web_page.load_action(
        write_yaml(
            "a.yaml",
            "name: x\nselectors_name: s\nsteps:\n"
            "  - goto: https://example.com\n  - click: a\n  - click: b\n",
        )
    )
    with pytest.raises(UnknownSelectorKey) as exc:
        await web_page.run("x")
    assert exc.value.unknown == ("b",)
    # Validation happens before anything runs, so the goto never fired.
    assert fake_page.calls == []


async def test_run_later_selectors_override(
    web_page: WebPage, fake_page: FakePage, write_yaml: WriteYaml
) -> None:
    await web_page.load_selectors(
        write_yaml("s1.yaml", "name: base\nselectors: {a: '#base-a', b: '#base-b'}\n")
    )
    await web_page.load_selectors(
        write_yaml("s2.yaml", "name: override\nselectors: {a: '#override-a'}\n")
    )
    await web_page.load_action(
        write_yaml(
            "a.yaml",
            "name: x\nselectors_name: [base, override]\nsteps: [{click: a}, {click: b}]\n",
        )
    )
    await web_page.run("x")
    assert fake_page.calls == [("click", "#override-a"), ("click", "#base-b")]


async def test_concurrent_runs_do_not_interleave(
    web_page: WebPage, fake_page: FakePage, write_yaml: WriteYaml
) -> None:
    await web_page.load_selectors(
        write_yaml("s.yaml", "name: s\nselectors: {a: '#a', b: '#b'}\n")
    )
    await web_page.load_action(
        write_yaml(
            "a.yaml",
            "name: x\nselectors_name: s\nparams: [v]\nsteps:\n"
            "  - fill: {target: a, value: '{v}'}\n  - fill: {target: b, value: '{v}'}\n",
        )
    )
    await asyncio.gather(*(web_page.run("x", {"v": str(i)}) for i in range(5)))

    values = [call[2] for call in fake_page.calls]
    assert values[0::2] == values[1::2]


def test_errors_share_base_class() -> None:
    for error in (
        ActionAlreadyLoaded,
        ActionNotLoaded,
        BadActionFile,
        BadActionParams,
        BadSelectorsFile,
        SelectorsAlreadyLoaded,
        SelectorsNotLoaded,
        UnknownSelectorKey,
        UnknownSelectorParam,
        HookAlreadyRegistered,
        HookNotRegistered,
        ListParamNotAllowed,
        BadFilesParam,
    ):
        assert issubclass(error, WebPageError)


async def test_accepts_path_objects(web_page: WebPage, tmp_path: Path) -> None:
    path = tmp_path / "s.yml"
    path.write_text("name: s\nselectors: {}\n", encoding="utf-8")
    await web_page.load_selectors(path)


UPLOAD_SELECTORS = """    name: form
    selectors:
      docs: "#docs"
    """

UPLOAD_ACTION = """    name: attach
    selectors_name: form
    params: [blob, name, both]
    steps:
      - upload: {target: docs, files: [{data: blob, name: "{name}"}, {data: both, name: x}]}
      - fill: {target: docs, value: "{both}"}
    """


async def test_run_accepts_bytes_only_for_data_params(
    web_page: WebPage, fake_page: FakePage, write_yaml: WriteYaml
) -> None:
    await web_page.load_selectors(write_yaml("s.yaml", UPLOAD_SELECTORS))
    await web_page.load_action(write_yaml("a.yaml", UPLOAD_ACTION))

    for bad in ({"name": b"n"}, {"both": b"b"}):
        params = {"blob": b"1", "name": "n", "both": "b", **bad}
        with pytest.raises(BytesParamNotAllowed) as exc:
            await web_page.run("attach", params)
        assert exc.value.params == tuple(bad)
        assert exc.value.allowed == ("blob",)
    assert fake_page.calls == []

    await web_page.run("attach", {"blob": b"1", "name": "n.txt", "both": "b"})
    assert fake_page.calls == [
        (
            "set_input_files",
            "#docs",
            "n.txt text/plain b'1'",
            "x application/octet-stream b'b'",
        ),
        ("fill", "#docs", "b"),
    ]


# Templated selectors

MEDIA_SELECTORS = """\
    name: media
    selectors:
      card: "[data-id='{media_id}']"
      menu: "[data-id='{media_id}'] button.more"
      item: "role=menuitem[name='{item}']"
      other: "#other-{unrelated}"
      braces: "x{{y}}"
"""


async def test_selectors_are_templates(
    web_page: WebPage, fake_page: FakePage, write_yaml: WriteYaml
) -> None:
    await web_page.load_selectors(write_yaml("s.yaml", MEDIA_SELECTORS))
    await web_page.load_action(
        write_yaml(
            "a.yaml",
            """\
            name: delete
            selectors_name: media
            params: [media_id, item]
            steps:
              - hover: card
              - click: menu
              - click: item
              - click: braces
            """,
        )
    )
    await web_page.run("delete", {"media_id": "42", "item": "Delete"})
    assert fake_page.calls == [
        ("hover", "[data-id='42']"),
        ("click", "[data-id='42'] button.more"),
        ("click", "role=menuitem[name='Delete']"),
        ("click", "x{y}"),
    ]


async def test_selector_with_undeclared_param(
    web_page: WebPage, fake_page: FakePage, write_yaml: WriteYaml
) -> None:
    await web_page.load_selectors(write_yaml("s.yaml", MEDIA_SELECTORS))
    await web_page.load_action(
        write_yaml(
            "a.yaml",
            "name: a\nselectors_name: media\nparams: [item]\n"
            "steps: [{click: item}, {click: menu}]\n",
        )
    )
    with pytest.raises(UnknownSelectorParam) as exc:
        await web_page.run("a", {"item": "x"})
    assert exc.value.selector_key == "menu"
    assert exc.value.unknown == ("media_id",)
    assert fake_page.calls == []


async def test_hooks_see_every_selector_the_params_fill(
    web_page: WebPage, write_yaml: WriteYaml
) -> None:
    seen: dict[str, str] = {}

    async def look(ctx: ActionContext) -> None:
        seen.update(ctx.sel)

    web_page.register("look", look)
    await web_page.load_selectors(write_yaml("s.yaml", MEDIA_SELECTORS))
    await web_page.load_action(
        write_yaml(
            "a.yaml",
            "name: a\nselectors_name: media\nparams: [media_id]\n"
            "steps: [{call: look}]\n",
        )
    )
    await web_page.run("a", {"media_id": "7"})
    # `item` and `other` need params this action doesn't have, so they are left out.
    assert seen == {
        "card": "[data-id='7']",
        "menu": "[data-id='7'] button.more",
        "braces": "x{y}",
    }


async def test_load_selectors_rejects_malformed_template(
    web_page: WebPage, write_yaml: WriteYaml
) -> None:
    path = write_yaml("s.yaml", "name: s\nselectors: {a: 'div{'}\n")
    with pytest.raises(BadSelectorsFile, match="malformed template"):
        await web_page.load_selectors(path)


async def test_bytes_param_in_selector_is_rejected(
    web_page: WebPage, write_yaml: WriteYaml
) -> None:
    await web_page.load_selectors(
        write_yaml("s.yaml", "name: s\nselectors: {docs: '#{blob}'}\n")
    )
    await web_page.load_action(
        write_yaml(
            "a.yaml",
            "name: a\nselectors_name: s\nparams: [blob]\nsteps:\n"
            "  - upload: {target: docs, files: {data: blob, name: b}}\n",
        )
    )
    with pytest.raises(BytesParamNotAllowed):
        await web_page.run("a", {"blob": b"x"})


# Files chosen at run time

FILES_ACTION = """\
    name: attach
    selectors_name: form
    params: [paths, label]
    steps:
      - upload: {target: docs, files: {param: paths}}
      - fill: {target: docs, value: "{label}"}
"""


async def test_run_accepts_lists_only_for_files_params(
    web_page: WebPage,
    fake_page: FakePage,
    write_yaml: WriteYaml,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "one.txt").write_text("one")
    await web_page.load_selectors(write_yaml("s.yaml", UPLOAD_SELECTORS))
    await web_page.load_action(write_yaml("a.yaml", FILES_ACTION))

    with pytest.raises(ListParamNotAllowed) as exc:
        await web_page.run("attach", {"paths": [], "label": ["x"]})
    assert exc.value.params == ("label",)
    assert exc.value.allowed == ("paths",)

    # Not a FilePayload: the kind of mistake the run-time check is for.
    half_payload: Any = {"name": "b"}
    with pytest.raises(BadFilesParam) as bad:
        await web_page.run("attach", {"paths": ["a", half_payload], "label": "x"})
    assert bad.value.item == {"name": "b"}
    assert fake_page.calls == []

    payload: FilePayload = {"name": "b", "mimeType": "text/plain", "buffer": b"B"}
    await web_page.run("attach", {"paths": [payload], "label": "x"})
    await web_page.run("attach", {"paths": "one.txt", "label": "x"})
    assert fake_page.calls == [
        ("set_input_files", "#docs", "b text/plain b'B'"),
        ("fill", "#docs", "x"),
        ("set_input_files", "#docs", "one.txt"),
        ("fill", "#docs", "x"),
    ]


# Hooks


async def test_register_twice(web_page: WebPage) -> None:
    async def hook(ctx: ActionContext) -> None:
        pass

    web_page.register("h", hook)
    with pytest.raises(HookAlreadyRegistered) as exc:
        web_page.register("h", hook)
    assert exc.value.hook_name == "h"


async def test_run_checks_hooks_are_registered(
    web_page: WebPage, fake_page: FakePage, write_yaml: WriteYaml
) -> None:
    await web_page.load_selectors(write_yaml("s.yaml", SELECTORS))
    await web_page.load_action(
        write_yaml(
            "a.yaml",
            "name: a\nselectors_name: search_page\nparams: [q]\nsteps:\n"
            "  - click: submit\n"
            "  - if: {param: q, then: [{call: pick}], else: [{call: other}]}\n",
        )
    )
    with pytest.raises(HookNotRegistered) as exc:
        await web_page.run("a", {"q": "x"})
    assert exc.value.unknown == ("other", "pick")
    assert fake_page.calls == []


async def test_run_checks_nested_selector_keys(
    web_page: WebPage, write_yaml: WriteYaml
) -> None:
    await web_page.load_selectors(write_yaml("s.yaml", SELECTORS))
    await web_page.load_action(
        write_yaml(
            "a.yaml",
            "name: a\nselectors_name: search_page\nsteps:\n"
            "  - download: {steps: [{click: nope}], to: out/}\n",
        )
    )
    with pytest.raises(UnknownSelectorKey) as exc:
        await web_page.run("a")
    assert exc.value.unknown == ("nope",)


async def test_hook_results_are_returned(
    web_page: WebPage, write_yaml: WriteYaml
) -> None:
    async def pick(ctx: ActionContext) -> None:
        ctx.results["model"] = f"picked {ctx.params['model']}"

    web_page.register("pick", pick)
    await web_page.load_selectors(write_yaml("s.yaml", SELECTORS))
    await web_page.load_action(
        write_yaml(
            "a.yaml",
            "name: a\nselectors_name: search_page\nparams: [model]\n"
            "steps: [{call: pick}]\n",
        )
    )
    assert await web_page.run("a", {"model": "fast"}) == {"model": "picked fast"}


# Optional steps


class PageTimeout(Exception):
    """Stands in for a page library's own timeout error."""


async def test_custom_timeout_errors(write_yaml: WriteYaml) -> None:
    async def slow(ctx: ActionContext) -> None:
        raise PageTimeout

    web_page = WebPage(FakePage(), timeout_errors=[PageTimeout])
    web_page.register("slow", slow)
    await web_page.load_selectors(write_yaml("s.yaml", SELECTORS))
    await web_page.load_action(
        write_yaml(
            "a.yaml",
            "name: a\nselectors_name: search_page\n"
            "steps: [{call: {hook: slow, optional: true}}]\n",
        )
    )
    assert await web_page.run("a") == {}


async def test_optional_steps_in_a_run(
    web_page: WebPage, fake_page: FakePage, write_yaml: WriteYaml
) -> None:
    fake_page.missing.add(".results")
    await web_page.load_selectors(write_yaml("s.yaml", SELECTORS))
    await web_page.load_action(
        write_yaml(
            "a.yaml",
            """\
            name: a
            selectors_name: search_page
            steps:
              - click: {target: results, timeout: 100, optional: true}
              - get_text: {target: results, as: snackbar, optional: true}
              - if:
                  visible: {target: results, timeout: 50}
                  then: [{click: results}]
                  else: [{click: submit}]
            """,
        )
    )
    assert await web_page.run("a") == {"snackbar": None}
    assert fake_page.calls == [("click", "button[type=submit]")]

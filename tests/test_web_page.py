import asyncio
from pathlib import Path

import pytest

from tests.conftest import FakePage, WriteYaml
from web_page import WebPage
from web_page.exceptions import (
    ActionAlreadyLoaded,
    ActionNotLoaded,
    BadActionFile,
    BadActionName,
    BadActionParams,
    BadSelectorsFile,
    BadSelectorsName,
    SelectorsAlreadyLoaded,
    SelectorsNotLoaded,
    UnknownSelectorKey,
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
    await loaded.run("search", {"query": "cats"})
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
    ):
        assert issubclass(error, WebPageError)


async def test_accepts_path_objects(web_page: WebPage, tmp_path: Path) -> None:
    path = tmp_path / "s.yml"
    path.write_text("name: s\nselectors: {}\n", encoding="utf-8")
    await web_page.load_selectors(path)

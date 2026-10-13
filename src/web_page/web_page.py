from __future__ import annotations

from asyncio import Lock

from playwright.async_api import Page

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
)
from web_page.manifest import as_str_list, read_manifest, require
from web_page.steps import parse_steps
from web_page.types import (
    Action,
    ActionContext,
    ActionName,
    CssSelector,
    ManifestPath,
    ParamName,
    ParamValues,
    SelectorKey,
    Selectors,
    SelectorsName,
)
from web_page.utils import is_str_dict


class WebPage:
    def __init__(self, page: Page) -> None:
        self._page = page
        self._lock = Lock()
        self._actions: dict[ActionName, Action] = {}
        self._selectors: dict[SelectorsName, Selectors] = {}

    async def load_selectors(self, manifest_path: ManifestPath) -> None:
        manifest = await read_manifest(manifest_path, BadSelectorsFile, "Selectors")

        name = require(manifest, "name", BadSelectorsFile, "Selectors")
        if not isinstance(name, str):
            raise BadSelectorsName(f"Selectors name should be str, not {name!r}.")
        if name in self._selectors:
            raise SelectorsAlreadyLoaded(name)

        selectors = require(manifest, "selectors", BadSelectorsFile, "Selectors")
        if not is_str_dict(selectors):
            raise BadSelectorsFile(
                f"Selectors should be dict[str, str], not {selectors!r}."
            )

        self._selectors[name] = selectors

    async def load_action(self, manifest_path: ManifestPath) -> None:
        manifest = await read_manifest(manifest_path, BadActionFile, "Action")

        name = require(manifest, "name", BadActionFile, "Action")
        if not isinstance(name, str):
            raise BadActionName(f"Action name should be str, not {name!r}.")
        if name in self._actions:
            raise ActionAlreadyLoaded(name)

        selectors_name: list[SelectorsName] = as_str_list(
            require(manifest, "selectors_name", BadActionFile, "Action"),
            BadActionFile,
            "Selectors name",
        )
        params: list[ParamName] = as_str_list(
            manifest.get("params", []), BadActionFile, "Action params"
        )
        steps = parse_steps(require(manifest, "steps", BadActionFile, "Action"), params)

        self._actions[name] = Action(
            selectors_name=tuple(selectors_name),
            params=tuple(params),
            steps=steps,
        )

    async def run(
        self, action_name: ActionName, params: ParamValues | None = None
    ) -> None:
        """Run a loaded action on the page with the given param values.

        Everything is validated before the first step runs, so a bad call never
        leaves the page half-driven. Steps run under a lock so concurrent calls
        on the same page don't interleave.
        """
        try:
            action = self._actions[action_name]
        except KeyError:
            raise ActionNotLoaded(action_name, self._actions) from None

        values: ParamValues = params or {}
        missing = set(action.params) - set(values)
        extra = set(values) - set(action.params)
        if missing or extra:
            raise BadActionParams(action_name, action.params, missing, extra)

        # Later selector sets override earlier ones on key clashes.
        sel: dict[SelectorKey, CssSelector] = {}
        for selectors_name in action.selectors_name:
            try:
                sel.update(self._selectors[selectors_name])
            except KeyError:
                raise SelectorsNotLoaded(action_name, selectors_name) from None

        unknown = {
            key
            for step in action.steps
            if (key := step.selector_key) is not None and key not in sel
        }
        if unknown:
            raise UnknownSelectorKey(action_name, unknown, action.selectors_name)

        ctx = ActionContext(page=self._page, sel=sel, params=dict(values))
        async with self._lock:
            for step in action.steps:
                await step.run(ctx)

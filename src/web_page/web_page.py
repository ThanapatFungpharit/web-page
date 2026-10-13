from __future__ import annotations

from asyncio import Lock
from collections.abc import Iterable

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
)
from web_page.manifest import as_str_list, read_manifest, require
from web_page.protocols import Page, default_timeout_errors
from web_page.steps import parse_steps, run_steps, walk
from web_page.types import (
    Action,
    ActionContext,
    ActionName,
    CssSelector,
    Hook,
    HookName,
    ManifestPath,
    ParamName,
    ParamValues,
    Results,
    SelectorKey,
    Selectors,
    SelectorsName,
)
from web_page.utils import fill, is_file_payload, is_str_dict, placeholders


class WebPage:
    def __init__(
        self,
        page: Page,
        *,
        timeout_errors: Iterable[type[BaseException]] | None = None,
    ) -> None:
        """Wrap ``page``. ``timeout_errors`` are what an ``optional`` step treats as
        "not there"; the default suits Playwright, so set it for a page library
        whose timeouts raise something else."""
        self._page = page
        self._lock = Lock()
        self._actions: dict[ActionName, Action] = {}
        self._selectors: dict[SelectorsName, Selectors] = {}
        self._hooks: dict[HookName, Hook] = {}
        self._timeout_errors = (
            default_timeout_errors()
            if timeout_errors is None
            else tuple(timeout_errors)
        )

    def register(self, name: HookName, hook: Hook) -> None:
        """Make an async function runnable from manifests as ``- call: <name>``.

        The hook gets the running action's ``ActionContext``: the page, the
        formatted selectors, the params, and the results so far, which it may add
        to. Use it for logic a manifest can't express.
        """
        if name in self._hooks:
            raise HookAlreadyRegistered(name)
        self._hooks[name] = hook

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
        # Selectors are templates; which params they need is checked in run, since
        # the actions using them may not be loaded yet.
        for key, selector in selectors.items():
            try:
                placeholders(selector)
            except ValueError as e:
                raise BadSelectorsFile(
                    f"Selector {key!r} has a malformed template {selector!r}: {e}. "
                    f"Write a literal brace as '{{{{' or '}}}}'."
                ) from e

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

        # A bytes or list value can't be formatted into text, so a param may be
        # one only if no template uses it. Selector templates are checked in run.
        every = tuple(walk(steps))
        formatted = {
            p for step in every for t in step.templates for p in placeholders(t)
        }
        data = {p for step in every for p in step.data_params}
        files = {p for step in every for p in step.list_params}
        self._actions[name] = Action(
            selectors_name=tuple(selectors_name),
            params=tuple(params),
            steps=steps,
            bytes_params=frozenset(data - formatted - files),
            list_params=frozenset(files - formatted - data),
        )

    async def run(
        self, action_name: ActionName, params: ParamValues | None = None
    ) -> Results:
        """Run a loaded action on the page with the given param values.

        Returns the values saved by retrieval steps (``get_text`` etc.) keyed by
        their ``as`` names, or an empty dict if the action retrieves nothing.
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
        raw_sel: dict[SelectorKey, CssSelector] = {}
        for selectors_name in action.selectors_name:
            try:
                raw_sel.update(self._selectors[selectors_name])
            except KeyError:
                raise SelectorsNotLoaded(action_name, selectors_name) from None

        steps = tuple(walk(action.steps))
        used = {key for step in steps for key in step.selector_keys}
        unknown = used - raw_sel.keys()
        if unknown:
            raise UnknownSelectorKey(action_name, unknown, action.selectors_name)
        in_selectors: set[ParamName] = set()
        for key in sorted(used):
            names = placeholders(raw_sel[key])
            if undeclared := names - set(action.params):
                raise UnknownSelectorParam(action_name, key, raw_sel[key], undeclared)
            in_selectors |= names

        # Params formatted into a used selector must be strings too.
        bytes_ok = action.bytes_params - in_selectors
        bad_bytes = {
            name
            for name, value in values.items()
            if isinstance(value, bytes) and name not in bytes_ok
        }
        if bad_bytes:
            raise BytesParamNotAllowed(action_name, bad_bytes, bytes_ok)
        lists_ok = action.list_params - in_selectors
        bad_lists = {
            name
            for name, value in values.items()
            if isinstance(value, list) and name not in lists_ok
        }
        if bad_lists:
            raise ListParamNotAllowed(action_name, bad_lists, lists_ok)
        for name in sorted(lists_ok):
            value = values[name]
            for item in value if isinstance(value, list) else ():
                if not (isinstance(item, str) or is_file_payload(item)):
                    raise BadFilesParam(action_name, name, item)

        unregistered = {
            hook for step in steps if (hook := step.hook_name) is not None
        } - self._hooks.keys()
        if unregistered:
            raise HookNotRegistered(action_name, unregistered)

        # Fill in every selector the params can, so hooks can use them too. Each
        # one a step uses can be, as checked above.
        sel = {
            key: fill(selector, values)
            for key, selector in raw_sel.items()
            if all(isinstance(values.get(p), str) for p in placeholders(selector))
        }
        ctx = ActionContext(
            page=self._page,
            sel=sel,
            params=dict(values),
            hooks=dict(self._hooks),
            timeout_errors=self._timeout_errors,
        )
        async with self._lock:
            await run_steps(action.steps, ctx)
        return ctx.results

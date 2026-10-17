from __future__ import annotations

from asyncio import Lock
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from web_page.exceptions import (
    ActionAlreadyLoaded,
    ActionNotLoaded,
    BadActionFile,
    BadActionName,
    BadActionParams,
    BadFilesParam,
    BadParamType,
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
from web_page.manifest import as_str_list, check_keys, read_manifest, require
from web_page.protocols import Page, default_timeout_errors
from web_page.steps import RunAction, Step, _check_results, parse_steps, run_steps, walk
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
from web_page.utils import (
    SelectorTemplates,
    compiled_template,
    is_file_payload,
    is_str_dict,
    placeholders,
    prepare_template,
)


@dataclass(frozen=True, slots=True)
class _ActionPlan:
    """Static action metadata, compiled once when its manifest is loaded."""

    action: Action
    params: frozenset[str]
    selectors: frozenset[str]
    hooks: frozenset[str]
    results: frozenset[str]
    calls: tuple[RunAction, ...]

    @classmethod
    def compile(cls, action: Action, steps: tuple[Step, ...]) -> _ActionPlan:
        return cls(
            action,
            frozenset(action.params),
            frozenset(key for step in steps for key in step.selector_keys),
            frozenset(hook for step in steps if (hook := step.hook_name) is not None),
            frozenset(name for step in steps for name in step.result_names),
            tuple(step for step in steps if isinstance(step, RunAction)),
        )


@dataclass(frozen=True, slots=True)
class _LinkedAction:
    """A plan linked to selectors and validated dependencies for this registry."""

    selectors: dict[SelectorKey, CssSelector]
    bytes_params: frozenset[str]
    list_params: frozenset[str]
    # Paths retain recursion detection even for actions entered dynamically by hooks.
    paths: dict[str, tuple[str, ...]]


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
        self._plans: dict[ActionName, _ActionPlan] = {}
        self._linked: dict[ActionName, _LinkedAction] = {}
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
        self._linked.clear()

    async def load_selectors(self, manifest_path: ManifestPath) -> None:
        manifest = await read_manifest(manifest_path, BadSelectorsFile, "Selectors")
        check_keys(manifest, {"name", "selectors"}, BadSelectorsFile)

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
        prepared: dict[str, str] = {}
        for key, selector in selectors.items():
            try:
                prepared[key] = prepare_template(selector)
            except ValueError as e:
                raise BadSelectorsFile(
                    f"Selector {key!r} has a malformed template {selector!r}: {e}. "
                    f"Write a literal brace as '{{{{' or '}}}}'."
                ) from e

        self._selectors[name] = prepared
        self._linked.clear()

    async def load_action(self, manifest_path: ManifestPath) -> None:
        manifest = await read_manifest(manifest_path, BadActionFile, "Action")
        check_keys(
            manifest, {"name", "selectors_name", "params", "steps"}, BadActionFile
        )

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
        action = Action(
            selectors_name=tuple(selectors_name),
            params=tuple(params),
            steps=steps,
            bytes_params=frozenset(data - formatted - files),
            list_params=frozenset(files - formatted - data),
        )
        plan = _ActionPlan.compile(action, every)
        self._actions[name] = action
        self._plans[name] = plan
        self._linked.clear()

    async def run(
        self, action_name: ActionName, params: ParamValues | None = None
    ) -> Results:
        """Run a loaded action on the page with the given param values.

        Returns the values saved by retrieval steps (``get_text`` etc.) keyed by
        their ``as`` names, or an empty dict if the action retrieves nothing.
        Caller params and manifest references are validated before the first step.
        Live results and filesystem operations can still fail while executing.
        Steps run under a lock so concurrent calls on the same page don't interleave.
        """
        steps, ctx = self._prepare(action_name, params)
        async with self._lock:
            await run_steps(steps, ctx)
        return ctx.results

    def _prepare(
        self,
        action_name: ActionName,
        params: ParamValues | None,
        stack: tuple[str, ...] = (),
    ) -> tuple[tuple[Step, ...], ActionContext]:
        if action_name in stack:
            raise BadActionFile(
                f"Recursive action calls: {' -> '.join((*stack, action_name))}."
            )
        plan = self._plan(action_name)
        action = plan.action

        if params is not None and not isinstance(params, Mapping):
            raise BadParamType(action_name, "<params>", params)
        values: ParamValues = {} if params is None else dict(params)
        for name, value in values.items():
            if not isinstance(name, str) or not isinstance(value, str | bytes | list):
                raise BadParamType(action_name, str(name), value)
        missing = plan.params - values.keys()
        extra = values.keys() - plan.params
        if missing or extra:
            raise BadActionParams(action_name, action.params, missing, extra)

        linked = self._link(action_name, stack)

        # Params formatted into a used selector must be strings too.
        bytes_ok = linked.bytes_params
        bad_bytes = {
            name
            for name, value in values.items()
            if isinstance(value, bytes) and name not in bytes_ok
        }
        if bad_bytes:
            raise BytesParamNotAllowed(action_name, bad_bytes, bytes_ok)
        lists_ok = linked.list_params
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

        # Resolve selectors on access so new results can select later elements.
        results: Results = {}
        sel = SelectorTemplates(dict(linked.selectors), values, results)

        async def execute_child(name: str, child_params: ParamValues) -> Results:
            child_steps, child_ctx = self._prepare(
                name, child_params, (*stack, action_name)
            )
            await run_steps(child_steps, child_ctx)
            return child_ctx.results

        ctx = ActionContext(
            page=self._page,
            sel=sel,
            params=values,
            hooks=dict(self._hooks),
            results=results,
            run_action=execute_child,
            timeout_errors=self._timeout_errors,
        )
        return action.steps, ctx

    def _plan(self, action_name: ActionName) -> _ActionPlan:
        try:
            return self._plans[action_name]
        except KeyError:
            raise ActionNotLoaded(action_name, self._actions) from None

    def _link(
        self, action_name: ActionName, stack: tuple[str, ...] = ()
    ) -> _LinkedAction:
        # Manifests may load in any order, so cross-manifest linking happens on
        # first use. Successful links are reused until the registry changes.
        if action_name in stack:
            raise BadActionFile(
                f"Recursive action calls: {' -> '.join((*stack, action_name))}."
            )
        if linked := self._linked.get(action_name):
            for ancestor in stack:
                if path := linked.paths.get(ancestor):
                    raise BadActionFile(
                        f"Recursive action calls: {' -> '.join((*stack, *path))}."
                    )
            return linked

        plan = self._plan(action_name)
        action = plan.action
        raw_sel: dict[SelectorKey, CssSelector] = {}
        for selectors_name in action.selectors_name:
            try:
                raw_sel.update(self._selectors[selectors_name])
            except KeyError:
                raise SelectorsNotLoaded(action_name, selectors_name) from None
        unknown = plan.selectors - raw_sel.keys()
        if unknown:
            raise UnknownSelectorKey(action_name, unknown, action.selectors_name)
        _check_results(action.steps, set(), raw_sel)
        in_selectors: set[ParamName] = set()
        for key in sorted(plan.selectors):
            names = compiled_template(raw_sel[key]).params
            if undeclared := names - plan.params:
                raise UnknownSelectorParam(action_name, key, raw_sel[key], undeclared)
            in_selectors |= names
        if unregistered := plan.hooks - self._hooks.keys():
            raise HookNotRegistered(action_name, unregistered)

        paths: dict[str, tuple[str, ...]] = {action_name: (action_name,)}
        for step in plan.calls:
            child = self._plan(step.action)
            missing = child.params - step.values.keys()
            extra = step.values.keys() - child.params
            if missing or extra:
                raise BadActionParams(step.action, child.action.params, missing, extra)
            child_link = self._link(step.action, (*stack, action_name))
            if unknown_results := step.outputs.keys() - child.results:
                raise BadActionFile(
                    f"Action {step.action!r} does not save results {sorted(unknown_results)}."
                )
            for name, path in child_link.paths.items():
                paths.setdefault(name, (action_name, *path))
        linked = _LinkedAction(
            raw_sel,
            action.bytes_params - in_selectors,
            action.list_params - in_selectors,
            paths,
        )
        self._linked[action_name] = linked
        return linked

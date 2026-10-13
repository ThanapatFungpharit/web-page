from __future__ import annotations

import math
import mimetypes
import os
import re
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import ClassVar, Self

import aiofiles

from web_page.exceptions import BadActionFile
from web_page.protocols import ElementState, FilePayload
from web_page.types import (
    ActionContext,
    AttributeName,
    HookName,
    KeyTemplate,
    ManifestKey,
    Milliseconds,
    ParamName,
    ParamNames,
    ParamValue,
    ParamValues,
    PathTemplate,
    RawYaml,
    ResultName,
    SelectorKey,
    StepKind,
    Template,
    UrlTemplate,
)
from web_page.utils import check_placeholders, fill

# Filled by Step.__init_subclass__, so defining a step class is what registers it.
_STEPS: dict[StepKind, type[Step]] = {}

# Keys any step's mapping form may carry besides its own; see Step.
_OPTION_KEYS = frozenset({"timeout", "optional"})


@dataclass(frozen=True)
class Step(ABC):
    """One manifest step, e.g. ``- click: submit``.

    Each kind is a single subclass declared as ``class Foo(Step, kind="foo")``;
    it owns parsing, running and selector lookup, so they can't drift apart.
    Subclasses declared without a ``kind`` are shared bases and are not registered.

    Every step also takes two options in its mapping form, which ``parse_steps``
    strips before ``parse`` sees the value: ``timeout`` (milliseconds) overrides
    the page's default for the step's waits, and ``optional: true`` skips the
    step, instead of failing the action, when it times out.
    """

    kind: ClassVar[StepKind]
    takes_timeout: ClassVar[bool] = True
    """False for steps that never wait on the page, so a timeout would do nothing."""

    timeout: Milliseconds | None = field(default=None, kw_only=True)
    optional: bool = field(default=False, kw_only=True)

    def __init_subclass__(
        cls, *, kind: StepKind | None = None, **kwargs: object
    ) -> None:
        super().__init_subclass__(**kwargs)
        if kind is None:
            return
        if kind in _STEPS:
            raise TypeError(f"Step kind {kind!r} is already defined by {_STEPS[kind]}.")
        cls.kind = kind
        _STEPS[kind] = cls

    @classmethod
    @abstractmethod
    def parse(cls, value: RawYaml, params: ParamNames) -> Self:
        """Validate a raw manifest value and build the step from it."""

    @property
    def selector_keys(self) -> tuple[SelectorKey, ...]:
        """The selector keys this step refers to, if any."""
        return ()

    @property
    def result_name(self) -> ResultName | None:
        """The name this step saves a result under, if it retrieves one."""
        return None

    @property
    def templates(self) -> tuple[Template, ...]:
        """The templates this step formats with the params, if any."""
        return ()

    @property
    def data_params(self) -> frozenset[ParamName]:
        """Params this step reads as raw data instead of formatting them."""
        return frozenset()

    @property
    def list_params(self) -> frozenset[ParamName]:
        """Params this step reads as a list of files instead of formatting them."""
        return frozenset()

    @property
    def hook_name(self) -> HookName | None:
        """The hook this step calls, if any."""
        return None

    @property
    def branches(self) -> tuple[tuple[Step, ...], ...]:
        """Nested step lists. Each run of this step runs at most one of them, so
        result names may repeat across them (but not with steps outside)."""
        return ()

    @property
    def _timeout(self) -> dict[str, Milliseconds]:
        """``timeout`` as keyword args, left out when unset."""
        return {} if self.timeout is None else {"timeout": self.timeout}

    @abstractmethod
    async def run(self, ctx: ActionContext) -> None: ...


async def run_steps(steps: Iterable[Step], ctx: ActionContext) -> None:
    """Run steps in order, skipping an ``optional`` one that times out."""
    for step in steps:
        if not step.optional:
            await step.run(ctx)
            continue
        try:
            await step.run(ctx)
        except ctx.timeout_errors:
            # Still save the name, so callers can rely on the key being there.
            if (name := step.result_name) is not None:
                ctx.results[name] = None


def walk(steps: Iterable[Step]) -> Iterator[Step]:
    """Every step, nested ones included, in manifest order."""
    for step in steps:
        yield step
        for branch in step.branches:
            yield from walk(branch)


# Shared bases: declared without a kind, so they are not steps themselves.
@dataclass(frozen=True)
class TargetStep(Step):
    """A step whose manifest value is a selector key, e.g. ``- click: submit``."""

    target: SelectorKey

    @classmethod
    def parse(cls, value: RawYaml, params: ParamNames) -> Self:
        return cls(_selector_key(_unwrap(value, "target"), cls.kind))

    @property
    def selector_keys(self) -> tuple[SelectorKey, ...]:
        return (self.target,)


@dataclass(frozen=True)
class TargetValueStep(TargetStep):
    """A step whose manifest value is ``{target: <key>, value: <template>}``."""

    value: Template

    @classmethod
    def parse(cls, value: RawYaml, params: ParamNames) -> Self:
        target, (template,) = _target_mapping(value, cls.kind, "value")
        return cls(target, _template(template, params, cls.kind))

    @property
    def templates(self) -> tuple[Template, ...]:
        return (self.value,)


@dataclass(frozen=True)
class TargetResultStep(TargetStep):
    """A step whose manifest value is ``{target: <key>, as: <result name>}``."""

    result: ResultName

    @classmethod
    def parse(cls, value: RawYaml, params: ParamNames) -> Self:
        target, (result,) = _target_mapping(value, cls.kind, "as")
        return cls(target, _result_name(result, cls.kind))

    @property
    def result_name(self) -> ResultName:
        return self.result


@dataclass(frozen=True)
class TextStep(TargetResultStep):
    """A text retrieval step; ``exclude`` is a selector key for descendants to
    leave out, such as icons a page renders as ligature text. Its selector must
    be plain CSS, since the page's own ``Element.matches`` tests it."""

    exclude: SelectorKey | None = None

    @classmethod
    def parse(cls, value: RawYaml, params: ParamNames) -> Self:
        step = super().parse(value, params)
        exclude: RawYaml = value.get("exclude") if isinstance(value, dict) else None
        if exclude is None:
            return step
        return replace(step, exclude=_selector_key(exclude, cls.kind))

    @property
    def selector_keys(self) -> tuple[SelectorKey, ...]:
        if self.exclude is None:
            return (self.target,)
        return (self.target, self.exclude)


# Page steps: act on the page itself, so they use no selector key.
@dataclass(frozen=True)
class Goto(Step, kind="goto"):
    url: UrlTemplate

    @classmethod
    def parse(cls, value: RawYaml, params: ParamNames) -> Self:
        return cls(_template(_unwrap(value, "url"), params, cls.kind))

    @property
    def templates(self) -> tuple[Template, ...]:
        return (self.url,)

    async def run(self, ctx: ActionContext) -> None:
        await ctx.page.goto(
            fill(self.url, ctx.params),
            wait_until="domcontentloaded",
            **self._timeout,
        )


@dataclass(frozen=True)
class WaitForUrl(Step, kind="wait_for_url"):
    url: UrlTemplate

    @classmethod
    def parse(cls, value: RawYaml, params: ParamNames) -> Self:
        return cls(_template(_unwrap(value, "url"), params, cls.kind))

    @property
    def templates(self) -> tuple[Template, ...]:
        return (self.url,)

    async def run(self, ctx: ActionContext) -> None:
        await ctx.page.wait_for_url(
            fill(self.url, ctx.params),
            wait_until="domcontentloaded",
            **self._timeout,
        )


@dataclass(frozen=True)
class Wait(Step, kind="wait"):
    takes_timeout = False

    ms: Milliseconds

    @classmethod
    def parse(cls, value: RawYaml, params: ParamNames) -> Self:
        return cls(_milliseconds(value, cls.kind))

    async def run(self, ctx: ActionContext) -> None:
        await ctx.page.wait_for_timeout(self.ms)


# Element steps: act on the element a selector key points at.
@dataclass(frozen=True)
class Click(TargetStep, kind="click"):
    async def run(self, ctx: ActionContext) -> None:
        await ctx.page.locator(ctx.sel[self.target]).click(**self._timeout)


@dataclass(frozen=True)
class Hover(TargetStep, kind="hover"):
    async def run(self, ctx: ActionContext) -> None:
        await ctx.page.locator(ctx.sel[self.target]).hover(**self._timeout)


@dataclass(frozen=True)
class Check(TargetStep, kind="check"):
    async def run(self, ctx: ActionContext) -> None:
        await ctx.page.locator(ctx.sel[self.target]).check(**self._timeout)


@dataclass(frozen=True)
class Uncheck(TargetStep, kind="uncheck"):
    async def run(self, ctx: ActionContext) -> None:
        await ctx.page.locator(ctx.sel[self.target]).uncheck(**self._timeout)


@dataclass(frozen=True)
class WaitFor(TargetStep, kind="wait_for"):
    async def run(self, ctx: ActionContext) -> None:
        await ctx.page.locator(ctx.sel[self.target]).wait_for(
            state="visible", **self._timeout
        )


@dataclass(frozen=True)
class Fill(TargetValueStep, kind="fill"):
    async def run(self, ctx: ActionContext) -> None:
        await ctx.page.locator(ctx.sel[self.target]).fill(
            fill(self.value, ctx.params), **self._timeout
        )


@dataclass(frozen=True)
class Select(TargetValueStep, kind="select"):
    async def run(self, ctx: ActionContext) -> None:
        await ctx.page.locator(ctx.sel[self.target]).select_option(
            fill(self.value, ctx.params), **self._timeout
        )


@dataclass(frozen=True)
class Press(TargetStep, kind="press"):
    key: KeyTemplate

    @classmethod
    def parse(cls, value: RawYaml, params: ParamNames) -> Self:
        target, (key,) = _target_mapping(value, cls.kind, "key")
        return cls(target, _template(key, params, cls.kind))

    @property
    def templates(self) -> tuple[Template, ...]:
        return (self.key,)

    async def run(self, ctx: ActionContext) -> None:
        await ctx.page.locator(ctx.sel[self.target]).press(
            fill(self.key, ctx.params), **self._timeout
        )


# File steps: move files between the page and the local file system.
@dataclass(frozen=True)
class UploadFile:
    """An ``upload`` file the page receives as an in-memory payload.

    Its contents come from exactly one of ``path`` (a local file, which is only
    read: never renamed, copied or changed) and ``data`` (a param holding
    ``bytes``, or a ``str`` sent as UTF-8). ``name`` is the filename the page
    sees; ``mime_type`` defaults to a guess from that name.
    """

    path: PathTemplate | None = None
    data: ParamName | None = None
    name: Template | None = None
    mime_type: Template | None = None

    @property
    def templates(self) -> tuple[Template, ...]:
        return tuple(t for t in (self.path, self.name, self.mime_type) if t is not None)

    async def payload(self, params: ParamValues) -> FilePayload:
        name = None if self.name is None else fill(self.name, params)
        mime_type = None if self.mime_type is None else fill(self.mime_type, params)
        if self.data is None:
            assert self.path is not None
            return await _read_payload(Path(fill(self.path, params)), name, mime_type)
        value = params[self.data]
        assert not isinstance(value, list)
        buffer = value if isinstance(value, bytes) else value.encode()
        return _payload(buffer, self.data if name is None else name, mime_type)


@dataclass(frozen=True)
class FilesParam:
    """``{param: <name>}``: files chosen at run time, as a param holding a list
    of paths and :class:`FilePayload` dicts, or a single path string."""

    param: ParamName


type UploadEntry = PathTemplate | UploadFile | FilesParam
"""A plain path, which the page library reads itself, an in-memory file, or a
param holding files."""

_UPLOAD_FILE_KEYS = frozenset({"path", "data", "name", "mime_type"})


@dataclass(frozen=True)
class Upload(Step, kind="upload"):
    """Give files to the page: by setting an ``<input type=file>`` (``target``),
    or through the file chooser that clicking ``click``, or running ``steps``,
    opens."""

    target: SelectorKey | None
    files: tuple[UploadEntry, ...]
    click: SelectorKey | None = None
    steps: tuple[Step, ...] = ()

    @classmethod
    def parse(cls, value: RawYaml, params: ParamNames) -> Self:
        target, click, steps = _event_source(
            value, cls.kind, "files", ("target", "click", "steps"), params
        )
        assert isinstance(value, dict)
        files: RawYaml = value["files"]
        entries: RawYaml = files if isinstance(files, list) else [files]
        assert isinstance(entries, list)
        return cls(
            target,
            tuple(_upload_entry(e, params, cls.kind) for e in entries),
            click,
            steps,
        )

    @property
    def selector_keys(self) -> tuple[SelectorKey, ...]:
        return tuple(key for key in (self.target, self.click) if key is not None)

    @property
    def templates(self) -> tuple[Template, ...]:
        return tuple(
            t
            for file in self.files
            if not isinstance(file, FilesParam)
            for t in ((file,) if isinstance(file, str) else file.templates)
        )

    @property
    def data_params(self) -> frozenset[ParamName]:
        return frozenset(
            file.data
            for file in self.files
            if isinstance(file, UploadFile) and file.data is not None
        )

    @property
    def list_params(self) -> frozenset[ParamName]:
        return frozenset(
            file.param for file in self.files if isinstance(file, FilesParam)
        )

    @property
    def branches(self) -> tuple[tuple[Step, ...], ...]:
        return (self.steps,) if self.steps else ()

    async def run(self, ctx: ActionContext) -> None:
        # Read everything first, so a missing file fails before the page is touched.
        files = await self._files(ctx.params)
        if self.target is not None:
            locator = ctx.page.locator(ctx.sel[self.target])
            await locator.set_input_files(files, **self._timeout)
            return
        async with ctx.page.expect_file_chooser(**self._timeout) as info:
            await _start_event(ctx, self.click, self.steps)
        chooser = await info.value
        await chooser.set_files(files)

    async def _files(self, params: ParamValues) -> list[str] | list[FilePayload]:
        entries: list[str | FilePayload] = []
        for file in self.files:
            if isinstance(file, str):
                entries.append(fill(file, params))
            elif isinstance(file, UploadFile):
                entries.append(await file.payload(params))
            else:
                entries.extend(_param_files(params[file.param]))
        paths = [entry for entry in entries if isinstance(entry, str)]
        if len(paths) == len(entries):
            return paths
        # Paths and payloads can't be mixed in one call, so plain paths are read
        # into payloads too, keeping their own names.
        return [
            await _read_payload(Path(entry)) if isinstance(entry, str) else entry
            for entry in entries
        ]


@dataclass(frozen=True)
class Download(Step, kind="download"):
    """Click the target, or run ``steps``, and save the download that starts.

    ``to`` is a file path, or a directory (existing, or written with a trailing
    slash) to save into under the browser's suggested filename. ``timeout``
    bounds the wait for the download to start.
    """

    target: SelectorKey | None
    to: PathTemplate
    result: ResultName | None = None
    steps: tuple[Step, ...] = ()

    @classmethod
    def parse(cls, value: RawYaml, params: ParamNames) -> Self:
        target, _, steps = _event_source(
            value, cls.kind, "to", ("target", "steps"), params
        )
        assert isinstance(value, dict)
        result: RawYaml = value.get("as")
        return cls(
            target,
            _template(value["to"], params, cls.kind),
            None if result is None else _result_name(result, cls.kind),
            steps,
        )

    @property
    def selector_keys(self) -> tuple[SelectorKey, ...]:
        return () if self.target is None else (self.target,)

    @property
    def result_name(self) -> ResultName | None:
        return self.result

    @property
    def templates(self) -> tuple[Template, ...]:
        return (self.to,)

    @property
    def branches(self) -> tuple[tuple[Step, ...], ...]:
        return (self.steps,) if self.steps else ()

    async def run(self, ctx: ActionContext) -> None:
        async with ctx.page.expect_download(**self._timeout) as info:
            await _start_event(ctx, self.target, self.steps)
        download = await info.value

        to = fill(self.to, ctx.params)
        path = Path(to)
        if to.endswith(("/", os.sep)) or path.is_dir():
            # Keep only the name, in case a page suggests one with a path in it.
            path /= Path(download.suggested_filename).name
        await download.save_as(str(path))
        if self.result is not None:
            ctx.results[self.result] = str(path.resolve())


async def _start_event(
    ctx: ActionContext, click: SelectorKey | None, steps: tuple[Step, ...]
) -> None:
    """Do what makes the page fire a file event: click ``click``, or run ``steps``."""
    if click is not None:
        await ctx.page.locator(ctx.sel[click]).click()
    else:
        await run_steps(steps, ctx)


def _param_files(value: ParamValue) -> list[str | FilePayload]:
    # WebPage.run has checked the value's shape, so only these two are possible.
    assert not isinstance(value, bytes)
    return [value] if isinstance(value, str) else value


async def _read_payload(
    path: Path, name: str | None = None, mime_type: str | None = None
) -> FilePayload:
    async with aiofiles.open(path, "rb") as f:
        buffer = await f.read()
    return _payload(buffer, path.name if name is None else name, mime_type)


def _payload(buffer: bytes, name: str, mime_type: str | None) -> FilePayload:
    if mime_type is None:
        mime_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    return {"name": name, "mimeType": mime_type, "buffer": buffer}


# Retrieval steps: save a value under a result name; WebPage.run returns them all.
@dataclass(frozen=True)
class GetUrl(Step, kind="get_url"):
    takes_timeout = False

    result: ResultName

    @classmethod
    def parse(cls, value: RawYaml, params: ParamNames) -> Self:
        return cls(_result_name(_unwrap(value, "as"), cls.kind))

    @property
    def result_name(self) -> ResultName:
        return self.result

    async def run(self, ctx: ActionContext) -> None:
        ctx.results[self.result] = ctx.page.url


# Reads text the way a user sees it, like innerText, but skipping descendants
# that match `exclude`, and with whitespace collapsed. It only reads the live
# element, so the page is never changed.
_TEXT_OF = r"""
const textOf = (root, exclude) => {
  const parts = [];
  const visit = (node) => {
    if (node.nodeType === Node.TEXT_NODE) {
      parts.push(node.data);
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE || node.matches(exclude)) return;
    const display = getComputedStyle(node).display;
    if (display === "none") return;
    const block = node.tagName === "BR" || !display.startsWith("inline");
    if (block) parts.push(" ");
    node.childNodes.forEach(visit);
    if (block) parts.push(" ");
  };
  root.childNodes.forEach(visit);
  return parts.join("").replace(/\s+/g, " ").trim();
};
"""
_TEXT_JS = "(el, exclude) => {" + _TEXT_OF + "return textOf(el, exclude); }"
_ALL_TEXTS_JS = (
    "(els, exclude) => {" + _TEXT_OF + "return els.map(el => textOf(el, exclude)); }"
)


@dataclass(frozen=True)
class GetText(TextStep, kind="get_text"):
    async def run(self, ctx: ActionContext) -> None:
        locator = ctx.page.locator(ctx.sel[self.target])
        if self.exclude is None:
            text = await locator.inner_text(**self._timeout)
        else:
            exclude = ctx.sel[self.exclude]
            text = str(await locator.evaluate(_TEXT_JS, exclude, **self._timeout))
        ctx.results[self.result] = text


@dataclass(frozen=True)
class GetAllTexts(TextStep, kind="get_all_texts"):
    # Reads whatever matches right now, without waiting for anything.
    takes_timeout = False

    async def run(self, ctx: ActionContext) -> None:
        locator = ctx.page.locator(ctx.sel[self.target])
        if self.exclude is None:
            ctx.results[self.result] = await locator.all_inner_texts()
            return
        texts = await locator.evaluate_all(_ALL_TEXTS_JS, ctx.sel[self.exclude])
        assert isinstance(texts, list)
        ctx.results[self.result] = [str(text) for text in texts]


@dataclass(frozen=True)
class GetValue(TargetResultStep, kind="get_value"):
    async def run(self, ctx: ActionContext) -> None:
        locator = ctx.page.locator(ctx.sel[self.target])
        ctx.results[self.result] = await locator.input_value(**self._timeout)


@dataclass(frozen=True)
class GetAttribute(TargetResultStep, kind="get_attribute"):
    attribute: AttributeName

    @classmethod
    def parse(cls, value: RawYaml, params: ParamNames) -> Self:
        target, (attribute, result) = _target_mapping(
            value, cls.kind, "attribute", "as"
        )
        return cls(target, _result_name(result, cls.kind), _string(attribute, cls.kind))

    async def run(self, ctx: ActionContext) -> None:
        locator = ctx.page.locator(ctx.sel[self.target])
        ctx.results[self.result] = await locator.get_attribute(
            self.attribute, **self._timeout
        )


# Control steps: decide at run time what runs.
@dataclass(frozen=True)
class ElementIs:
    """``visible``, ``hidden`` or ``exists``: checked at once, or with a
    ``timeout``, true if the target reaches that state within it."""

    target: SelectorKey
    state: ElementState
    timeout: Milliseconds | None = None

    async def holds(self, ctx: ActionContext) -> bool:
        locator = ctx.page.locator(ctx.sel[self.target])
        if self.timeout is None:
            if self.state == "attached":
                return await locator.count() > 0
            return await locator.is_visible() == (self.state == "visible")
        try:
            await locator.wait_for(state=self.state, timeout=self.timeout)
        except ctx.timeout_errors:
            return False
        return True


@dataclass(frozen=True)
class ParamIsSet:
    """``param``: true if the param is not empty."""

    param: ParamName

    async def holds(self, ctx: ActionContext) -> bool:
        return bool(ctx.params[self.param])


@dataclass(frozen=True)
class ParamEquals:
    """``equals``: true if the param equals the formatted value."""

    param: ParamName
    value: Template

    async def holds(self, ctx: ActionContext) -> bool:
        return ctx.params[self.param] == fill(self.value, ctx.params)


@dataclass(frozen=True)
class UrlIs:
    """``url``: true if the page's whole URL matches the formatted glob, where
    ``**`` matches anything and ``*`` anything but ``/``."""

    url: UrlTemplate

    async def holds(self, ctx: ActionContext) -> bool:
        return _glob(fill(self.url, ctx.params)).fullmatch(ctx.page.url) is not None


def _glob(glob: str) -> re.Pattern[str]:
    parts = re.split(r"(\*\*|\*)", glob)
    return re.compile(
        "".join(
            ".*" if part == "**" else "[^/]*" if part == "*" else re.escape(part)
            for part in parts
        )
    )


type Condition = ElementIs | ParamIsSet | ParamEquals | UrlIs

_ELEMENT_STATES: dict[ManifestKey, ElementState] = {
    "visible": "visible",
    "hidden": "hidden",
    "exists": "attached",
}
_CONDITIONS = (*_ELEMENT_STATES, "param", "equals", "url")


@dataclass(frozen=True)
class If(Step, kind="if"):
    # Waits belong to the condition and to the steps in each branch.
    takes_timeout = False

    condition: Condition
    then: tuple[Step, ...]
    otherwise: tuple[Step, ...] = ()

    @classmethod
    def parse(cls, value: RawYaml, params: ParamNames) -> Self:
        if isinstance(value, dict) and "then" in value:
            keys = value.keys() - {"then", "else"}
            if len(keys) == 1 and (key := next(iter(keys))) in _CONDITIONS:
                assert isinstance(key, str)
                return cls(
                    _condition(key, value[key], params, cls.kind),
                    parse_step_list(value["then"], params),
                    parse_step_list(value.get("else", []), params),
                )
        raise BadActionFile(
            f"Step {cls.kind!r} needs a mapping with 'then', an optional 'else' and "
            f"one condition out of {list(_CONDITIONS)}, not {value!r}."
        )

    @property
    def selector_keys(self) -> tuple[SelectorKey, ...]:
        if isinstance(self.condition, ElementIs):
            return (self.condition.target,)
        return ()

    @property
    def templates(self) -> tuple[Template, ...]:
        if isinstance(self.condition, ParamEquals):
            return (self.condition.value,)
        if isinstance(self.condition, UrlIs):
            return (self.condition.url,)
        return ()

    @property
    def branches(self) -> tuple[tuple[Step, ...], ...]:
        return (self.then, self.otherwise)

    async def run(self, ctx: ActionContext) -> None:
        holds = await self.condition.holds(ctx)
        await run_steps(self.then if holds else self.otherwise, ctx)


@dataclass(frozen=True)
class Call(Step, kind="call"):
    """Run a hook registered with ``WebPage.register``."""

    takes_timeout = False

    hook: HookName

    @classmethod
    def parse(cls, value: RawYaml, params: ParamNames) -> Self:
        hook = _string(_unwrap(value, "hook"), cls.kind)
        if not hook:
            raise BadActionFile(f"Step {cls.kind!r} needs a non-empty hook name.")
        return cls(hook)

    @property
    def hook_name(self) -> HookName:
        return self.hook

    async def run(self, ctx: ActionContext) -> None:
        await ctx.hooks[self.hook](ctx)


def _condition(
    key: ManifestKey, value: RawYaml, params: ParamNames, kind: StepKind
) -> Condition:
    if key in _ELEMENT_STATES:
        if isinstance(value, dict):
            target, (timeout,) = _target_mapping(value, kind, "timeout")
            ms = None if timeout is None else _milliseconds(timeout, kind)
        else:
            target, ms = _selector_key(value, kind), None
        return ElementIs(target, _ELEMENT_STATES[key], ms)
    if key == "param":
        return ParamIsSet(_declared_param(value, params, kind))
    if key == "equals":
        if not isinstance(value, dict) or value.keys() != {"param", "value"}:
            raise BadActionFile(
                f"Step {kind!r} needs 'equals' to be a mapping with 'param' and "
                f"'value' keys, not {value!r}."
            )
        return ParamEquals(
            _declared_param(value["param"], params, kind),
            _template(value["value"], params, kind),
        )
    return UrlIs(_template(value, params, kind))


# Checkers: validate a raw manifest value and return it with its precise type.
def _string(value: RawYaml, kind: StepKind) -> str:
    if not isinstance(value, str):
        raise BadActionFile(f"Step {kind!r} needs a string value, not {value!r}.")
    return value


def _selector_key(value: RawYaml, kind: StepKind) -> SelectorKey:
    return _string(value, kind)


def _template(value: RawYaml, params: ParamNames, kind: StepKind) -> Template:
    template = _string(value, kind)
    check_placeholders(template, params, kind)
    return template


def _milliseconds(value: RawYaml, kind: StepKind) -> Milliseconds:
    # bool is an int subclass, but `wait: true` is surely a mistake.
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not (value >= 0 and math.isfinite(value))
    ):
        raise BadActionFile(
            f"Step {kind!r} needs a non-negative number of milliseconds, not {value!r}."
        )
    return value


def _declared_param(value: RawYaml, params: ParamNames, kind: StepKind) -> ParamName:
    name = _string(value, kind)
    if name not in params:
        raise BadActionFile(
            f"Step {kind!r} reads undeclared param {name!r}; declared: {list(params)}."
        )
    return name


def _unwrap(value: RawYaml, key: ManifestKey) -> RawYaml:
    """``{<key>: x}`` as plain ``x``: the mapping form a step takes to carry options."""
    if isinstance(value, dict) and value.keys() == {key}:
        return value[key]
    return value


def _upload_entry(value: RawYaml, params: ParamNames, kind: StepKind) -> UploadEntry:
    """A path template, ``{param}``, or ``{path | data, name?, mime_type?}``."""
    if isinstance(value, str):
        return _template(value, params, kind)
    if isinstance(value, dict) and value.keys() == {"param"}:
        return FilesParam(_declared_param(value["param"], params, kind))
    if (
        not isinstance(value, dict)
        or not value.keys() <= _UPLOAD_FILE_KEYS
        or ("path" in value) == ("data" in value)
    ):
        raise BadActionFile(
            f"Step {kind!r} needs each file to be a path or a mapping with 'param', "
            f"or with either 'path' or 'data' (plus optional 'name' and "
            f"'mime_type'), not {value!r}."
        )

    def template(key: ManifestKey) -> Template | None:
        return _template(value[key], params, kind) if key in value else None

    path, name, mime_type = template("path"), template("name"), template("mime_type")
    if path is not None:
        # Nothing to override, so let the page library read the file itself.
        if name is None and mime_type is None:
            return path
        return UploadFile(path=path, name=name, mime_type=mime_type)

    data = _declared_param(value["data"], params, kind)
    if name is None:
        raise BadActionFile(f"Step {kind!r} needs a 'name' for data {data!r}.")
    return UploadFile(data=data, name=name, mime_type=mime_type)


def _event_source(
    value: RawYaml,
    kind: StepKind,
    required: ManifestKey,
    sources: tuple[ManifestKey, ...],
    params: ParamNames,
) -> tuple[SelectorKey | None, SelectorKey | None, tuple[Step, ...]]:
    """Check a file step has ``required`` and exactly one of ``sources``, and
    return its ``target``, ``click`` and ``steps`` (unset ones as None or ())."""
    if isinstance(value, dict) and required in value:
        given = [key for key in sources if key in value]
        if len(given) == 1:
            (key,) = given
            if key == "steps":
                return None, None, parse_step_list(value["steps"], params)
            selector = _selector_key(value[key], kind)
            if key == "target":
                return selector, None, ()
            return None, selector, ()
    *first, last = sources
    raise BadActionFile(
        f"Step {kind!r} needs a mapping with {required!r} and one of "
        f"{', '.join(map(repr, first))} or {last!r}, not {value!r}."
    )


def _target_mapping(
    value: RawYaml, kind: StepKind, *fields: ManifestKey
) -> tuple[SelectorKey, tuple[RawYaml, ...]]:
    """Split ``{target: <key>, <field>: ...}``; the caller checks the field values."""
    if isinstance(value, dict):
        target: RawYaml = value.get("target")
        if isinstance(target, str):
            return target, tuple(value.get(f) for f in fields)
    *first, last = ("target", *fields)
    raise BadActionFile(
        f"Step {kind!r} needs a mapping with string {', '.join(map(repr, first))} "
        f"and {last!r} keys, not {value!r}."
    )


def _result_name(value: RawYaml, kind: StepKind) -> ResultName:
    name = _string(value, kind)
    if not name:
        raise BadActionFile(f"Step {kind!r} needs a non-empty result name.")
    return name


def _split_options(
    value: RawYaml, step_type: type[Step]
) -> tuple[RawYaml, dict[str, Milliseconds | bool]]:
    """Take the shared ``timeout`` and ``optional`` keys out of a step's value."""
    if not isinstance(value, dict) or not value.keys() & _OPTION_KEYS:
        return value, {}
    kind = step_type.kind
    options: dict[str, Milliseconds | bool] = {}
    if "timeout" in value:
        if not step_type.takes_timeout:
            raise BadActionFile(f"Step {kind!r} does not take a 'timeout'.")
        options["timeout"] = _milliseconds(value["timeout"], kind)
    if "optional" in value:
        optional: RawYaml = value["optional"]
        if not isinstance(optional, bool):
            raise BadActionFile(
                f"Step {kind!r} needs 'optional' to be true or false, not {optional!r}."
            )
        options["optional"] = optional
    rest = {k: v for k, v in value.items() if k not in _OPTION_KEYS}
    return rest, options


def parse_step_list(raw: RawYaml, params: ParamNames) -> tuple[Step, ...]:
    """Parse a list of steps, such as one nested in an ``if``.

    Unlike ``parse_steps`` it doesn't check result names, so a step nesting
    lists can parse them in its ``parse``, and ``parse_steps`` checks them all.
    """
    if not isinstance(raw, list):
        raise BadActionFile(
            f"Action steps should be a list of single-key mappings, not {raw!r}."
        )

    steps: list[Step] = []
    item: RawYaml
    for item in raw:
        if not isinstance(item, dict) or len(item) != 1:
            raise BadActionFile(
                f"Each step should be a single-key mapping, not {item!r}."
            )
        kind: RawYaml
        value: RawYaml
        ((kind, value),) = item.items()

        step_type = _STEPS.get(kind) if isinstance(kind, str) else None
        if step_type is None:
            raise BadActionFile(
                f"Unknown step {kind!r}; expected one of {sorted(_STEPS)}."
            )
        value, options = _split_options(value, step_type)
        step = step_type.parse(value, params)
        steps.append(replace(step, **options) if options else step)
    return tuple(steps)


def _check_results(steps: Iterable[Step], taken: set[ResultName]) -> None:
    """Reject a result name saved twice, since the later one would overwrite the
    earlier. Alternative branches may share a name: only one of them runs."""
    for step in steps:
        if (result := step.result_name) is not None:
            if result in taken:
                raise BadActionFile(
                    f"Step {step.kind!r} saves result {result!r}, which an earlier "
                    f"step already saves."
                )
            taken.add(result)
        in_branches: set[ResultName] = set()
        for branch in step.branches:
            seen = set(taken)
            _check_results(branch, seen)
            in_branches |= seen
        taken |= in_branches


def parse_steps(raw: RawYaml, params: ParamNames) -> tuple[Step, ...]:
    """Steps are an ordered list of single-key mappings, e.g. ``- click: submit``.

    Steps such as ``if`` nest more step lists, which are parsed the same way.
    """
    steps = parse_step_list(raw, params)
    _check_results(steps, set())
    return steps

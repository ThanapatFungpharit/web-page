from __future__ import annotations

import math
import mimetypes
import os
import re
from abc import ABC, abstractmethod
from asyncio import get_running_loop, sleep
from asyncio import timeout as deadline
from collections.abc import Iterable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import ClassVar, Self, TypedDict

import aiofiles

from web_page.exceptions import ActionFailed, BadActionFile, BadResultValue
from web_page.protocols import ElementState, FilePayload, MouseButton
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
    Results,
    SelectorKey,
    StepKind,
    Template,
    UrlTemplate,
)
from web_page.utils import (
    check_placeholders,
    fill,
    is_result_value,
    prepare_template,
    result_placeholders,
)

# Filled by Step.__init_subclass__, so defining a step class is what registers it.
_STEPS: dict[StepKind, type[Step]] = {}

# Keys any step's mapping form may carry besides its own; see Step.
_OPTION_KEYS = frozenset({"timeout", "optional"})
_DEADLINE: ContextVar[float | None] = ContextVar("web_page_deadline", default=None)


class _TimeoutArg(TypedDict, total=False):
    timeout: Milliseconds


def _timeout_arg(configured: Milliseconds | None = None) -> _TimeoutArg:
    expires = _DEADLINE.get()
    if expires is None:
        return {} if configured is None else {"timeout": configured}
    remaining = (expires - get_running_loop().time()) * 1000
    if remaining <= 0:
        raise TimeoutError("Step deadline expired.")
    return {"timeout": min(configured, remaining) if configured else remaining}


_MOUSE_BUTTONS: tuple[MouseButton, ...] = ("left", "right", "middle")
_WAIT_STATES: tuple[ElementState, ...] = ("visible", "hidden", "attached", "detached")


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
    total_timeout: ClassVar[bool] = False
    reads_after_branches: ClassVar[bool] = False

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
    def result_names(self) -> tuple[ResultName, ...]:
        return () if self.result_name is None else (self.result_name,)

    @property
    def results_read(self) -> tuple[ResultName, ...]:
        """Results saved by earlier steps that this step reads, if any."""
        return ()

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
    def _timeout(self) -> _TimeoutArg:
        """``timeout`` as keyword args, left out when unset."""
        return _timeout_arg(self.timeout)

    @abstractmethod
    async def run(self, ctx: ActionContext) -> None: ...


async def run_steps(steps: Iterable[Step], ctx: ActionContext) -> None:
    """Run steps in order, skipping an ``optional`` one that times out."""
    for step in steps:
        try:
            if step.total_timeout and step.timeout:
                expires = get_running_loop().time() + step.timeout / 1000
                inherited = _DEADLINE.get()
                if inherited is not None:
                    expires = min(expires, inherited)
                token = _DEADLINE.set(expires)
                try:
                    async with deadline(max(0, expires - get_running_loop().time())):
                        await step.run(ctx)
                finally:
                    _DEADLINE.reset(token)
            else:
                await step.run(ctx)
        except (TimeoutError, *ctx.timeout_errors):
            if not step.optional:
                raise
            # Still save the name, so callers can rely on the key being there.
            for name in step.result_names:
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
        target, (result, exclude) = _target_mapping(value, cls.kind, "as", "exclude")
        return cls(
            target,
            _result_name(result, cls.kind),
            None if exclude is None else _selector_key(exclude, cls.kind),
        )

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
            fill(self.url, ctx.params, ctx.results),
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
            fill(self.url, ctx.params, ctx.results),
            wait_until="domcontentloaded",
            **self._timeout,
        )


@dataclass(frozen=True)
class Wait(Step, kind="wait"):
    takes_timeout = False

    ms: Milliseconds

    @classmethod
    def parse(cls, value: RawYaml, params: ParamNames) -> Self:
        return cls(_milliseconds(_unwrap(value, "ms"), cls.kind))

    async def run(self, ctx: ActionContext) -> None:
        await ctx.page.wait_for_timeout(self.ms)


# Element steps: act on the element a selector key points at.
@dataclass(frozen=True)
class Click(TargetStep, kind="click"):
    """``click: <key>``, or ``{target: <key>, button: left | right | middle}``."""

    button: MouseButton | None = None

    @classmethod
    def parse(cls, value: RawYaml, params: ParamNames) -> Self:
        if not isinstance(value, dict):
            return super().parse(value, params)
        target, (button,) = _target_mapping(value, cls.kind, "button")
        if button is None:
            return cls(target)
        return cls(target, _choice(button, _MOUSE_BUTTONS, cls.kind, "button"))

    async def run(self, ctx: ActionContext) -> None:
        locator = ctx.page.locator(ctx.sel[self.target])
        # Passed only when set, so a page library without `button` still works.
        if self.button is None:
            await locator.click(**self._timeout)
        else:
            await locator.click(button=self.button, **self._timeout)


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
    """``wait_for: <key>`` waits until the element is visible;
    ``{target: <key>, state: <state>}`` waits for another state, such as
    ``hidden`` or ``detached`` for an element to go away."""

    state: ElementState = "visible"

    @classmethod
    def parse(cls, value: RawYaml, params: ParamNames) -> Self:
        if not isinstance(value, dict):
            return super().parse(value, params)
        target, (state,) = _target_mapping(value, cls.kind, "state")
        if state is None:
            return cls(target)
        return cls(target, _choice(state, _WAIT_STATES, cls.kind, "state"))

    async def run(self, ctx: ActionContext) -> None:
        await ctx.page.locator(ctx.sel[self.target]).wait_for(
            state=self.state, **self._timeout
        )


@dataclass(frozen=True)
class Fill(TargetValueStep, kind="fill"):
    async def run(self, ctx: ActionContext) -> None:
        await ctx.page.locator(ctx.sel[self.target]).fill(
            fill(self.value, ctx.params, ctx.results), **self._timeout
        )


@dataclass(frozen=True)
class Select(TargetValueStep, kind="select"):
    async def run(self, ctx: ActionContext) -> None:
        await ctx.page.locator(ctx.sel[self.target]).select_option(
            fill(self.value, ctx.params, ctx.results), **self._timeout
        )


@dataclass(frozen=True)
class Press(Step, kind="press"):
    """``{target: <key>, key: <template>}`` presses a key on the element;
    without ``target``, on the page, wherever the focus is."""

    target: SelectorKey | None
    key: KeyTemplate

    def __post_init__(self) -> None:
        # The keyboard doesn't wait for anything, so a timeout would do nothing.
        if self.target is None and self.timeout is not None:
            raise BadActionFile(
                f"Step {self.kind!r} without a 'target' does not take a 'timeout'."
            )

    @classmethod
    def parse(cls, value: RawYaml, params: ParamNames) -> Self:
        if isinstance(value, dict) and value.keys() == {"key"}:
            return cls(None, _template(value["key"], params, cls.kind))
        target, (key,) = _target_mapping(value, cls.kind, "key")
        return cls(target, _template(key, params, cls.kind))

    @property
    def selector_keys(self) -> tuple[SelectorKey, ...]:
        return () if self.target is None else (self.target,)

    @property
    def templates(self) -> tuple[Template, ...]:
        return (self.key,)

    async def run(self, ctx: ActionContext) -> None:
        key = fill(self.key, ctx.params, ctx.results)
        if self.target is None:
            await ctx.page.keyboard.press(key)
            return
        await ctx.page.locator(ctx.sel[self.target]).press(key, **self._timeout)


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

    async def payload(
        self, params: ParamValues, results: Results | None = None
    ) -> FilePayload:
        name = None if self.name is None else fill(self.name, params, results)
        mime_type = (
            None if self.mime_type is None else fill(self.mime_type, params, results)
        )
        if self.data is None:
            assert self.path is not None
            return await _read_payload(
                Path(fill(self.path, params, results)), name, mime_type
            )
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
    total_timeout = True

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
        files = await self._files(ctx.params, ctx.results)
        if self.target is not None:
            locator = ctx.page.locator(ctx.sel[self.target])
            await locator.set_input_files(files, **self._timeout)
            return
        async with ctx.page.expect_file_chooser(**self._timeout) as info:
            await _start_event(ctx, self.click, self.steps)
        chooser = await info.value
        await chooser.set_files(files, **_timeout_arg())

    async def _files(
        self, params: ParamValues, results: Results | None = None
    ) -> list[str] | list[FilePayload]:
        entries: list[str | FilePayload] = []
        for file in self.files:
            if isinstance(file, str):
                entries.append(fill(file, params, results))
            elif isinstance(file, UploadFile):
                entries.append(await file.payload(params, results))
            else:
                entries.extend(_param_files(params[file.param]))
        paths = [entry for entry in entries if isinstance(entry, str)]
        if len(paths) == len(entries):
            # Preserve path-based streaming, but check files before the trigger.
            for path in paths:
                async with aiofiles.open(path, "rb") as source:
                    await source.read(1)
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
    bounds the entire step, including its trigger and saving the file.
    """

    target: SelectorKey | None
    to: PathTemplate
    result: ResultName | None = None
    steps: tuple[Step, ...] = ()
    total_timeout = True
    reads_after_branches = True

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

        to = fill(self.to, ctx.params, ctx.results)
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
        await ctx.page.locator(ctx.sel[click]).click(**_timeout_arg())
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
      const visibility = getComputedStyle(node.parentElement).visibility;
      if (visibility !== "hidden" && visibility !== "collapse") parts.push(node.data);
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
    # Keep snapshot behavior by default; an explicit timeout waits for a match.
    total_timeout = True

    async def run(self, ctx: ActionContext) -> None:
        locator = ctx.page.locator(ctx.sel[self.target])
        if self.timeout is not None:
            while await locator.count() == 0:
                await sleep(0.025)
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
            await locator.wait_for(state=self.state, **_timeout_arg(self.timeout))
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
        return ctx.params[self.param] == fill(self.value, ctx.params, ctx.results)


@dataclass(frozen=True)
class ResultIsSet:
    """``result``: true if an earlier step saved the result and it is not empty
    or ``None``."""

    result: ResultName

    async def holds(self, ctx: ActionContext) -> bool:
        return bool(ctx.results.get(self.result))


@dataclass(frozen=True)
class ResultEquals:
    """``equals`` with ``result``: true if the saved result equals the
    formatted value."""

    result: ResultName
    value: Template

    async def holds(self, ctx: ActionContext) -> bool:
        return ctx.results.get(self.result) == fill(self.value, ctx.params, ctx.results)


@dataclass(frozen=True)
class UrlIs:
    """``url``: true if the page's whole URL matches the formatted glob, where
    ``**`` matches anything and ``*`` anything but ``/``."""

    url: UrlTemplate

    async def holds(self, ctx: ActionContext) -> bool:
        return (
            _glob(fill(self.url, ctx.params, ctx.results)).fullmatch(ctx.page.url)
            is not None
        )


def _glob(glob: str) -> re.Pattern[str]:
    # Match Playwright's URL glob semantics, without requiring Playwright.
    tokens: list[str] = []
    group = False
    i = 0
    while i < len(glob):
        char = glob[i]
        if char == "\\" and i + 1 < len(glob):
            i += 1
            tokens.append(re.escape(glob[i]))
        elif char == "*":
            start = i
            while i + 1 < len(glob) and glob[i + 1] == "*":
                i += 1
            if i > start:
                if i + 1 < len(glob) and glob[i + 1] == "/":
                    tokens.append(
                        "(?:.+/)?" if start > 0 and glob[start - 1] == "/" else ".*/"
                    )
                    i += 1
                else:
                    tokens.append(".*")
            else:
                tokens.append("[^/]*")
        elif char == "{":
            group = True
            tokens.append("(")
        elif char == "}":
            group = False
            tokens.append(")")
        elif char == "," and group:
            tokens.append("|")
        else:
            tokens.append(re.escape(char))
        i += 1
    return re.compile("".join(tokens))


type Condition = (
    ElementIs | ParamIsSet | ParamEquals | ResultIsSet | ResultEquals | UrlIs
)

_ELEMENT_STATES: dict[ManifestKey, ElementState] = {
    "visible": "visible",
    "hidden": "hidden",
    "exists": "attached",
}
_CONDITIONS = (*_ELEMENT_STATES, "param", "result", "equals", "url")


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
        if isinstance(self.condition, ParamEquals | ResultEquals):
            return (self.condition.value,)
        if isinstance(self.condition, UrlIs):
            return (self.condition.url,)
        return ()

    @property
    def results_read(self) -> tuple[ResultName, ...]:
        if isinstance(self.condition, ResultIsSet | ResultEquals):
            return (self.condition.result,)
        return ()

    @property
    def branches(self) -> tuple[tuple[Step, ...], ...]:
        return (self.then, self.otherwise)

    async def run(self, ctx: ActionContext) -> None:
        holds = await self.condition.holds(ctx)
        await run_steps(self.then if holds else self.otherwise, ctx)


@dataclass(frozen=True)
class Fail(Step, kind="fail"):
    """Stop the action with ``ActionFailed`` and the formatted message, such as
    in an ``if`` that has found an error on the page."""

    takes_timeout = False

    message: Template

    @classmethod
    def parse(cls, value: RawYaml, params: ParamNames) -> Self:
        return cls(_template(_unwrap(value, "message"), params, cls.kind))

    @property
    def templates(self) -> tuple[Template, ...]:
        return (self.message,)

    async def run(self, ctx: ActionContext) -> None:
        raise ActionFailed(fill(self.message, ctx.params, ctx.results), ctx.results)


@dataclass(frozen=True)
class Call(Step, kind="call"):
    """Run a hook registered with ``WebPage.register``."""

    takes_timeout = False

    hook: HookName
    result: ResultName | None = None
    outputs: tuple[ResultName, ...] = ()

    @classmethod
    def parse(cls, value: RawYaml, params: ParamNames) -> Self:
        if isinstance(value, dict):
            _mapping_keys(value, {"hook", "as", "results"}, cls.kind)
            hook = _string(value.get("hook"), cls.kind)
            result = None if "as" not in value else _result_name(value["as"], cls.kind)
            raw = value.get("results", [])
            if not isinstance(raw, list):
                raise BadActionFile(
                    "Step 'call' results must be a list of result names."
                )
            outputs = tuple(_result_name(name, cls.kind) for name in raw)
            names = (*outputs, *((result,) if result is not None else ()))
            if len(set(names)) != len(names):
                raise BadActionFile("Step 'call' has duplicate result names.")
        else:
            hook, result, outputs = _string(value, cls.kind), None, ()
        if not hook:
            raise BadActionFile(f"Step {cls.kind!r} needs a non-empty hook name.")
        return cls(hook, result, outputs)

    @property
    def result_name(self) -> ResultName | None:
        return self.result

    @property
    def result_names(self) -> tuple[ResultName, ...]:
        return (*self.outputs, *super().result_names)

    @property
    def hook_name(self) -> HookName:
        return self.hook

    async def run(self, ctx: ActionContext) -> None:
        value = await ctx.hooks[self.hook](ctx)
        if self.result is not None:
            if not is_result_value(value):
                raise BadResultValue(
                    self.result, value, "a string, list of strings or None"
                )
            ctx.results[self.result] = value
        for name in self.outputs:
            ctx.results.setdefault(name, None)
            if not is_result_value(ctx.results[name]):
                raise BadResultValue(
                    name, ctx.results[name], "a string, list of strings or None"
                )


@dataclass(frozen=True)
class RunAction(Step, kind="run"):
    """Call another loaded action under the current run's lock."""

    action: str
    values: dict[str, Template] = field(default_factory=dict)
    outputs: dict[str, ResultName] = field(default_factory=dict)
    total_timeout = True

    @classmethod
    def parse(cls, value: RawYaml, params: ParamNames) -> Self:
        if isinstance(value, str):
            return cls(value)
        if not isinstance(value, dict):
            raise BadActionFile("Step 'run' needs an action name or mapping.")
        _mapping_keys(value, {"action", "params", "results"}, cls.kind)
        action = _string(value.get("action"), cls.kind)
        values, outputs = value.get("params", {}), value.get("results", {})
        if not isinstance(values, dict) or not all(isinstance(k, str) for k in values):
            raise BadActionFile(
                "Step 'run' params must be a mapping of names to templates."
            )
        if not isinstance(outputs, dict) or not all(
            isinstance(k, str) for k in outputs
        ):
            raise BadActionFile(
                "Step 'run' results must map child result names to output names."
            )
        parsed = {
            _result_name(k, cls.kind): _result_name(v, cls.kind)
            for k, v in outputs.items()
        }
        if len(set(parsed.values())) != len(parsed):
            raise BadActionFile("Step 'run' has duplicate output names.")
        return cls(
            action,
            {k: _template(v, params, cls.kind) for k, v in values.items()},
            parsed,
        )

    @property
    def templates(self) -> tuple[Template, ...]:
        return tuple(self.values.values())

    @property
    def result_names(self) -> tuple[ResultName, ...]:
        return tuple(self.outputs.values())

    async def run(self, ctx: ActionContext) -> None:
        if ctx.run_action is None:
            raise BadActionFile("Step 'run' requires a WebPage action runner.")
        values = {k: fill(v, ctx.params, ctx.results) for k, v in self.values.items()}
        results = await ctx.run_action(self.action, values)
        for child, output in self.outputs.items():
            ctx.results[output] = results.get(child)


@dataclass(frozen=True)
class Repeat(Step, kind="repeat"):
    """Repeat a bounded number of times; an optional until condition polls."""

    times: int
    steps: tuple[Step, ...]
    until: Condition | None = None
    interval: Milliseconds = 0
    total_timeout = True
    reads_after_branches = True
    _local_results: frozenset[ResultName] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_local_results",
            frozenset(name for step in walk(self.steps) for name in step.result_names),
        )

    @classmethod
    def parse(cls, value: RawYaml, params: ParamNames) -> Self:
        if not isinstance(value, dict):
            raise BadActionFile("Step 'repeat' needs a mapping.")
        _mapping_keys(value, {"times", "steps", "until", "interval"}, cls.kind)
        condition = value.get("until")
        if condition is not None:
            if (
                not isinstance(condition, dict)
                or len(condition) != 1
                or next(iter(condition)) not in _CONDITIONS
            ):
                raise BadActionFile("Step 'repeat' until needs exactly one condition.")
            key, raw = next(iter(condition.items()))
            condition = _condition(key, raw, params, cls.kind)
        return cls(
            _positive_integer(value.get("times"), cls.kind),
            parse_step_list(value.get("steps"), params),
            condition,
            _milliseconds(value.get("interval", 0), cls.kind),
        )

    @property
    def branches(self) -> tuple[tuple[Step, ...], ...]:
        return (self.steps,)

    @property
    def selector_keys(self) -> tuple[SelectorKey, ...]:
        return (self.until.target,) if isinstance(self.until, ElementIs) else ()

    @property
    def templates(self) -> tuple[Template, ...]:
        if isinstance(self.until, ParamEquals | ResultEquals):
            return (self.until.value,)
        return (self.until.url,) if isinstance(self.until, UrlIs) else ()

    @property
    def results_read(self) -> tuple[ResultName, ...]:
        return (
            (self.until.result,)
            if isinstance(self.until, ResultIsSet | ResultEquals)
            else ()
        )

    async def run(self, ctx: ActionContext) -> None:
        for index in range(self.times):
            for name in self._local_results:
                ctx.results.pop(name, None)
            await run_steps(self.steps, ctx)
            if self.until is not None and await self.until.holds(ctx):
                return
            if index + 1 < self.times and self.interval:
                await sleep(self.interval / 1000)
        if self.until is not None:
            raise ActionFailed(
                f"Repeat condition was not met after {self.times} iterations.",
                ctx.results,
            )


@dataclass(frozen=True)
class Retry(Step, kind="retry"):
    attempts: int
    steps: tuple[Step, ...]
    interval: Milliseconds = 0
    total_timeout = True

    @classmethod
    def parse(cls, value: RawYaml, params: ParamNames) -> Self:
        if not isinstance(value, dict):
            raise BadActionFile("Step 'retry' needs a mapping.")
        _mapping_keys(value, {"attempts", "steps", "interval"}, cls.kind)
        return cls(
            _positive_integer(value.get("attempts"), cls.kind),
            parse_step_list(value.get("steps"), params),
            _milliseconds(value.get("interval", 0), cls.kind),
        )

    @property
    def branches(self) -> tuple[tuple[Step, ...], ...]:
        return (self.steps,)

    async def run(self, ctx: ActionContext) -> None:
        before = dict(ctx.results)
        for attempt in range(self.attempts):
            ctx.results.clear()
            ctx.results.update(before)
            try:
                await run_steps(self.steps, ctx)
                return
            except (TimeoutError, ActionFailed, *ctx.timeout_errors):
                if attempt + 1 == self.attempts:
                    raise
                if self.interval:
                    await sleep(self.interval / 1000)


def _positive_integer(value: RawYaml, kind: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise BadActionFile(
            f"Step {kind!r} needs a positive integer count, not {value!r}."
        )
    return value


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
    if key == "result":
        return ResultIsSet(_result_name(value, kind))
    if key == "equals":
        if not isinstance(value, dict) or value.keys() not in (
            {"param", "value"},
            {"result", "value"},
        ):
            raise BadActionFile(
                f"Step {kind!r} needs 'equals' to be a mapping with 'value' and "
                f"either 'param' or 'result', not {value!r}."
            )
        expected = _template(value["value"], params, kind)
        if "result" in value:
            return ResultEquals(_result_name(value["result"], kind), expected)
        return ParamEquals(_declared_param(value["param"], params, kind), expected)
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
    return prepare_template(template)


def _choice[T: str](
    value: RawYaml, choices: tuple[T, ...], kind: StepKind, key: ManifestKey
) -> T:
    for choice in choices:
        if value == choice:
            return choice
    raise BadActionFile(
        f"Step {kind!r} needs {key!r} to be one of {list(choices)}, not {value!r}."
    )


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
        _mapping_keys(
            value, {*sources, required, *(("as",) if kind == "download" else ())}, kind
        )
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
        _mapping_keys(value, {"target", *fields}, kind)
        target: RawYaml = value.get("target")
        if isinstance(target, str):
            return target, tuple(value.get(f) for f in fields)
    *first, last = ("target", *fields)
    raise BadActionFile(
        f"Step {kind!r} needs a mapping with string {', '.join(map(repr, first))} "
        f"and {last!r} keys, not {value!r}."
    )


def _mapping_keys(value: dict, allowed: set[str], kind: str) -> None:
    if unknown := value.keys() - allowed:
        raise BadActionFile(
            f"Step {kind!r} has unknown fields {sorted(map(str, unknown))}."
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


def _check_results(
    steps: Iterable[Step],
    taken: set[ResultName],
    selectors: dict[str, str] | None = None,
) -> None:
    """Reject a result name saved twice, since the later one would overwrite the
    earlier, and one read before any step saves it. Alternative branches may
    share a name: only one of them runs."""
    for step in steps:
        reads = set(step.results_read)
        for template in step.templates:
            reads |= result_placeholders(template)
        if selectors is not None:
            for key in step.selector_keys:
                reads |= result_placeholders(selectors[key])
        if not step.reads_after_branches:
            _require_results(reads, taken, step.kind)
        in_branches: set[ResultName] = set()
        for branch in step.branches:
            seen = set(taken)
            _check_results(branch, seen, selectors)
            in_branches |= seen
        taken |= in_branches
        if step.reads_after_branches:
            _require_results(reads, taken, step.kind)
        for result in step.result_names:
            if result in taken:
                raise BadActionFile(
                    f"Step {step.kind!r} saves result {result!r}, which an earlier step already saves."
                )
            taken.add(result)


def _require_results(reads: set[str], taken: set[str], kind: str) -> None:
    for read in sorted(reads - taken):
        raise BadActionFile(
            f"Step {kind!r} reads result {read!r}, which no earlier step saves."
        )


def parse_steps(raw: RawYaml, params: ParamNames) -> tuple[Step, ...]:
    """Steps are an ordered list of single-key mappings, e.g. ``- click: submit``.

    Steps such as ``if`` nest more step lists, which are parsed the same way.
    """
    steps = parse_step_list(raw, params)
    _check_results(steps, set())
    return steps

from __future__ import annotations

import math
import mimetypes
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Self

import aiofiles

from web_page.exceptions import BadActionFile
from web_page.protocols import FilePayload
from web_page.types import (
    ActionContext,
    AttributeName,
    KeyName,
    ManifestKey,
    Milliseconds,
    ParamName,
    ParamNames,
    ParamValues,
    PathTemplate,
    RawYaml,
    ResultName,
    SelectorKey,
    StepKind,
    Template,
    UrlTemplate,
)
from web_page.utils import check_placeholders

# Filled by Step.__init_subclass__, so defining a step class is what registers it.
_STEPS: dict[StepKind, type[Step]] = {}


class Step(ABC):
    """One manifest step, e.g. ``- click: submit``.

    Each kind is a single subclass declared as ``class Foo(Step, kind="foo")``;
    it owns parsing, running and selector lookup, so they can't drift apart.
    Subclasses declared without a ``kind`` are shared bases and are not registered.
    """

    kind: StepKind

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
    @abstractmethod
    def selector_key(self) -> SelectorKey | None:
        """The selector key this step refers to, if any."""

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

    @abstractmethod
    async def run(self, ctx: ActionContext) -> None: ...


# Shared bases: declared without a kind, so they are not steps themselves.
@dataclass(frozen=True)
class TargetStep(Step):
    """A step whose manifest value is a selector key, e.g. ``- click: submit``."""

    target: SelectorKey

    @classmethod
    def parse(cls, value: RawYaml, params: ParamNames) -> Self:
        return cls(_selector_key(value, cls.kind))

    @property
    def selector_key(self) -> SelectorKey:
        return self.target


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


# Page steps: act on the page itself, so they use no selector key.
@dataclass(frozen=True)
class Goto(Step, kind="goto"):
    url: UrlTemplate

    @classmethod
    def parse(cls, value: RawYaml, params: ParamNames) -> Self:
        return cls(_template(value, params, cls.kind))

    @property
    def selector_key(self) -> None:
        return None

    @property
    def templates(self) -> tuple[Template, ...]:
        return (self.url,)

    async def run(self, ctx: ActionContext) -> None:
        await ctx.page.goto(
            self.url.format(**ctx.params), wait_until="domcontentloaded"
        )


@dataclass(frozen=True)
class WaitForUrl(Step, kind="wait_for_url"):
    url: UrlTemplate

    @classmethod
    def parse(cls, value: RawYaml, params: ParamNames) -> Self:
        return cls(_template(value, params, cls.kind))

    @property
    def selector_key(self) -> None:
        return None

    @property
    def templates(self) -> tuple[Template, ...]:
        return (self.url,)

    async def run(self, ctx: ActionContext) -> None:
        await ctx.page.wait_for_url(
            self.url.format(**ctx.params), wait_until="domcontentloaded"
        )


@dataclass(frozen=True)
class Wait(Step, kind="wait"):
    ms: Milliseconds

    @classmethod
    def parse(cls, value: RawYaml, params: ParamNames) -> Self:
        # bool is an int subclass, but `wait: true` is surely a mistake.
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not (value >= 0 and math.isfinite(value))
        ):
            raise BadActionFile(
                f"Step {cls.kind!r} needs a non-negative number of milliseconds, "
                f"not {value!r}."
            )
        return cls(value)

    @property
    def selector_key(self) -> None:
        return None

    async def run(self, ctx: ActionContext) -> None:
        await ctx.page.wait_for_timeout(self.ms)


# Element steps: act on the element a selector key points at.
@dataclass(frozen=True)
class Click(TargetStep, kind="click"):
    async def run(self, ctx: ActionContext) -> None:
        await ctx.page.locator(ctx.sel[self.target]).click()


@dataclass(frozen=True)
class Hover(TargetStep, kind="hover"):
    async def run(self, ctx: ActionContext) -> None:
        await ctx.page.locator(ctx.sel[self.target]).hover()


@dataclass(frozen=True)
class Check(TargetStep, kind="check"):
    async def run(self, ctx: ActionContext) -> None:
        await ctx.page.locator(ctx.sel[self.target]).check()


@dataclass(frozen=True)
class Uncheck(TargetStep, kind="uncheck"):
    async def run(self, ctx: ActionContext) -> None:
        await ctx.page.locator(ctx.sel[self.target]).uncheck()


@dataclass(frozen=True)
class WaitFor(TargetStep, kind="wait_for"):
    async def run(self, ctx: ActionContext) -> None:
        await ctx.page.locator(ctx.sel[self.target]).wait_for(state="visible")


@dataclass(frozen=True)
class Fill(TargetValueStep, kind="fill"):
    async def run(self, ctx: ActionContext) -> None:
        await ctx.page.locator(ctx.sel[self.target]).fill(
            self.value.format(**ctx.params)
        )


@dataclass(frozen=True)
class Select(TargetValueStep, kind="select"):
    async def run(self, ctx: ActionContext) -> None:
        await ctx.page.locator(ctx.sel[self.target]).select_option(
            self.value.format(**ctx.params)
        )


@dataclass(frozen=True)
class Press(TargetStep, kind="press"):
    key: KeyName

    @classmethod
    def parse(cls, value: RawYaml, params: ParamNames) -> Self:
        # Not a template: a key like "Shift+{" would be read as a placeholder.
        target, (key,) = _target_mapping(value, cls.kind, "key")
        return cls(target, _string(key, cls.kind))

    async def run(self, ctx: ActionContext) -> None:
        await ctx.page.locator(ctx.sel[self.target]).press(self.key)


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
        if self.data is not None:
            value = params[self.data]
            buffer = value if isinstance(value, bytes) else value.encode()
            default_name = self.data
        else:
            assert self.path is not None
            path = Path(self.path.format(**params))
            async with aiofiles.open(path, "rb") as f:
                buffer = await f.read()
            default_name = path.name
        name = default_name if self.name is None else self.name.format(**params)
        if self.mime_type is not None:
            mime_type = self.mime_type.format(**params)
        else:
            mime_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        return {"name": name, "mimeType": mime_type, "buffer": buffer}


type UploadEntry = PathTemplate | UploadFile
"""A plain path, which the page library reads itself, or an in-memory file."""

_UPLOAD_FILE_KEYS = frozenset({"path", "data", "name", "mime_type"})


@dataclass(frozen=True)
class Upload(TargetStep, kind="upload"):
    files: tuple[UploadEntry, ...]

    @classmethod
    def parse(cls, value: RawYaml, params: ParamNames) -> Self:
        target, (files,) = _target_mapping(value, cls.kind, "files")
        entries: RawYaml = files if isinstance(files, list) else [files]
        assert isinstance(entries, list)
        return cls(target, tuple(_upload_entry(e, params, cls.kind) for e in entries))

    @property
    def templates(self) -> tuple[Template, ...]:
        return tuple(
            t
            for file in self.files
            for t in ((file,) if isinstance(file, str) else file.templates)
        )

    @property
    def data_params(self) -> frozenset[ParamName]:
        return frozenset(
            file.data
            for file in self.files
            if isinstance(file, UploadFile) and file.data is not None
        )

    async def run(self, ctx: ActionContext) -> None:
        locator = ctx.page.locator(ctx.sel[self.target])
        paths = [file for file in self.files if isinstance(file, str)]
        if len(paths) == len(self.files):
            await locator.set_input_files([path.format(**ctx.params) for path in paths])
            return
        # Paths and payloads can't be mixed in one call, so plain paths are read
        # into payloads too, keeping their own names.
        await locator.set_input_files(
            [
                await (
                    UploadFile(path=file) if isinstance(file, str) else file
                ).payload(ctx.params)
                for file in self.files
            ]
        )


@dataclass(frozen=True)
class Download(TargetStep, kind="download"):
    """Click the target and save the download it starts.

    ``to`` is a file path, or a directory (existing, or written with a trailing
    slash) to save into under the browser's suggested filename.
    """

    to: PathTemplate
    result: ResultName | None = None

    @classmethod
    def parse(cls, value: RawYaml, params: ParamNames) -> Self:
        target, (to,) = _target_mapping(value, cls.kind, "to")
        # `as` is optional, so it is not one of the mapping's required keys.
        result: RawYaml = value.get("as") if isinstance(value, dict) else None
        return cls(
            target,
            _template(to, params, cls.kind),
            None if result is None else _result_name(result, cls.kind),
        )

    @property
    def result_name(self) -> ResultName | None:
        return self.result

    @property
    def templates(self) -> tuple[Template, ...]:
        return (self.to,)

    async def run(self, ctx: ActionContext) -> None:
        locator = ctx.page.locator(ctx.sel[self.target])
        async with ctx.page.expect_download() as info:
            await locator.click()
        download = await info.value

        to = self.to.format(**ctx.params)
        path = Path(to)
        if to.endswith(("/", os.sep)) or path.is_dir():
            # Keep only the name, in case a page suggests one with a path in it.
            path /= Path(download.suggested_filename).name
        await download.save_as(str(path))
        if self.result is not None:
            ctx.results[self.result] = str(path.resolve())


# Retrieval steps: save a value under a result name; WebPage.run returns them all.
@dataclass(frozen=True)
class GetUrl(Step, kind="get_url"):
    result: ResultName

    @classmethod
    def parse(cls, value: RawYaml, params: ParamNames) -> Self:
        return cls(_result_name(value, cls.kind))

    @property
    def selector_key(self) -> None:
        return None

    @property
    def result_name(self) -> ResultName:
        return self.result

    async def run(self, ctx: ActionContext) -> None:
        ctx.results[self.result] = ctx.page.url


@dataclass(frozen=True)
class GetText(TargetResultStep, kind="get_text"):
    async def run(self, ctx: ActionContext) -> None:
        locator = ctx.page.locator(ctx.sel[self.target])
        ctx.results[self.result] = await locator.inner_text()


@dataclass(frozen=True)
class GetAllTexts(TargetResultStep, kind="get_all_texts"):
    async def run(self, ctx: ActionContext) -> None:
        locator = ctx.page.locator(ctx.sel[self.target])
        ctx.results[self.result] = await locator.all_inner_texts()


@dataclass(frozen=True)
class GetValue(TargetResultStep, kind="get_value"):
    async def run(self, ctx: ActionContext) -> None:
        locator = ctx.page.locator(ctx.sel[self.target])
        ctx.results[self.result] = await locator.input_value()


@dataclass(frozen=True)
class GetAttribute(TargetResultStep, kind="get_attribute"):
    attribute: AttributeName

    @classmethod
    def parse(cls, value: RawYaml, params: ParamNames) -> Self:
        target, (result, attribute) = _target_mapping(
            value, cls.kind, "attribute", "as"
        )
        return cls(target, _result_name(result, cls.kind), _string(attribute, cls.kind))

    async def run(self, ctx: ActionContext) -> None:
        locator = ctx.page.locator(ctx.sel[self.target])
        ctx.results[self.result] = await locator.get_attribute(self.attribute)


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


def _upload_entry(value: RawYaml, params: ParamNames, kind: StepKind) -> UploadEntry:
    """A path template, or ``{path | data, name?, mime_type?}``."""
    if isinstance(value, str):
        return _template(value, params, kind)
    if (
        not isinstance(value, dict)
        or not value.keys() <= _UPLOAD_FILE_KEYS
        or ("path" in value) == ("data" in value)
    ):
        raise BadActionFile(
            f"Step {kind!r} needs each file to be a path or a mapping with either "
            f"'path' or 'data' (plus optional 'name' and 'mime_type'), not {value!r}."
        )

    def template(key: ManifestKey) -> Template | None:
        return _template(value[key], params, kind) if key in value else None

    path, name, mime_type = template("path"), template("name"), template("mime_type")
    if path is not None:
        # Nothing to override, so let the page library read the file itself.
        if name is None and mime_type is None:
            return path
        return UploadFile(path=path, name=name, mime_type=mime_type)

    data = _string(value["data"], kind)
    if data not in params:
        raise BadActionFile(
            f"Step {kind!r} reads data from undeclared param {data!r}; "
            f"declared: {list(params)}."
        )
    if name is None:
        raise BadActionFile(f"Step {kind!r} needs a 'name' for data {data!r}.")
    return UploadFile(data=data, name=name, mime_type=mime_type)


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


def parse_steps(raw: RawYaml, params: ParamNames) -> tuple[Step, ...]:
    """Steps are an ordered list of single-key mappings, e.g. ``- click: submit``."""
    if not isinstance(raw, list):
        raise BadActionFile(
            f"Action steps should be a list of single-key mappings, not {raw!r}."
        )

    steps: list[Step] = []
    results: set[ResultName] = set()
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
        step = step_type.parse(value, params)
        # A repeated name would silently overwrite the earlier result.
        if (result := step.result_name) is not None:
            if result in results:
                raise BadActionFile(
                    f"Step {kind!r} saves result {result!r}, which an earlier step "
                    f"already saves."
                )
            results.add(result)
        steps.append(step)
    return tuple(steps)

from __future__ import annotations

import math
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from web_page.exceptions import BadActionFile
from web_page.types import (
    ActionContext,
    AttributeName,
    KeyName,
    ManifestKey,
    Milliseconds,
    ParamNames,
    PathTemplate,
    RawYaml,
    ResultName,
    SelectorKey,
    StepKind,
    Template,
    UrlTemplate,
)
from web_page.utils import check_placeholders, is_str_list

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
class Upload(TargetStep, kind="upload"):
    files: tuple[PathTemplate, ...]

    @classmethod
    def parse(cls, value: RawYaml, params: ParamNames) -> Self:
        target, (files,) = _target_mapping(value, cls.kind, "files")
        templates = [files] if isinstance(files, str) else files
        if not is_str_list(templates):
            raise BadActionFile(
                f"Step {cls.kind!r} needs 'files' to be a path or a list of paths, "
                f"not {files!r}."
            )
        return cls(target, tuple(_template(t, params, cls.kind) for t in templates))

    async def run(self, ctx: ActionContext) -> None:
        await ctx.page.locator(ctx.sel[self.target]).set_input_files(
            [file.format(**ctx.params) for file in self.files]
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

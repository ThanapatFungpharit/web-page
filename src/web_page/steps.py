from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Self

from web_page.exceptions import BadActionFile
from web_page.types import (
    ActionContext,
    ParamNames,
    RawYaml,
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
    """

    kind: StepKind

    def __init_subclass__(cls, *, kind: StepKind, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
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

    @abstractmethod
    async def run(self, ctx: ActionContext) -> None: ...


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
class Click(Step, kind="click"):
    target: SelectorKey

    @classmethod
    def parse(cls, value: RawYaml, params: ParamNames) -> Self:
        return cls(_selector_key(value, cls.kind))

    @property
    def selector_key(self) -> SelectorKey:
        return self.target

    async def run(self, ctx: ActionContext) -> None:
        await ctx.page.locator(ctx.sel[self.target]).click()


@dataclass(frozen=True)
class Fill(Step, kind="fill"):
    target: SelectorKey
    value: Template

    @classmethod
    def parse(cls, value: RawYaml, params: ParamNames) -> Self:
        if not isinstance(value, dict):
            raise BadActionFile(
                f"Step {cls.kind!r} needs a mapping with string 'target' and 'value' "
                f"keys, not {value!r}."
            )
        target: RawYaml = value.get("target")
        template: RawYaml = value.get("value")
        if not isinstance(target, str):
            raise BadActionFile(
                f"Step {cls.kind!r} needs a mapping with string 'target' and 'value' "
                f"keys, not {value!r}."
            )
        return cls(target, _template(template, params, cls.kind))

    @property
    def selector_key(self) -> SelectorKey:
        return self.target

    async def run(self, ctx: ActionContext) -> None:
        await ctx.page.locator(ctx.sel[self.target]).fill(
            self.value.format(**ctx.params)
        )


@dataclass(frozen=True)
class WaitFor(Step, kind="wait_for"):
    target: SelectorKey

    @classmethod
    def parse(cls, value: RawYaml, params: ParamNames) -> Self:
        return cls(_selector_key(value, cls.kind))

    @property
    def selector_key(self) -> SelectorKey:
        return self.target

    async def run(self, ctx: ActionContext) -> None:
        await ctx.page.locator(ctx.sel[self.target]).wait_for(state="visible")


# Checkers: validate a raw manifest value and return it with its precise type.
def _selector_key(value: RawYaml, kind: StepKind) -> SelectorKey:
    if not isinstance(value, str):
        raise BadActionFile(f"Step {kind!r} needs a string value, not {value!r}.")
    return value


def _template(value: RawYaml, params: ParamNames, kind: StepKind) -> Template:
    if not isinstance(value, str):
        raise BadActionFile(f"Step {kind!r} needs a string value, not {value!r}.")
    check_placeholders(value, params, kind)
    return value


def parse_steps(raw: RawYaml, params: ParamNames) -> tuple[Step, ...]:
    """Steps are an ordered list of single-key mappings, e.g. ``- click: submit``."""
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
        steps.append(step_type.parse(value, params))
    return tuple(steps)

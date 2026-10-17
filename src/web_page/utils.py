import re
import string
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import TypeIs

from web_page.exceptions import BadActionFile, BadResultValue
from web_page.protocols import FilePayload
from web_page.types import (
    ParamName,
    ParamNames,
    ParamValues,
    RawYaml,
    Results,
    ResultValue,
    StepKind,
    Template,
)


# Shape checks: these validate structure only; callers give the result meaning.
def is_str_list(value: RawYaml) -> TypeIs[list[str]]:
    return isinstance(value, list) and all(isinstance(x, str) for x in value)


def is_str_dict(value: RawYaml) -> TypeIs[dict[str, str]]:
    return isinstance(value, dict) and all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    )


def is_file_payload(value: object) -> TypeIs[FilePayload]:
    return (
        isinstance(value, dict)
        and value.keys() == {"name", "mimeType", "buffer"}
        and isinstance(value["name"], str)
        and isinstance(value["mimeType"], str)
        and isinstance(value["buffer"], bytes)
    )


def is_result_value(value: object) -> TypeIs[ResultValue]:
    return value is None or isinstance(value, str) or is_str_list(value)


def check_placeholders(template: Template, params: ParamNames, step: StepKind) -> None:
    try:
        unknown = placeholders(template) - set(params)
    except ValueError as e:
        raise BadActionFile(
            f"Step {step!r} has a malformed template {template!r}: {e}"
        ) from e
    if unknown:
        raise BadActionFile(
            f"Step {step!r} uses undeclared params {sorted(unknown)}; declared: {list(params)}."
        )


# Templates: the one definition of what a template may contain, and how it is
# filled in, shared by step values and selectors.
@dataclass(frozen=True, slots=True)
class _CompiledTemplate:
    # Store syntax only. Params and saved results can change between accesses.
    parts: tuple[tuple[str, str | None, str, bool], ...]
    params: frozenset[str]
    results: frozenset[str]
    literal: str | None

    def render(self, params: ParamValues, results: Results | None = None) -> str:
        if self.literal is not None:
            return self.literal
        out: list[str] = []
        for literal, name, escape, is_result in self.parts:
            out.append(literal)
            if name is None:
                continue
            if is_result:
                value = None if results is None else results.get(name)
                if not isinstance(value, str):
                    raise BadResultValue(name, value)
                text = value
            else:
                text = str(params[name])
            out.append(ESCAPES[escape](text) if escape else text)
        return "".join(out)


class _PreparedTemplate(str):
    """A string-compatible template that owns its compiled syntax for its lifetime."""

    __slots__ = ("compiled",)
    compiled: _CompiledTemplate

    def __new__(cls, template: str, compiled: _CompiledTemplate):
        instance = super().__new__(cls, template)
        instance.compiled = compiled
        return instance

    def __getnewargs_ex__(
        self,
    ) -> tuple[tuple[str, _CompiledTemplate], dict[str, object]]:
        return (str(self), self.compiled), {}


@lru_cache(maxsize=1024)
def _compile_template(template: Template) -> _CompiledTemplate:
    """The parameter and saved-result names a template uses.

    Plain ``{name}`` and ``{result.name}`` placeholders are allowed, optionally with an escape
    from ``ESCAPES`` as in ``{name:css}``, plus ``{{`` and ``}}`` for literal
    braces. ``str.format`` extras, such as ``{}``, ``{0}``,
    ``{name.attr}``, ``{name[0]}``, ``{name!r}`` and ``{name:>8}``, raise
    ``ValueError``: they would pass a load-time check but fail, or quietly
    produce something odd, when the action runs.
    """
    names: set[ParamName] = set()
    results: set[str] = set()
    parts: list[tuple[str, str | None, str, bool]] = []
    for literal, field, spec, conversion in string.Formatter().parse(template):
        if field is None:
            parts.append((literal, None, "", False))
            continue
        written = (
            "{"
            + field
            + ("" if conversion is None else f"!{conversion}")
            + (f":{spec}" if spec else "")
            + "}"
        )
        if not field or field.isdigit():
            raise ValueError(f"{written} needs a param name, as in {{name}}")
        result = field.removeprefix("result.") if field.startswith("result.") else None
        plain = field if result is None else result
        if (
            conversion is not None
            or not plain
            or "." in plain
            or "[" in plain
            or "]" in plain
        ):
            raise ValueError(
                f"{written} is not a plain {{name}} placeholder; "
                f"formatting, attributes and indexes are not supported"
            )
        if spec and spec not in ESCAPES:
            raise ValueError(
                f"{written} is not a plain {{name}} placeholder; the only "
                f"formats are the escapes {sorted(ESCAPES)}, as in {{{field}:css}}"
            )
        (names if result is None else results).add(plain)
        parts.append((literal, plain, spec or "", result is not None))
    return _CompiledTemplate(
        tuple(parts),
        frozenset(names),
        frozenset(results),
        "".join(part[0] for part in parts) if not names and not results else None,
    )


def placeholders(template: Template) -> set[ParamName]:
    # Return fresh sets so callers cannot mutate the shared compiled template.
    return set(compiled_template(template).params)


def result_placeholders(template: Template) -> set[str]:
    return set(compiled_template(template).results)


def compiled_template(template: Template) -> _CompiledTemplate:
    if isinstance(template, _PreparedTemplate):
        return template.compiled
    return _compile_template(template)


def prepare_template(template: Template) -> Template:
    """Bind syntax at load time, preserving string equality and the template API."""
    if isinstance(template, _PreparedTemplate):
        return template
    return _PreparedTemplate(template, _compile_template(template))


def fill(
    template: Template, params: ParamValues, results: Results | None = None
) -> str:
    """Fill a template's placeholders in with params, checked beforehand."""
    if isinstance(template, _PreparedTemplate):
        return template.compiled.render(params, results)
    if "{" not in template and "}" not in template:
        return template
    return _compile_template(template).render(params, results)


class SelectorTemplates(Mapping[str, str]):
    """Resolve selectors when used, so earlier results can select later elements."""

    def __init__(self, raw: Mapping[str, str], params: ParamValues, results: Results):
        self.raw, self.params, self.results = raw, params, results

    def __getitem__(self, key: str) -> str:
        compiled = compiled_template(self.raw[key])
        if not all(isinstance(self.params.get(p), str) for p in compiled.params):
            raise KeyError(key)
        return compiled.render(self.params, self.results)

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str) or key not in self.raw:
            return False
        compiled = compiled_template(self.raw[key])
        return all(
            isinstance(self.params.get(p), str) for p in compiled.params
        ) and all(isinstance(self.results.get(r), str) for r in compiled.results)

    def __iter__(self) -> Iterator[str]:
        return (key for key in self.raw if key in self)

    def __len__(self) -> int:
        return sum(1 for _ in self)


# Escapes: ``{name:<escape>}`` inserts the value so that it matches itself
# literally, wherever in a selector or value it lands.
def _css_identifier(value: str) -> str:
    """``CSS.escape``: for an identifier, such as an id after ``#``."""
    out: list[str] = []
    for i, char in enumerate(value):
        code = ord(char)
        leading_digit = char in string.digits and (
            i == 0 or (i == 1 and value[0] == "-")
        )
        if char == "\0":
            out.append("�")
        elif code < 0x20 or code == 0x7F or leading_digit:
            out.append(f"\\{code:x} ")
        elif value == "-":
            out.append("\\-")
        elif code >= 0x80 or char in "-_" or (char.isascii() and char.isalnum()):
            out.append(char)
        else:
            out.append("\\" + char)
    return "".join(out)


def _quoted_string(value: str) -> str:
    """For a CSS quoted string, including control characters."""
    escaped: list[str] = []
    for char in value:
        if char == "\0":
            escaped.append("�")
        elif ord(char) < 32 or ord(char) == 127:
            escaped.append(f"\\{ord(char):x} ")
        elif char in "\\\"'":
            escaped.append("\\" + char)
        else:
            escaped.append(char)
    return "".join(escaped)


def _role_string(value: str) -> str:
    # Playwright's role parser unescapes the next character, not CSS hex escapes.
    return re.sub(r"""[\\'"]""", r"\\\g<0>", value)


def _regex(value: str) -> str:
    """For inside a JavaScript regex, such as ``text=/…/`` or ``[name=/…/]``."""
    return re.sub(r"[\\^$.*+?()[\]{}|/]", r"\\\g<0>", value)


ESCAPES: dict[str, Callable[[str], str]] = {
    "css": _css_identifier,
    "string": _quoted_string,
    "role": _role_string,
    "regex": _regex,
}

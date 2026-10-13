import re
import string
from collections.abc import Callable
from typing import TypeIs

from web_page.exceptions import BadActionFile
from web_page.protocols import FilePayload
from web_page.types import (
    ParamName,
    ParamNames,
    ParamValues,
    RawYaml,
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
def placeholders(template: Template) -> set[ParamName]:
    """The param names a template uses.

    Only plain ``{name}`` placeholders are allowed, optionally with an escape
    from ``ESCAPES`` as in ``{name:css}``, plus ``{{`` and ``}}`` for literal
    braces. ``str.format`` extras, such as ``{}``, ``{0}``,
    ``{name.attr}``, ``{name[0]}``, ``{name!r}`` and ``{name:>8}``, raise
    ``ValueError``: they would pass a load-time check but fail, or quietly
    produce something odd, when the action runs.
    """
    names: set[ParamName] = set()
    for _, field, spec, conversion in string.Formatter().parse(template):
        if field is None:
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
        if conversion is not None or "." in field or "[" in field:
            raise ValueError(
                f"{written} is not a plain {{name}} placeholder; "
                f"formatting, attributes and indexes are not supported"
            )
        if spec and spec not in ESCAPES:
            raise ValueError(
                f"{written} is not a plain {{name}} placeholder; the only "
                f"formats are the escapes {sorted(ESCAPES)}, as in {{{field}:css}}"
            )
        names.add(field)
    return names


class _Filler(string.Formatter):
    def format_field(self, value: object, format_spec: str) -> str:
        text = str(value)
        return ESCAPES[format_spec](text) if format_spec else text


def fill(template: Template, params: ParamValues) -> str:
    """Fill a template's placeholders in with params, checked beforehand."""
    return _Filler().vformat(template, (), params)


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
    """For inside quotes: a CSS string, or a Playwright ``[name='…']``."""
    return re.sub(r"""[\\'"]""", r"\\\g<0>", value)


def _regex(value: str) -> str:
    """For inside a JavaScript regex, such as ``text=/…/`` or ``[name=/…/]``."""
    return re.sub(r"[\\^$.*+?()[\]{}|/]", r"\\\g<0>", value)


ESCAPES: dict[str, Callable[[str], str]] = {
    "css": _css_identifier,
    "string": _quoted_string,
    "regex": _regex,
}

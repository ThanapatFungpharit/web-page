import string
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

    Only plain ``{name}`` placeholders are allowed, plus ``{{`` and ``}}`` for
    literal braces. ``str.format`` extras, such as ``{}``, ``{0}``,
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
        if spec or conversion is not None or "." in field or "[" in field:
            raise ValueError(
                f"{written} is not a plain {{name}} placeholder; "
                f"formatting, attributes and indexes are not supported"
            )
        names.add(field)
    return names


def fill(template: Template, params: ParamValues) -> str:
    """Fill a template's placeholders in with params, checked beforehand."""
    return template.format_map(params)

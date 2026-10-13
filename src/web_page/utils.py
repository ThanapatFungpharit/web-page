import string
from typing import TypeIs

from web_page.exceptions import BadActionFile
from web_page.types import ParamName, ParamNames, RawYaml, StepKind, Template


# Shape checks: these validate structure only; callers give the result meaning.
def is_str_list(value: RawYaml) -> TypeIs[list[str]]:
    return isinstance(value, list) and all(isinstance(x, str) for x in value)


def is_str_dict(value: RawYaml) -> TypeIs[dict[str, str]]:
    return isinstance(value, dict) and all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
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


def placeholders(template: Template) -> set[ParamName]:
    return {
        field.split(".")[0].split("[")[0]
        for _, field, _, _ in string.Formatter().parse(template)
        if field
    }

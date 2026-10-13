from pathlib import Path

import aiofiles
from yaml import YAMLError, safe_load

from web_page.constants import YAML_SUFFIXES
from web_page.types import (
    FieldLabel,
    ManifestError,
    ManifestKey,
    ManifestKind,
    ManifestPath,
    RawManifest,
    RawYaml,
)
from web_page.utils import is_str_list


async def read_manifest(
    path: ManifestPath, error: ManifestError, kind: ManifestKind
) -> RawManifest:
    """Read a YAML manifest and make sure it is a mapping."""
    path = Path(path).resolve()
    if not path.is_file():
        raise error(f"{kind} manifest path ({path}) should be a valid file path.")
    if path.suffix.lower() not in YAML_SUFFIXES:
        raise error(
            f"{kind} manifest suffix should be .yaml or .yml, not {path.suffix}."
        )

    async with aiofiles.open(path, encoding="utf-8") as f:
        content = await f.read()
    try:
        manifest: RawYaml = safe_load(content)
    except YAMLError as e:
        raise error(f"{kind} manifest ({path}) is not valid YAML: {e}") from e
    if not isinstance(manifest, dict):
        raise error(f"{kind} manifest ({path}) should be a mapping, not {manifest!r}.")
    return manifest


def require(
    manifest: RawManifest,
    key: ManifestKey,
    error: ManifestError,
    kind: ManifestKind,
) -> RawYaml:
    """Return manifest[key], raising `error` with a clear message if it is missing."""
    try:
        return manifest[key]
    except KeyError:
        raise error(f"{kind} manifest is missing required key {key!r}.") from None


def as_str_list(value: RawYaml, error: ManifestError, what: FieldLabel) -> list[str]:
    """Accept a str or list[str]; always return list[str]."""
    if isinstance(value, str):
        return [value]
    if not is_str_list(value):
        raise error(f"{what} should be str or list[str], not {value!r}.")
    return value

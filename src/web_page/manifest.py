from pathlib import Path

import aiofiles
from yaml import SafeLoader, YAMLError, load
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode

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


class _ManifestLoader(SafeLoader):
    def __init__(self, stream) -> None:
        super().__init__(stream)
        self._checked_mappings: set[MappingNode] = set()

    def flatten_mapping(self, node: MappingNode) -> None:
        if node not in self._checked_mappings:
            self._checked_mappings.add(node)
            seen = set()
            merge_seen = False
            # Check explicit keys before flattening YAML merge keys. Overrides of
            # inherited values remain legal; duplicate keys written here do not.
            for key_node, _ in node.value:
                if key_node.tag == "tag:yaml.org,2002:merge":
                    if merge_seen:
                        raise ConstructorError(
                            None, None, "duplicate key '<<'", key_node.start_mark
                        )
                    merge_seen = True
                    continue
                key = self.construct_object(key_node)
                try:
                    duplicate = key in seen
                    seen.add(key)
                except TypeError as e:
                    raise ConstructorError(
                        None, None, "unhashable mapping key", key_node.start_mark
                    ) from e
                if duplicate:
                    raise ConstructorError(
                        None, None, f"duplicate key {key!r}", key_node.start_mark
                    )
        super().flatten_mapping(node)


def check_keys(manifest: RawManifest, allowed: set[str], error: ManifestError) -> None:
    if unknown := manifest.keys() - allowed:
        raise error(f"Unknown manifest fields: {sorted(map(str, unknown))}.")


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
        manifest: RawYaml = load(content, Loader=_ManifestLoader)
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

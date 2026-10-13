from pathlib import Path

import pytest

from tests.conftest import WriteYaml
from web_page.exceptions import BadActionFile, BadSelectorsFile
from web_page.manifest import as_str_list, read_manifest, require


@pytest.mark.parametrize("name", ["m.yaml", "m.yml", "m.YAML"])
async def test_read_manifest_reads_mapping(write_yaml: WriteYaml, name: str) -> None:
    path = write_yaml(name, "name: x\nvalue: 1\n")
    assert await read_manifest(path, BadActionFile, "Action") == {
        "name": "x",
        "value": 1,
    }


async def test_read_manifest_accepts_str_path(write_yaml: WriteYaml) -> None:
    path = write_yaml("m.yaml", "name: x\n")
    assert await read_manifest(str(path), BadActionFile, "Action") == {"name": "x"}


async def test_read_manifest_missing_file(tmp_path: Path) -> None:
    with pytest.raises(BadSelectorsFile, match="valid file path"):
        await read_manifest(tmp_path / "nope.yaml", BadSelectorsFile, "Selectors")


async def test_read_manifest_directory(tmp_path: Path) -> None:
    with pytest.raises(BadActionFile, match="valid file path"):
        await read_manifest(tmp_path, BadActionFile, "Action")


async def test_read_manifest_bad_suffix(write_yaml: WriteYaml) -> None:
    path = write_yaml("m.json", "name: x\n")
    with pytest.raises(BadActionFile, match=r"not \.json"):
        await read_manifest(path, BadActionFile, "Action")


async def test_read_manifest_invalid_yaml(write_yaml: WriteYaml) -> None:
    path = write_yaml("m.yaml", "name: [unclosed\n")
    with pytest.raises(BadActionFile, match="not valid YAML"):
        await read_manifest(path, BadActionFile, "Action")


@pytest.mark.parametrize("content", ["", "- a\n- b\n", "just a string\n"])
async def test_read_manifest_not_mapping(write_yaml: WriteYaml, content: str) -> None:
    path = write_yaml("m.yaml", content)
    with pytest.raises(BadActionFile, match="should be a mapping"):
        await read_manifest(path, BadActionFile, "Action")


def test_require_present() -> None:
    assert require({"name": "x"}, "name", BadActionFile, "Action") == "x"


def test_require_missing() -> None:
    with pytest.raises(BadActionFile, match="missing required key 'steps'"):
        require({}, "steps", BadActionFile, "Action")


def test_as_str_list_wraps_str() -> None:
    assert as_str_list("a", BadActionFile, "Params") == ["a"]


def test_as_str_list_passes_list() -> None:
    assert as_str_list(["a", "b"], BadActionFile, "Params") == ["a", "b"]


@pytest.mark.parametrize("value", [None, 1, ["a", 1], {"a": "b"}])
def test_as_str_list_rejects(value: object) -> None:
    with pytest.raises(BadActionFile, match="Params should be str or list"):
        as_str_list(value, BadActionFile, "Params")

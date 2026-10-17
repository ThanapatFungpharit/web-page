"""Keep cached template syntax separate from mutable execution state."""

import pytest

from web_page.exceptions import BadResultValue
from web_page.types import ParamValues, Results
from web_page.utils import (
    SelectorTemplates,
    fill,
    placeholders,
    result_placeholders,
)


@pytest.mark.parametrize(
    ("template", "expected"),
    [
        ("", ""),
        ("plain text", "plain text"),
        ("{{}}", "{}"),
        ("{{{name}}}", "{sample}"),
        ("{name:}/{name}/{result.saved}", "sample/sample/value"),
        ("{{result.saved}}/{result.saved}", "{result.saved}/value"),
    ],
)
def test_template_literals_and_repeated_fields(template: str, expected: str) -> None:
    for _ in range(2):
        assert fill(template, {"name": "sample"}, {"saved": "value"}) == expected


def test_template_references_cannot_be_mutated_by_callers() -> None:
    template = "{name}/{result.saved}"
    placeholders(template).clear()
    result_placeholders(template).add("other")
    assert placeholders(template) == {"name"}
    assert result_placeholders(template) == {"saved"}


def test_templates_read_current_values_after_cache_eviction() -> None:
    template = "{name}/{result.saved}"
    assert fill(template, {"name": "first"}, {"saved": "one"}) == "first/one"
    for index in range(1100):
        assert fill(f"{index}-{{name}}", {"name": "x"}) == f"{index}-x"
    assert fill(template, {"name": "second"}, {"saved": "two"}) == "second/two"


@pytest.mark.parametrize("value", [None, ["list"]])
def test_warm_templates_still_reject_non_string_results(value) -> None:
    results: Results = {"saved": "ok"}
    assert fill("{result.saved:string}", {}, results) == "ok"
    results["saved"] = value
    with pytest.raises(BadResultValue):
        fill("{result.saved:string}", {}, results)
    results.clear()
    with pytest.raises(BadResultValue):
        fill("{result.saved:string}", {}, results)


def test_selector_mapping_tracks_mutations_after_warming() -> None:
    params: dict = {"name": "first"}
    results: Results = {"saved": "one"}
    raw = {"item": "#{name}-{result.saved}"}
    selectors = SelectorTemplates(raw, params, results)
    assert dict(selectors) == {"item": "#first-one"}
    params["name"], results["saved"] = "second", "two"
    assert dict(selectors) == {"item": "#second-two"}
    results.clear()
    assert "item" not in selectors
    assert len(selectors) == 0
    with pytest.raises(BadResultValue):
        _ = selectors["item"]
    results["saved"] = "three"
    params["name"] = b"binary"
    assert "item" not in selectors
    with pytest.raises(KeyError):
        _ = selectors["item"]
    params["name"] = "third"
    raw["item"] = "[name='{name:string}']"
    assert dict(selectors) == {"item": "[name='third']"}


def test_shared_template_does_not_share_state_between_contexts() -> None:
    raw = {"item": "[name='{name:string}']-{result.saved:regex}"}
    first = SelectorTemplates(raw, {"name": "a'b"}, {"saved": "x.y"})
    second = SelectorTemplates(raw, {"name": "other"}, {"saved": "z*"})
    assert first["item"] == r"[name='a\'b']-x\.y"
    assert second["item"] == r"[name='other']-z\*"
    assert first["item"] == r"[name='a\'b']-x\.y"


def test_membership_does_not_scan_unrelated_selectors() -> None:
    class CountingParams(dict):
        reads = 0

        def get(self, key, default=None):
            self.reads += 1
            return super().get(key, default)

    params = CountingParams(name="sample")
    selectors = SelectorTemplates(
        {f"item{index}": "#{name}" for index in range(1000)}, params, {}
    )
    assert "item999" in selectors
    assert "absent" not in selectors
    assert params.reads == 1


def test_missing_template_parameter_keeps_key_error() -> None:
    params: ParamValues = {"name": "sample"}
    assert fill("{name}", params) == "sample"
    with pytest.raises(KeyError, match="name"):
        fill("{name}", {})

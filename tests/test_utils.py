import pytest

from web_page.exceptions import BadActionFile
from web_page.utils import check_placeholders, fill, is_str_dict, is_str_list


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ([], True),
        (["a", "b"], True),
        (["a", 1], False),
        ("a", False),
        (None, False),
        (("a",), False),
    ],
)
def test_is_str_list(value: object, expected: bool) -> None:
    assert is_str_list(value) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({}, True),
        ({"a": "b"}, True),
        ({"a": 1}, False),
        ({1: "b"}, False),
        (["a"], False),
        (None, False),
    ],
)
def test_is_str_dict(value: object, expected: bool) -> None:
    assert is_str_dict(value) is expected


@pytest.mark.parametrize(
    "template",
    [
        "no placeholders",
        "https://example.com/{user}",
        "{user}/{query}",
        "{{literal braces}}",
        "Shift+{{",
    ],
)
def test_check_placeholders_accepts_declared(template: str) -> None:
    check_placeholders(template, ["user", "query"], "goto")


@pytest.mark.parametrize(
    ("template", "match"),
    [
        ("{}", "needs a param name"),
        ("{0}", "needs a param name"),
        ("{user.name}", "not a plain"),
        ("{user[0]}", "not a plain"),
        ("{user!r}", "not a plain"),
        ("{user:>10}", "not a plain"),
        ("{user:{query}}", "not a plain"),
    ],
)
def test_placeholders_are_plain_names(template: str, match: str) -> None:
    # These all pass str.format's own parsing, but would fail or misbehave at run.
    with pytest.raises(BadActionFile, match=match):
        check_placeholders(template, ["user", "query"], "goto")


def test_fill() -> None:
    assert fill("{a}-{{b}}-{a}", {"a": "x"}) == "x-{b}-x"


def test_check_placeholders_rejects_undeclared() -> None:
    with pytest.raises(BadActionFile, match=r"undeclared params \['other'\]"):
        check_placeholders("{user}/{other}", ["user"], "goto")


@pytest.mark.parametrize("template", ["{user", "user}", "{0"])
def test_check_placeholders_rejects_malformed(template: str) -> None:
    with pytest.raises(BadActionFile, match="malformed template"):
        check_placeholders(template, ["user"], "fill")


@pytest.mark.parametrize(
    ("value", "escape", "expected"),
    [
        ("plain-id_1", "css", "plain-id_1"),
        ("a b.c#d", "css", r"a\ b\.c\#d"),
        ("1st", "css", r"\31 st"),
        ("-2", "css", r"-\32 "),
        ("-", "css", r"\-"),
        ("é", "css", "é"),
        ('it\'s "x" \\', "string", r"it\'s \"x\" \\"),
        ("a.b*(c)/[d]", "regex", r"a\.b\*\(c\)\/\[d\]"),
        ("$1 + {2}?", "regex", r"\$1 \+ \{2\}\?"),
    ],
)
def test_fill_escapes(value: str, escape: str, expected: str) -> None:
    assert fill(f"<{{v:{escape}}}>", {"v": value}) == f"<{expected}>"


def test_escapes_are_placeholders() -> None:
    check_placeholders("[name='{user:string}'] #{query:css}", ["user", "query"], "x")
    with pytest.raises(BadActionFile, match="undeclared params"):
        check_placeholders("{other:css}", ["user"], "x")
    with pytest.raises(BadActionFile, match="escapes"):
        check_placeholders("{user:html}", ["user"], "x")

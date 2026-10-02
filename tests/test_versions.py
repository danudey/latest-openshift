from __future__ import annotations

import pytest

from latest_openshift.errors import InvalidVersionError
from latest_openshift.versions import Version


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("4", Version(4)),
        ("4.22", Version(4, 22)),
        ("4.22.13", Version(4, 22, 13)),
        ("v4.22.13", Version(4, 22, 13)),
        (" 4.22.13 ", Version(4, 22, 13)),
        ("4.22.0-rc.5", Version(4, 22, 0, ("rc", "5"))),
        ("5.1.0-ec.0", Version(5, 1, 0, ("ec", "0"))),
        ("4.9.13-assembly.art3657", Version(4, 9, 13, ("assembly", "art3657"))),
    ],
)
def test_parse(text, expected):
    assert Version.parse(text) == expected


@pytest.mark.parametrize("text", ["", "x", "4.", "4.x", "4.22.", "4.22-rc.1", "-1"])
def test_parse_rejects_nonsense(text):
    with pytest.raises(InvalidVersionError):
        Version.parse(text)


def test_try_parse_returns_none():
    assert Version.try_parse("release.txt") is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("4", "4"),
        ("4.22", "4.22"),
        ("4.22.13", "4.22.13"),
        ("4.22.0-rc.5", "4.22.0-rc.5"),
    ],
)
def test_roundtrips_through_str(text, expected):
    assert str(Version.parse(text)) == expected


def test_is_complete():
    assert Version.parse("4.22.13").is_complete
    assert not Version.parse("4.22").is_complete
    assert not Version.parse("4").is_complete


def test_ordering_is_numeric_not_lexical():
    versions = [Version.parse(t) for t in ("4.9.59", "4.10.1", "4.22.13", "4.22.9")]
    assert [str(v) for v in sorted(versions)] == [
        "4.9.59",
        "4.10.1",
        "4.22.9",
        "4.22.13",
    ]


def test_prerelease_sorts_below_its_release():
    assert Version.parse("4.22.0-rc.5") < Version.parse("4.22.0")
    assert Version.parse("4.22.0-ec.6") < Version.parse("4.22.0-rc.0")
    assert Version.parse("4.22.0-rc.2") < Version.parse("4.22.0-rc.10")


def test_partial_sorts_below_its_completions():
    assert Version.parse("4") < Version.parse("4.0")
    assert Version.parse("4.22") < Version.parse("4.22.0")


def test_matches_is_a_prefix_test():
    assert Version.parse("4").matches(Version.parse("4.22.13"))
    assert Version.parse("4.22").matches(Version.parse("4.22.13"))
    assert Version.parse("4.22.13").matches(Version.parse("4.22.13"))
    assert not Version.parse("4.21").matches(Version.parse("4.22.13"))
    assert not Version.parse("5").matches(Version.parse("4.22.13"))
    assert not Version.parse("4.22.13").matches(Version.parse("4.22"))


def test_stream():
    assert Version.parse("4.22.13").stream == Version(4, 22)
    assert Version.parse("4").stream == Version(4)

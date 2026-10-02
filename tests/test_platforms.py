from __future__ import annotations

import pytest

from latest_openshift import platforms
from latest_openshift.errors import PlatformError
from latest_openshift.platforms import Platform, mirror_arch_dir, prefers_rhel8


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("linux/amd64", Platform("linux", "amd64")),
        ("linux-amd64", Platform("linux", "amd64")),
        ("linux/x86_64", Platform("linux", "amd64")),
        ("darwin/arm64", Platform("mac", "arm64")),
        ("mac/aarch64", Platform("mac", "arm64")),
        ("Windows/AMD64", Platform("windows", "amd64")),
        ("linux/s390x", Platform("linux", "s390x")),
    ],
)
def test_parse(text, expected):
    assert Platform.parse(text) == expected


@pytest.mark.parametrize("text", ["linux", "linux/riscv64", "plan9/amd64", "/amd64"])
def test_parse_rejects_unknown(text):
    with pytest.raises(PlatformError):
        Platform.parse(text)


def test_archive_suffix():
    assert Platform("windows", "amd64").archive_suffix == ".zip"
    assert Platform("linux", "amd64").archive_suffix == ".tar.gz"


@pytest.mark.parametrize(
    ("platform", "expected"),
    [
        (Platform("linux", "amd64"), "x86_64"),
        (Platform("linux", "arm64"), "arm64"),
        (Platform("linux", "ppc64le"), "ppc64le"),
        (Platform("linux", "s390x"), "s390x"),
        # macOS and Windows archives are the same in every directory.
        (Platform("mac", "arm64"), "x86_64"),
        (Platform("windows", "amd64"), "x86_64"),
        (None, "x86_64"),
    ],
)
def test_mirror_arch_dir(platform, expected):
    assert mirror_arch_dir(platform) == expected


def test_prefers_rhel8_only_for_this_machine(monkeypatch):
    native = platforms.current_platform()
    monkeypatch.setattr(platforms, "glibc_version", lambda: (2, 28))

    if native.os == "linux":
        assert prefers_rhel8(native) is True
    # A platform we are merely cross-downloading for has an unknowable glibc.
    other = Platform("linux", "s390x" if native.arch != "s390x" else "ppc64le")
    assert prefers_rhel8(other) is False


def test_prefers_rhel8_is_false_on_new_glibc(monkeypatch):
    monkeypatch.setattr(platforms, "glibc_version", lambda: (2, 39))
    assert prefers_rhel8(platforms.current_platform()) is False


def test_prefers_rhel8_is_false_without_glibc(monkeypatch):
    monkeypatch.setattr(platforms, "glibc_version", lambda: None)
    assert prefers_rhel8(platforms.current_platform()) is False

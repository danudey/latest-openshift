from __future__ import annotations

import pytest

from latest_openshift.artifacts import (
    available_tools,
    binaries_for_tool,
    canonical_tool_name,
    is_binary_member,
    normalize_member_name,
    parse_filename,
    parse_listing,
    select_artifact,
    tool_for_binary,
)
from latest_openshift.platforms import Platform
from latest_openshift.versions import Version


@pytest.mark.parametrize(
    ("filename", "tool", "os_name", "arch", "libc", "version"),
    [
        (
            "openshift-client-linux-4.22.13.tar.gz",
            "openshift-client",
            "linux",
            "amd64",
            None,
            "4.22.13",
        ),
        (
            "openshift-client-linux-arm64-4.22.13.tar.gz",
            "openshift-client",
            "linux",
            "arm64",
            None,
            "4.22.13",
        ),
        (
            "openshift-client-linux-amd64-rhel8-4.22.13.tar.gz",
            "openshift-client",
            "linux",
            "amd64",
            "rhel8",
            "4.22.13",
        ),
        (
            "openshift-client-mac-4.22.13.tar.gz",
            "openshift-client",
            "mac",
            "amd64",
            None,
            "4.22.13",
        ),
        (
            "openshift-client-mac-arm64-4.22.13.tar.gz",
            "openshift-client",
            "mac",
            "arm64",
            None,
            "4.22.13",
        ),
        (
            "openshift-client-windows-4.22.13.zip",
            "openshift-client",
            "windows",
            "amd64",
            None,
            "4.22.13",
        ),
        ("opm-windows-4.22.13.tar.gz", "opm", "windows", "amd64", None, "4.22.13"),
        (
            "ccoctl-linux-rhel9-4.22.13.tar.gz",
            "ccoctl",
            "linux",
            "amd64",
            "rhel9",
            "4.22.13",
        ),
        ("ccoctl-linux.tar.gz", "ccoctl", "linux", "amd64", None, None),
        ("oc-mirror.tar.gz", "oc-mirror", "linux", "amd64", None, None),
        ("oc-mirror.rhel9.tar.gz", "oc-mirror", "linux", "amd64", "rhel9", None),
        (
            "openshift-install-rhel9-amd64.tar.gz",
            "openshift-install",
            "linux",
            "amd64",
            "rhel9",
            None,
        ),
        (
            "openshift-client-linux-4.22.0-rc.5.tar.gz",
            "openshift-client",
            "linux",
            "amd64",
            None,
            "4.22.0-rc.5",
        ),
        (
            "ccoctl-linux-5.1.0-ec.0.tar.gz",
            "ccoctl",
            "linux",
            "amd64",
            None,
            "5.1.0-ec.0",
        ),
        (
            "openshift-client-src-4.22.13-x86_64.tar.gz",
            "openshift-client",
            "src",
            "amd64",
            None,
            "4.22.13",
        ),
    ],
)
def test_parse_filename(filename, tool, os_name, arch, libc, version):
    artifact = parse_filename(filename)
    assert artifact is not None
    assert artifact.tool == tool
    assert artifact.platform == Platform(os_name, arch)
    assert artifact.libc == libc
    assert artifact.version == (Version.parse(version) if version else None)


@pytest.mark.parametrize(
    "filename", ["release.txt", "sha256sum.txt", "sha256sum.txt.gpg", "index.html"]
)
def test_parse_filename_ignores_non_archives(filename):
    assert parse_filename(filename) is None


def test_unqualified_linux_name_follows_the_directory_architecture():
    """In the arm64 directory an unqualified Linux archive is an arm64 build."""
    artifact = parse_filename("ccoctl-linux-4.22.13.tar.gz", default_arch="arm64")
    assert artifact.platform == Platform("linux", "arm64")


def test_unqualified_mac_name_is_amd64_in_every_directory():
    """macOS archives are identical in each directory and always spell out arm64."""
    artifact = parse_filename(
        "openshift-client-mac-4.22.13.tar.gz", default_arch="arm64"
    )
    assert artifact.platform == Platform("mac", "amd64")


def test_available_tools_excludes_sources(listing_4_22):
    artifacts = parse_listing(listing_4_22)
    assert available_tools(artifacts) == [
        "ccoctl",
        "oc-mirror",
        "openshift-client",
        "openshift-install",
        "opm",
    ]


def test_available_tools_for_one_platform(listing_4_22):
    artifacts = parse_listing(listing_4_22)
    assert available_tools(artifacts, Platform("windows", "amd64")) == [
        "openshift-client",
        "opm",
    ]


@pytest.mark.parametrize(
    ("tool", "platform", "expected"),
    [
        (
            "openshift-client",
            ("linux", "amd64"),
            "openshift-client-linux-4.22.13.tar.gz",
        ),
        ("oc", ("linux", "amd64"), "openshift-client-linux-4.22.13.tar.gz"),
        (
            "openshift-client",
            ("linux", "arm64"),
            "openshift-client-linux-arm64-4.22.13.tar.gz",
        ),
        (
            "openshift-client",
            ("mac", "arm64"),
            "openshift-client-mac-arm64-4.22.13.tar.gz",
        ),
        (
            "openshift-client",
            ("windows", "amd64"),
            "openshift-client-windows-4.22.13.zip",
        ),
        # s390x publishes no plain build, so the rhel9 one is the default.
        (
            "openshift-client",
            ("linux", "s390x"),
            "openshift-client-linux-s390x-rhel9-4.22.13.tar.gz",
        ),
        ("oc-mirror", ("linux", "amd64"), "oc-mirror.tar.gz"),
        ("opm", ("linux", "amd64"), "opm-linux-4.22.13.tar.gz"),
    ],
)
def test_select_artifact(listing_4_22, tool, platform, expected):
    artifacts = parse_listing(listing_4_22)
    chosen = select_artifact(artifacts, tool, Platform(*platform))
    assert chosen is not None
    assert chosen.filename == expected


def test_select_artifact_prefers_rhel8_on_old_glibc(listing_4_22):
    artifacts = parse_listing(listing_4_22)
    chosen = select_artifact(
        artifacts, "openshift-client", Platform("linux", "amd64"), prefer_rhel8=True
    )
    assert chosen.filename == "openshift-client-linux-amd64-rhel8-4.22.13.tar.gz"


def test_select_artifact_falls_back_when_no_rhel8_exists(listing_4_22):
    """arm64 has no rhel8 build here, so the plain one is used instead."""
    artifacts = parse_listing(listing_4_22)
    chosen = select_artifact(
        artifacts, "openshift-client", Platform("linux", "arm64"), prefer_rhel8=True
    )
    assert chosen.filename == "openshift-client-linux-arm64-4.22.13.tar.gz"


def test_select_artifact_prefers_versioned_over_alias(listing_4_22):
    artifacts = parse_listing(listing_4_22)
    chosen = select_artifact(artifacts, "openshift-client", Platform("mac", "arm64"))
    assert chosen.filename == "openshift-client-mac-arm64-4.22.13.tar.gz"


def test_select_artifact_never_returns_source(listing_4_22):
    artifacts = parse_listing(listing_4_22)
    assert (
        select_artifact(artifacts, "openshift-client", Platform("src", "amd64")) is None
    )


def test_select_artifact_missing_platform(listing_4_22):
    artifacts = parse_listing(listing_4_22)
    assert select_artifact(artifacts, "ccoctl", Platform("mac", "arm64")) is None


def test_tool_aliases():
    assert canonical_tool_name("oc") == "openshift-client"
    assert canonical_tool_name("installer") == "openshift-install"
    assert canonical_tool_name("ccoctl") == "ccoctl"


def test_binaries_and_reverse_lookup():
    assert binaries_for_tool("openshift-client") == ("oc", "kubectl")
    assert binaries_for_tool("ccoctl") == ("ccoctl",)
    assert tool_for_binary("kubectl") == "openshift-client"
    assert tool_for_binary("oc") == "openshift-client"
    assert tool_for_binary("opm") == "opm"


@pytest.mark.parametrize(
    ("member", "expected"),
    [
        ("opm-rhel8", "opm"),
        ("oc", "oc"),
        ("dir/ccoctl", "ccoctl"),
        ("opm-rhel9", "opm"),
    ],
)
def test_normalize_member_name(member, expected):
    assert normalize_member_name(member) == expected


@pytest.mark.parametrize(
    ("member", "expected"),
    [("oc", True), ("README.md", False), ("LICENSE", False), ("opm-rhel8", True)],
)
def test_is_binary_member(member, expected):
    assert is_binary_member(member) is expected

from __future__ import annotations

import io
import os
import tarfile
import zipfile

import pytest

from latest_openshift import install, paths
from latest_openshift.errors import OpenShiftToolsError
from latest_openshift.platforms import Platform
from latest_openshift.versions import Version

LINUX = Platform("linux", "amd64")
WINDOWS = Platform("windows", "amd64")
V = Version.parse("4.22.13")


def _tarball(path, members, *, hardlinks=()):
    """Build a tarball. ``members`` is name -> (content, mode)."""
    with tarfile.open(path, "w:gz") as tf:
        for name, (content, mode) in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = mode
            tf.addfile(info, io.BytesIO(content))
        for name, target in hardlinks:
            info = tarfile.TarInfo(name)
            info.type = tarfile.LNKTYPE
            info.linkname = target
            info.mode = 0o755
            tf.addfile(info)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("openshift-install", "openshift-install-v4.22.13"),
        ("oc", "oc-v4.22.13"),
        ("oc.exe", "oc-v4.22.13.exe"),
    ],
)
def test_versioned_filename(name, expected):
    assert install.versioned_filename(name, V) == expected


def test_binary_path_is_namespaced_by_platform():
    linux = install.binary_path("oc", V, LINUX)
    arm = install.binary_path("oc", V, Platform("linux", "arm64"))
    assert linux != arm
    assert linux.parent.name == "linux-amd64"
    assert linux.name == "oc-v4.22.13"


def test_install_archive_extracts_binaries_and_skips_docs(tmp_path):
    archive = tmp_path / "openshift-client-linux-4.22.13.tar.gz"
    _tarball(
        archive,
        {"README.md": (b"docs", 0o644), "oc": (b"#!/bin/true\n", 0o755)},
        hardlinks=[("kubectl", "oc")],
    )

    installed = install.install_archive(archive, V, LINUX)

    assert sorted(item.name for item in installed) == ["kubectl", "oc"]
    for item in installed:
        assert item.path.is_file()
        assert os.access(item.path, os.X_OK)
        assert item.path.read_bytes() == b"#!/bin/true\n"
    assert not (paths.platform_bin_dir("linux", "amd64") / "README.md-v4.22.13").exists()


def test_install_archive_renames_libc_qualified_member(tmp_path):
    """opm's archive ships the binary as opm-rhel8; it installs as opm."""
    archive = tmp_path / "opm-linux-4.22.13.tar.gz"
    _tarball(archive, {"opm-rhel8": (b"binary", 0o755)})

    (installed,) = install.install_archive(archive, V, LINUX)

    assert installed.name == "opm"
    assert installed.path.name == "opm-v4.22.13"


def test_install_archive_adds_the_execute_bit(tmp_path):
    """oc-mirror ships without the execute bit set at all."""
    archive = tmp_path / "oc-mirror.tar.gz"
    _tarball(archive, {"oc-mirror": (b"binary", 0o640)})

    (installed,) = install.install_archive(archive, V, LINUX)

    assert os.access(installed.path, os.X_OK)


def test_install_archive_honours_only(tmp_path):
    archive = tmp_path / "openshift-client-linux-4.22.13.tar.gz"
    _tarball(archive, {"oc": (b"a", 0o755), "kubectl": (b"b", 0o755)})

    installed = install.install_archive(archive, V, LINUX, only=["oc"])

    assert [item.name for item in installed] == ["oc"]


def test_install_archive_reads_zip(tmp_path):
    archive = tmp_path / "openshift-client-windows-4.22.13.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("README.md", "docs")
        zf.writestr("oc.exe", "binary")

    (installed,) = install.install_archive(archive, V, WINDOWS)

    assert installed.name == "oc"
    assert installed.path.name == "oc-v4.22.13.exe"


def test_install_archive_rejects_an_archive_with_no_binaries(tmp_path):
    archive = tmp_path / "docs.tar.gz"
    _tarball(archive, {"README.md": (b"docs", 0o644)})

    with pytest.raises(OpenShiftToolsError, match="no executables"):
        install.install_archive(archive, V, LINUX)


def test_install_archive_leaves_no_partial_file_behind(tmp_path):
    archive = tmp_path / "openshift-client-linux-4.22.13.tar.gz"
    _tarball(archive, {"oc": (b"binary", 0o755)})

    install.install_archive(archive, V, LINUX)

    leftovers = list(paths.platform_bin_dir("linux", "amd64").glob("*.part"))
    assert leftovers == []


def test_find_installed_round_trip(tmp_path):
    archive = tmp_path / "opm-linux-4.22.13.tar.gz"
    _tarball(archive, {"opm": (b"binary", 0o755)})
    install.install_archive(archive, V, LINUX)

    assert install.find_installed("opm", V, LINUX) is not None
    assert install.find_installed("opm", Version.parse("4.21.0"), LINUX) is None
    assert install.find_installed("oc", V, LINUX) is None


def test_list_installed_parses_names_back(tmp_path):
    archive = tmp_path / "openshift-client-linux-4.22.13.tar.gz"
    _tarball(archive, {"oc": (b"a", 0o755), "kubectl": (b"b", 0o755)})
    install.install_archive(archive, V, LINUX)
    install.install_archive(archive, Version.parse("4.21.32"), LINUX)

    entries = install.list_installed()

    assert {(e.name, str(e.version)) for e in entries} == {
        ("oc", "4.22.13"),
        ("kubectl", "4.22.13"),
        ("oc", "4.21.32"),
        ("kubectl", "4.21.32"),
    }
    assert all(e.platform == LINUX for e in entries)


def test_list_installed_ignores_unrecognised_files():
    directory = paths.ensure_dir(paths.platform_bin_dir("linux", "amd64"))
    (directory / "stray-file").write_text("not ours")
    (directory / "oc-vnope").write_text("bad version")

    assert install.list_installed() == []


def test_install_files_copies_and_marks_executable(tmp_path):
    source = tmp_path / "openshift-install"
    source.write_bytes(b"binary")
    source.chmod(0o644)

    (installed,) = install.install_files([source], V, LINUX)

    assert installed.name == "openshift-install"
    assert installed.path.name == "openshift-install-v4.22.13"
    assert os.access(installed.path, os.X_OK)

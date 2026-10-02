from __future__ import annotations

import pytest

from latest_openshift import links
from latest_openshift.errors import OpenShiftToolsError


@pytest.fixture
def shim(tmp_path):
    path = tmp_path / "openshift-shim"
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


@pytest.fixture
def bin_dir(tmp_path):
    directory = tmp_path / "bin"
    directory.mkdir()
    return directory


def test_install_creates_symlinks(bin_dir, shim):
    results = links.install_links(["oc", "openshift-install"], bin_dir, shim=shim)

    assert [r.action for r in results] == [links.CREATED, links.CREATED]
    assert (bin_dir / "oc").is_symlink()
    assert (bin_dir / "oc").resolve() == shim.resolve()


def test_reinstalling_is_a_no_op(bin_dir, shim):
    links.install_links(["oc"], bin_dir, shim=shim)
    (result,) = links.install_links(["oc"], bin_dir, shim=shim)

    assert result.action == links.UNCHANGED


def test_a_real_file_is_not_clobbered(bin_dir, shim):
    (bin_dir / "oc").write_text("the system oc")

    (result,) = links.install_links(["oc"], bin_dir, shim=shim)

    assert result.action == links.SKIPPED
    assert (bin_dir / "oc").read_text() == "the system oc"


def test_force_replaces_a_real_file(bin_dir, shim):
    (bin_dir / "oc").write_text("the system oc")

    (result,) = links.install_links(["oc"], bin_dir, shim=shim, force=True)

    assert result.action == links.UPDATED
    assert (bin_dir / "oc").resolve() == shim.resolve()


def test_a_foreign_symlink_is_not_clobbered(bin_dir, shim, tmp_path):
    other = tmp_path / "some-other-oc"
    other.write_text("#!/bin/sh\n")
    (bin_dir / "oc").symlink_to(other)

    (result,) = links.install_links(["oc"], bin_dir, shim=shim)

    assert result.action == links.SKIPPED
    assert (bin_dir / "oc").resolve() == other.resolve()


def test_remove_only_touches_our_own_links(bin_dir, shim, tmp_path):
    other = tmp_path / "some-other-oc"
    other.write_text("#!/bin/sh\n")
    links.install_links(["oc", "opm"], bin_dir, shim=shim)
    (bin_dir / "kubectl").symlink_to(other)

    results = {
        r.name: r.action
        for r in links.remove_links(
            ["oc", "opm", "kubectl", "ccoctl"], bin_dir, shim=shim
        )
    }

    assert results == {
        "oc": links.REMOVED,
        "opm": links.REMOVED,
        "kubectl": links.SKIPPED,
        "ccoctl": links.ABSENT,
    }
    assert (bin_dir / "kubectl").is_symlink()


def test_installed_links_lists_only_ours(bin_dir, shim, tmp_path):
    other = tmp_path / "elsewhere"
    other.write_text("x")
    links.install_links(["oc", "opm"], bin_dir, shim=shim)
    (bin_dir / "kubectl").symlink_to(other)
    (bin_dir / "plain-file").write_text("x")

    found = [path.name for path in links.installed_links(bin_dir, shim=shim)]

    assert found == ["oc", "opm"]


def test_is_on_path(bin_dir, monkeypatch):
    monkeypatch.setenv("PATH", f"/usr/bin:{bin_dir}")
    assert links.is_on_path(bin_dir)

    monkeypatch.setenv("PATH", "/usr/bin")
    assert not links.is_on_path(bin_dir)


def test_shim_executable_is_reported_missing(monkeypatch, tmp_path):
    monkeypatch.setattr("sys.executable", str(tmp_path / "python"))
    monkeypatch.setattr("sys.argv", [str(tmp_path / "prog")])
    monkeypatch.setattr(links.shutil, "which", lambda name: None)

    with pytest.raises(OpenShiftToolsError, match="openshift-shim"):
        links.shim_executable()

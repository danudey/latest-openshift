from __future__ import annotations

import types

import pytest

import fake_mirror
from latest_openshift import paths, platforms
from latest_openshift.mirror import Mirror


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    """Point the cache and config at a temporary directory for every test."""
    monkeypatch.setenv(paths.CACHE_ENV, str(tmp_path / "cache"))
    monkeypatch.setenv(paths.CONFIG_ENV, str(tmp_path / "config"))
    monkeypatch.setenv("OPENSHIFT_TOOLS_BIN", str(tmp_path / "bin"))
    monkeypatch.delenv("OCP_VERSION", raising=False)
    monkeypatch.delenv("OC_VERSION", raising=False)
    monkeypatch.delenv("REGISTRY_AUTH_FILE", raising=False)
    # GitHub Actions sets CI=true, which switches the UI to plain output.
    monkeypatch.delenv("CI", raising=False)
    return tmp_path


@pytest.fixture(autouse=True)
def linux_amd64_host(monkeypatch):
    """Run every test as if on a glibc linux/amd64 machine.

    The synthetic mirror and the expectations written against it describe
    that platform, so without this the suite fails on macOS and arm64.
    Tests about detection itself still patch ``glibc_version`` over this.
    """
    monkeypatch.setattr(
        platforms,
        "_platform",
        types.SimpleNamespace(system=lambda: "Linux", machine=lambda: "x86_64"),
    )
    monkeypatch.setattr(platforms, "glibc_version", lambda: (2, 34))


@pytest.fixture
def fake() -> fake_mirror.FakeMirror:
    """A synthetic mirror tree, with request counting."""
    return fake_mirror.FakeMirror(fake_mirror.build_pages())


@pytest.fixture
def mirror(fake):
    """A :class:`Mirror` wired to the synthetic tree, with caching enabled."""
    with Mirror(client=fake.client()) as instance:
        yield instance


@pytest.fixture
def listing_4_22() -> list[str]:
    """A trimmed but faithful copy of a real ``latest-4.22`` directory index."""
    return [
        "ccoctl-linux-4.22.13.tar.gz",
        "ccoctl-linux-rhel8-4.22.13.tar.gz",
        "ccoctl-linux-rhel8.tar.gz",
        "ccoctl-linux-rhel9-4.22.13.tar.gz",
        "ccoctl-linux-rhel9.tar.gz",
        "ccoctl-linux.tar.gz",
        "oc-mirror.rhel9.tar.gz",
        "oc-mirror.tar.gz",
        "openshift-client-linux-4.22.13.tar.gz",
        "openshift-client-linux-amd64-rhel8-4.22.13.tar.gz",
        "openshift-client-linux-amd64-rhel8.tar.gz",
        "openshift-client-linux-amd64-rhel9-4.22.13.tar.gz",
        "openshift-client-linux-amd64-rhel9.tar.gz",
        "openshift-client-linux-arm64-4.22.13.tar.gz",
        "openshift-client-linux-arm64-rhel9-4.22.13.tar.gz",
        "openshift-client-linux-arm64.tar.gz",
        "openshift-client-linux-ppc64le-4.22.13.tar.gz",
        "openshift-client-linux-s390x-rhel8-4.22.13.tar.gz",
        "openshift-client-linux-s390x-rhel9-4.22.13.tar.gz",
        "openshift-client-linux.tar.gz",
        "openshift-client-mac-4.22.13.tar.gz",
        "openshift-client-mac-arm64-4.22.13.tar.gz",
        "openshift-client-mac-arm64.tar.gz",
        "openshift-client-mac.tar.gz",
        "openshift-client-src-4.22.13-x86_64.tar.gz",
        "openshift-client-windows-4.22.13.zip",
        "openshift-client-windows.zip",
        "openshift-install-linux-4.22.13.tar.gz",
        "openshift-install-linux-arm64-4.22.13.tar.gz",
        "openshift-install-mac-4.22.13.tar.gz",
        "openshift-install-mac-arm64-4.22.13.tar.gz",
        "openshift-install-rhel9-amd64.tar.gz",
        "openshift-installer-src.tar.gz",
        "opm-linux-4.22.13.tar.gz",
        "opm-linux-rhel9-4.22.13.tar.gz",
        "opm-mac-4.22.13.tar.gz",
        "opm-src-4.22.13-x86_64.tar.gz",
        "opm-windows-4.22.13.tar.gz",
        "release.txt",
        "sha256sum.txt",
        "sha256sum.txt.gpg",
    ]

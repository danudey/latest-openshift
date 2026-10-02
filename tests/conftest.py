from __future__ import annotations

import pytest

import fake_mirror
from latest_openshift import paths
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
    return tmp_path


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

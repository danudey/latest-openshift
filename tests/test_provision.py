from __future__ import annotations

import io
import tarfile

import pytest

import fake_mirror
from latest_openshift import install
from latest_openshift.errors import PrereleaseError, ToolNotFoundError
from latest_openshift.mirror import Mirror
from latest_openshift.platforms import Platform
from latest_openshift.provision import Provisioner
from latest_openshift.ui import UI
from latest_openshift.versions import Version

LINUX = Platform("linux", "amd64")
ARM = Platform("linux", "arm64")


def tarball(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tf:
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = 0o755
            tf.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


@pytest.fixture
def provisioner(fake):
    """A provisioner over the synthetic mirror, with real client archives."""
    for arch in ("x86_64", "arm64"):
        for directory in ("latest-4.22", "4.22.13"):
            base = f"{fake_mirror.BASE}/openshift-v4/{arch}/clients/ocp/{directory}"
            fake.blobs[f"{base}/openshift-client-linux-4.22.13.tar.gz"] = tarball(
                {
                    "README.md": b"docs",
                    "oc": f"oc for {arch}".encode(),
                    "kubectl": b"kubectl",
                }
            )
            fake.blobs[f"{base}/opm-linux-4.22.13.tar.gz"] = tarball(
                {"opm-rhel8": b"opm"}
            )
            fake.blobs[f"{base}/openshift-client-linux-arm64-4.22.13.tar.gz"] = tarball(
                {"oc": b"oc for cross-arm64"}
            )

    with Mirror(client=fake.client()) as mirror:
        yield Provisioner(mirror, UI(quiet=True))


def test_locate_builds_a_url(provisioner):
    release = provisioner.resolve_release(Version.parse("4.22"))
    located = provisioner.locate(release, "oc", LINUX)

    assert located.artifact.filename == "openshift-client-linux-4.22.13.tar.gz"
    assert located.url.endswith(
        "openshift-v4/x86_64/clients/ocp/latest-4.22/"
        "openshift-client-linux-4.22.13.tar.gz"
    )


def test_locate_reads_the_matching_architecture_directory(provisioner):
    """An unqualified Linux name only means arm64 inside the arm64 directory."""
    release = provisioner.resolve_release(Version.parse("4.22"))
    located = provisioner.locate(release, "oc", ARM)

    assert "openshift-v4/arm64/clients/ocp" in located.url
    assert located.artifact.filename == "openshift-client-linux-4.22.13.tar.gz"


def test_locate_reports_an_unknown_tool(provisioner):
    release = provisioner.resolve_release(Version.parse("4.22"))

    with pytest.raises(ToolNotFoundError, match="does not publish a tool called"):
        provisioner.locate(release, "kustomize", LINUX)


def test_locate_reports_a_missing_platform(provisioner):
    release = provisioner.resolve_release(Version.parse("4.22"))

    with pytest.raises(ToolNotFoundError, match="but not for windows/amd64"):
        provisioner.locate(release, "ccoctl", Platform("windows", "amd64"))


def test_tools_lists_what_a_release_publishes(provisioner):
    release = provisioner.resolve_release(Version.parse("4.22"))

    assert provisioner.tools(release, LINUX) == [
        "ccoctl",
        "oc-mirror",
        "openshift-client",
        "openshift-install",
        "opm",
    ]


def test_install_tool_extracts_every_binary(provisioner):
    release = provisioner.resolve_release(Version.parse("4.22"))

    installed = provisioner.install_tool(release, "oc", LINUX)

    assert sorted(item.name for item in installed) == ["kubectl", "oc"]
    assert install.find_installed("oc", Version.parse("4.22.13"), LINUX) is not None


def test_install_tool_renames_the_libc_qualified_binary(provisioner):
    release = provisioner.resolve_release(Version.parse("4.22"))

    (installed,) = provisioner.install_tool(release, "opm", LINUX)

    assert installed.name == "opm"


def test_install_tool_reuses_the_cache(provisioner, fake):
    release = provisioner.resolve_release(Version.parse("4.22"))
    provisioner.install_tool(release, "oc", LINUX)
    downloads = sum(1 for url in fake.requests if url.endswith(".tar.gz"))

    provisioner.install_tool(release, "oc", LINUX)

    assert sum(1 for url in fake.requests if url.endswith(".tar.gz")) == downloads


def test_ensure_binary_downloads_then_caches(provisioner, fake):
    path, release = provisioner.ensure_binary("oc", Version.parse("4.22"))

    assert path.name == "oc-v4.22.13"
    assert str(release.version) == "4.22.13"
    assert path.read_bytes() == b"oc for x86_64"


def test_ensure_binary_short_circuits_an_exact_cached_version(provisioner, fake):
    provisioner.ensure_binary("oc", Version.parse("4.22.13"))
    before = len(fake.requests)

    path, _ = provisioner.ensure_binary("oc", Version.parse("4.22.13"))

    assert path.name == "oc-v4.22.13"
    # A fully pinned, already-installed version must not touch the network.
    assert len(fake.requests) == before


def test_ensure_binary_maps_kubectl_to_the_client_archive(provisioner):
    path, _ = provisioner.ensure_binary("kubectl", Version.parse("4.22"))

    assert path.name == "kubectl-v4.22.13"


def test_release_image_path_needs_a_pull_secret(provisioner, monkeypatch):
    monkeypatch.setattr("latest_openshift.prerelease.FALLBACK_PULL_SECRETS", ())
    release = provisioner.resolve_release(Version.parse("4.30.1"))
    assert release.is_prerelease_image

    with pytest.raises(PrereleaseError, match="pull secret"):
        provisioner.install_tool(release, "oc", LINUX)


def test_locate_refuses_a_release_that_is_not_on_the_mirror(provisioner):
    release = provisioner.resolve_release(Version.parse("4.30.1"))

    with pytest.raises(ToolNotFoundError, match="not on the mirror"):
        provisioner.locate(release, "oc", LINUX)


def test_release_image_bootstraps_oc_then_extracts(fake, tmp_path, monkeypatch):
    """The last resort: bootstrap oc from the mirror, then use it on quay.io.

    This is the third step of the same fallback chain the shell installer
    uses -- ocp, then ocp-dev-preview, then the release image.
    """
    oc_script = (
        b"#!/bin/sh\n"
        b'if [ "$3" = "info" ]; then exit 0; fi\n'
        b"while [ $# -gt 0 ]; do\n"
        b'  case "$1" in\n'
        b'    --command) cmd="$2"; shift 2 ;;\n'
        b'    --to) to="$2"; shift 2 ;;\n'
        b"    *) shift ;;\n"
        b"  esac\n"
        b"done\n"
        b'mkdir -p "$to"; printf "extracted" > "$to/$cmd"\n'
    )
    base = f"{fake_mirror.BASE}/openshift-v4/x86_64/clients/ocp/latest-4.22"
    fake.blobs[f"{base}/openshift-client-linux-4.22.13.tar.gz"] = tarball(
        {"oc": oc_script}
    )

    secret = tmp_path / "pull-secret.json"
    secret.write_text('{"auths": {}}')
    monkeypatch.setenv("REGISTRY_AUTH_FILE", str(secret))

    with Mirror(client=fake.client()) as mirror:
        provisioner = Provisioner(mirror, UI(quiet=True))
        release = provisioner.resolve_release(Version.parse("4.30.1"))
        assert release.is_prerelease_image

        (installed,) = provisioner.install_tool(
            release, "openshift-install", LINUX, only=["openshift-install"]
        )

    assert installed.path.name == "openshift-install-v4.30.1"
    assert installed.path.read_text() == "extracted"


def test_release_image_reports_an_inaccessible_image(fake, tmp_path, monkeypatch):
    base = f"{fake_mirror.BASE}/openshift-v4/x86_64/clients/ocp/latest-4.22"
    fake.blobs[f"{base}/openshift-client-linux-4.22.13.tar.gz"] = tarball(
        {"oc": b"#!/bin/sh\nexit 1\n"}
    )
    secret = tmp_path / "pull-secret.json"
    secret.write_text("{}")
    monkeypatch.setenv("REGISTRY_AUTH_FILE", str(secret))

    with Mirror(client=fake.client()) as mirror:
        provisioner = Provisioner(mirror, UI(quiet=True))
        release = provisioner.resolve_release(Version.parse("4.30.1"))

        with pytest.raises(PrereleaseError, match="could not be read"):
            provisioner.install_tool(release, "oc", LINUX)

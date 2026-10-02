from __future__ import annotations

import io
import tarfile

import pytest

import fake_mirror
from latest_openshift import install, shim
from latest_openshift.config import Config
from latest_openshift.mirror import Mirror
from latest_openshift.platforms import current_platform
from latest_openshift.versions import Version


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
def execs(monkeypatch):
    """Capture the exec the shim would perform instead of performing it."""
    calls = []

    def fake_execv(path, argv):
        calls.append((path, argv))
        raise SystemExit(0)

    monkeypatch.setattr(shim.os, "execv", fake_execv)
    return calls


@pytest.fixture
def wired(fake, monkeypatch):
    """Point the shim's mirror at the synthetic tree, with real archives."""
    arch_dir = (
        "x86_64" if current_platform().arch == "amd64" else current_platform().arch
    )
    for directory in ("latest-4.22", "latest-4.21", "4.22.13"):
        base = f"{fake_mirror.BASE}/openshift-v4/{arch_dir}/clients/ocp/{directory}"
        version = "4.21.32" if directory == "latest-4.21" else "4.22.13"
        fake.blobs[f"{base}/openshift-client-linux-{version}.tar.gz"] = tarball(
            {"oc": f"oc {version}".encode(), "kubectl": b"kubectl"}
        )
    monkeypatch.setattr(shim, "Mirror", lambda **kwargs: Mirror(client=fake.client()))
    return fake


def run_shim(argv, execs):
    with pytest.raises(SystemExit):
        shim.main(argv)
    assert execs, "the shim did not exec anything"
    return execs[-1]


def test_dispatches_on_argv0(wired, execs):
    path, argv = run_shim(["/somewhere/bin/oc", "version", "--client"], execs)

    assert path.endswith("oc-v4.22.13")
    assert argv == ["oc", "version", "--client"]


def test_passes_every_argument_through(wired, execs):
    _, argv = run_shim(["/bin/oc", "get", "pods", "-o", "json", "--", "-x"], execs)

    assert argv == ["oc", "get", "pods", "-o", "json", "--", "-x"]


def test_takes_no_arguments_of_its_own(wired, execs):
    """A --help meant for the wrapped tool must not be eaten by the shim."""
    _, argv = run_shim(["/bin/oc", "--help"], execs)

    assert argv == ["oc", "--help"]


def test_uses_oc_version_from_the_environment(wired, execs, monkeypatch):
    monkeypatch.setenv("OC_VERSION", "4.21")

    path, _ = run_shim(["/bin/oc"], execs)

    assert path.endswith("oc-v4.21.32")


def test_ocp_version_beats_oc_version(wired, execs, monkeypatch):
    monkeypatch.setenv("OCP_VERSION", "4.22")
    monkeypatch.setenv("OC_VERSION", "4.21")

    path, _ = run_shim(["/bin/oc"], execs)

    assert path.endswith("oc-v4.22.13")


def test_uses_the_configured_default(wired, execs):
    config = Config.load()
    config.default_version = Version.parse("4.21")
    config.save()

    path, _ = run_shim(["/bin/oc"], execs)

    assert path.endswith("oc-v4.21.32")


def test_the_environment_beats_the_configured_default(wired, execs, monkeypatch):
    config = Config.load()
    config.default_version = Version.parse("4.21")
    config.save()
    monkeypatch.setenv("OC_VERSION", "4.22")

    path, _ = run_shim(["/bin/oc"], execs)

    assert path.endswith("oc-v4.22.13")


def test_an_incomplete_version_is_resolved_and_downloaded(wired, execs, monkeypatch):
    monkeypatch.setenv("OC_VERSION", "4")

    path, _ = run_shim(["/bin/oc"], execs)

    assert path.endswith("oc-v4.22.13")


def test_a_pinned_cached_version_needs_no_network(wired, execs, monkeypatch):
    monkeypatch.setenv("OC_VERSION", "4.22.13")
    run_shim(["/bin/oc"], execs)
    before = len(wired.requests)

    run_shim(["/bin/oc"], execs)

    assert len(wired.requests) == before


def test_kubectl_comes_from_the_client_archive(wired, execs):
    path, argv = run_shim(["/bin/kubectl"], execs)

    assert path.endswith("kubectl-v4.22.13")
    assert argv[0] == "kubectl"


def test_direct_invocation_needs_a_binary_name(wired, execs, capsys):
    assert shim.main(["/venv/bin/openshift-shim"]) == 2
    assert "usage:" in capsys.readouterr().err


def test_direct_invocation_with_a_binary_name(wired, execs):
    path, argv = run_shim(["/venv/bin/openshift-shim", "oc", "whoami"], execs)

    assert path.endswith("oc-v4.22.13")
    assert argv == ["oc", "whoami"]


def test_falls_back_to_the_cache_when_the_mirror_is_unreachable(
    wired, execs, monkeypatch
):
    """A tool that already works offline should keep working offline."""
    run_shim(["/bin/oc"], execs)
    installed = install.find_installed(
        "oc", Version.parse("4.22.13"), current_platform()
    )
    assert installed is not None

    broken = fake_mirror.FakeMirror({})
    monkeypatch.setattr(shim, "Mirror", lambda **kwargs: Mirror(client=broken.client()))

    path, _ = run_shim(["/bin/oc"], execs)

    assert path == str(installed)


def test_reports_an_unknown_binary(wired, capsys):
    assert shim.main(["/bin/not-an-openshift-tool"]) == 1
    assert "does not publish a tool called" in capsys.readouterr().err


def test_strips_an_executable_extension_when_looking_the_tool_up(wired, execs):
    path, argv = run_shim(["/tools/oc.exe"], execs)

    assert path.endswith("oc-v4.22.13")
    # The wrapped program still sees the name it was invoked as.
    assert argv[0] == "oc.exe"

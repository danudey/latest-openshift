from __future__ import annotations

import io
import tarfile

import pytest
from click.testing import CliRunner

import fake_mirror
from latest_openshift import cli
from latest_openshift.mirror import Mirror


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
def run(fake, monkeypatch):
    """Invoke the CLI against the synthetic mirror, capturing streams apart."""
    base = f"{fake_mirror.BASE}/openshift-v4/x86_64/clients/ocp/latest-4.22"
    fake.blobs[f"{base}/openshift-client-linux-4.22.13.tar.gz"] = tarball(
        {"README.md": b"docs", "oc": b"oc", "kubectl": b"kubectl"}
    )

    monkeypatch.setattr(cli, "Mirror", lambda **kwargs: Mirror(client=fake.client()))
    runner = CliRunner()

    def invoke(*args):
        return runner.invoke(cli.main, list(args), catch_exceptions=False)

    return invoke


def out_lines(result):
    """Only the data stream: stderr carries progress and must stay out of it."""
    return [line for line in result.stdout.splitlines() if line]


def test_latest(run):
    result = run("latest")
    assert result.exit_code == 0
    assert out_lines(result) == ["4.22.13"]


def test_latest_for_a_stream(run):
    assert out_lines(run("latest", "4.21")) == ["4.21.32"]


def test_latest_for_a_major(run):
    assert out_lines(run("latest", "4")) == ["4.22.13"]


def test_latest_rejects_an_exact_version(run):
    result = run("latest", "4.22.13")
    assert result.exit_code != 0
    assert "already an exact version" in result.output


def test_latest_rejects_nonsense(run):
    assert run("latest", "banana").exit_code != 0


def test_list_prints_bare_versions_when_piped(run):
    assert out_lines(run("list")) == ["4.22.13", "4.21.32", "4.20.37"]


def test_list_respects_count(run):
    assert out_lines(run("list", "-n", "2")) == ["4.22.13", "4.21.32"]


def test_tools(run):
    assert out_lines(run("tools")) == [
        "ccoctl",
        "oc-mirror",
        "openshift-client",
        "openshift-install",
        "opm",
    ]


def test_download_url(run):
    (line,) = out_lines(run("download", "oc", "--url"))
    assert line.endswith("latest-4.22/openshift-client-linux-4.22.13.tar.gz")


def test_download_url_for_another_platform(run):
    (line,) = out_lines(run("download", "oc", "--url", "-p", "mac/arm64"))
    assert line.endswith("openshift-client-mac-arm64-4.22.13.tar.gz")


def test_download_url_for_several_tools(run):
    lines = out_lines(run("download", "oc", "openshift-install", "--url"))
    assert len(lines) == 2


def test_download_saves_an_archive(run, tmp_path):
    destination = tmp_path / "downloads"
    (line,) = out_lines(run("download", "oc", "-d", str(destination)))

    saved = destination / "openshift-client-linux-4.22.13.tar.gz"
    assert line == str(saved)
    assert saved.is_file()


def test_download_install_print_location(run):
    lines = out_lines(run("download", "oc", "--install", "--print-location"))

    assert len(lines) == 2
    assert {line.rsplit("/", 1)[-1] for line in lines} == {
        "oc-v4.22.13",
        "kubectl-v4.22.13",
    }
    assert all(line.startswith("/") for line in lines)


def test_print_location_requires_install(run):
    result = run("download", "oc", "--print-location")
    assert result.exit_code != 0
    assert "--print-location only makes sense with --install" in result.output


def test_url_and_install_conflict(run):
    result = run("download", "oc", "--url", "--install")
    assert result.exit_code != 0


def test_download_reports_an_unknown_tool(run):
    result = run("download", "kustomize", "--url")
    assert result.exit_code != 0
    assert "does not publish a tool called" in result.output


def test_path_downloads_and_prints(run):
    (line,) = out_lines(run("path", "oc"))
    assert line.endswith("oc-v4.22.13")


def test_default_round_trip(run):
    assert run("default", "set", "4.21").exit_code == 0
    assert out_lines(run("default", "show")) == ["4.21"]
    assert out_lines(run("default", "show", "--resolved")) == ["4.21.32"]
    assert run("default", "clear").exit_code == 0
    assert run("default", "show").exit_code == 1


def test_default_affects_other_commands(run):
    run("default", "set", "4.20")
    (line,) = out_lines(run("download", "oc", "--url"))
    assert "4.20.37" in line


def test_explicit_version_beats_the_default(run):
    run("default", "set", "4.20")
    (line,) = out_lines(run("download", "oc", "--url", "-V", "4.21"))
    assert "4.21.32" in line


def test_oc_version_environment_beats_the_default(run, monkeypatch):
    run("default", "set", "4.20")
    monkeypatch.setenv("OC_VERSION", "4.21")
    (line,) = out_lines(run("download", "oc", "--url"))
    assert "4.21.32" in line


def test_default_set_rejects_an_unreleased_version(run):
    result = run("default", "set", "4.99")
    assert result.exit_code != 0


def test_link_and_unlink(run, tmp_path):
    bin_dir = tmp_path / "bin"

    result = run("link", "-d", str(bin_dir))
    assert result.exit_code == 0
    assert sorted(p.name for p in bin_dir.iterdir()) == [
        "ccoctl",
        "kubectl",
        "oc",
        "oc-mirror",
        "openshift-install",
        "opm",
    ]
    assert all(p.is_symlink() for p in bin_dir.iterdir())

    assert run("unlink", "-d", str(bin_dir)).exit_code == 0
    assert list(bin_dir.iterdir()) == []


def test_link_a_single_tool(run, tmp_path):
    bin_dir = tmp_path / "bin"
    run("link", "-d", str(bin_dir), "--tool", "openshift-install")

    assert [p.name for p in bin_dir.iterdir()] == ["openshift-install"]


def test_link_set_default(run, tmp_path):
    run("link", "-d", str(tmp_path / "bin"), "-V", "4.21", "--set-default")
    assert out_lines(run("default", "show")) == ["4.21"]


def test_cache_list_and_path(run):
    assert out_lines(run("cache", "path"))[0].endswith("cache")

    run("download", "oc", "--install")
    lines = out_lines(run("cache", "list"))
    assert any(line.endswith("oc-v4.22.13") for line in lines)


def test_info(run):
    result = run("info")
    assert result.exit_code == 0
    assert "4.22.13" in result.output


def test_stdout_carries_only_data(run):
    """The contract that makes $(latest-openshift latest) usable in a script."""
    result = run("latest")

    assert result.stdout == "4.22.13\n"


def test_progress_and_status_go_to_stderr(run):
    result = run("download", "oc", "--install", "--print-location")

    assert "OpenShift 4.22.13" in result.stderr
    assert "OpenShift" not in result.stdout
    assert all(line.startswith("/") for line in result.stdout.splitlines() if line)


def test_quiet_leaves_stdout_alone(run):
    result = run("-q", "latest")

    assert result.stdout == "4.22.13\n"
    assert result.stderr == ""

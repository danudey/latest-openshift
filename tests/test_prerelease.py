from __future__ import annotations

import textwrap

import pytest

from latest_openshift import prerelease
from latest_openshift.config import Config
from latest_openshift.errors import PrereleaseError
from latest_openshift.platforms import Platform, current_platform
from latest_openshift.versions import Version

V = Version.parse("4.30.1")


@pytest.fixture
def secret_file(tmp_path):
    path = tmp_path / "pull-secret.json"
    path.write_text('{"auths": {}}')
    return path


def fake_oc(tmp_path, script: str):
    """A stand-in for the oc binary that records how it was called."""
    path = tmp_path / "oc"
    path.write_text("#!/bin/sh\n" + textwrap.dedent(script))
    path.chmod(0o755)
    return path


def test_release_image_reference():
    assert prerelease.release_image(V) == (
        "quay.io/openshift-release-dev/ocp-release:4.30.1-multi"
    )


def test_pull_secret_from_the_environment(monkeypatch, secret_file):
    monkeypatch.setenv("REGISTRY_AUTH_FILE", str(secret_file))

    found = prerelease.find_pull_secret(Config())

    assert found.path == secret_file
    assert found.origin == "REGISTRY_AUTH_FILE"


def test_a_bad_environment_pull_secret_is_an_error(monkeypatch, tmp_path):
    monkeypatch.setenv("REGISTRY_AUTH_FILE", str(tmp_path / "nope.json"))

    with pytest.raises(PrereleaseError, match="does not exist"):
        prerelease.find_pull_secret(Config())


def test_pull_secret_from_the_config(secret_file, monkeypatch):
    monkeypatch.setattr(prerelease, "FALLBACK_PULL_SECRETS", ())

    found = prerelease.find_pull_secret(Config(pull_secret=secret_file))

    assert found.path == secret_file
    assert found.origin == "config pull_secret"


def test_the_environment_beats_the_config(monkeypatch, tmp_path, secret_file):
    other = tmp_path / "other.json"
    other.write_text("{}")
    monkeypatch.setenv("REGISTRY_AUTH_FILE", str(other))

    assert prerelease.find_pull_secret(Config(pull_secret=secret_file)).path == other


def test_no_pull_secret_found(monkeypatch):
    monkeypatch.setattr(prerelease, "FALLBACK_PULL_SECRETS", ())

    assert prerelease.find_pull_secret(Config()) is None
    assert "pull secret" in prerelease.describe_missing_pull_secret()


def test_image_accessible_runs_release_info(tmp_path, secret_file):
    log = tmp_path / "argv.log"
    oc = fake_oc(tmp_path, f'echo "$@" > {log}\nexit 0\n')
    secret = prerelease.PullSecret(path=secret_file, origin="test")

    assert prerelease.image_accessible(oc, V, secret) is True
    recorded = log.read_text()
    assert "adm release info" in recorded
    assert str(secret_file) in recorded
    assert "ocp-release:4.30.1-multi" in recorded


def test_image_accessible_is_false_when_oc_fails(tmp_path, secret_file):
    oc = fake_oc(tmp_path, "exit 1\n")
    secret = prerelease.PullSecret(path=secret_file, origin="test")

    assert prerelease.image_accessible(oc, V, secret) is False


def test_extract_commands_produces_binaries(tmp_path, secret_file):
    destination = tmp_path / "out"
    oc = fake_oc(
        tmp_path,
        """
        while [ $# -gt 0 ]; do
          case "$1" in
            --command) cmd="$2"; shift 2 ;;
            --to) to="$2"; shift 2 ;;
            *) shift ;;
          esac
        done
        mkdir -p "$to"
        printf 'binary' > "$to/$cmd"
        """,
    )
    secret = prerelease.PullSecret(path=secret_file, origin="test")

    produced = prerelease.extract_commands(
        oc, V, secret, ["oc", "openshift-install"], destination
    )

    assert [path.name for path in produced] == ["oc", "openshift-install"]
    assert all(path.read_text() == "binary" for path in produced)


def test_extract_commands_reports_a_failure(tmp_path, secret_file):
    oc = fake_oc(tmp_path, 'echo "unauthorized" >&2\nexit 1\n')
    secret = prerelease.PullSecret(path=secret_file, origin="test")

    with pytest.raises(PrereleaseError, match="unauthorized"):
        prerelease.extract_commands(oc, V, secret, ["oc"], tmp_path / "out")


def test_extract_commands_notices_a_missing_binary(tmp_path, secret_file):
    oc = fake_oc(tmp_path, "exit 0\n")
    secret = prerelease.PullSecret(path=secret_file, origin="test")

    with pytest.raises(PrereleaseError, match="did not provide"):
        prerelease.extract_commands(oc, V, secret, ["oc"], tmp_path / "out")


def test_cross_platform_extraction_passes_command_os(tmp_path, secret_file):
    log = tmp_path / "argv.log"
    oc = fake_oc(
        tmp_path,
        f"""
        echo "$@" >> {log}
        while [ $# -gt 0 ]; do
          case "$1" in
            --command) cmd="$2"; shift 2 ;;
            --to) to="$2"; shift 2 ;;
            *) shift ;;
          esac
        done
        mkdir -p "$to"; printf 'x' > "$to/$cmd"
        """,
    )
    secret = prerelease.PullSecret(path=secret_file, origin="test")

    prerelease.extract_commands(
        oc, V, secret, ["oc"], tmp_path / "out", platform=Platform("mac", "arm64")
    )

    assert "--command-os darwin/arm64" in log.read_text()


def test_native_extraction_omits_command_os(tmp_path, secret_file):
    log = tmp_path / "argv.log"
    oc = fake_oc(
        tmp_path,
        f"""
        echo "$@" >> {log}
        while [ $# -gt 0 ]; do
          case "$1" in
            --command) cmd="$2"; shift 2 ;;
            --to) to="$2"; shift 2 ;;
            *) shift ;;
          esac
        done
        mkdir -p "$to"; printf 'x' > "$to/$cmd"
        """,
    )
    secret = prerelease.PullSecret(path=secret_file, origin="test")

    prerelease.extract_commands(
        oc, V, secret, ["oc"], tmp_path / "out", platform=current_platform()
    )

    assert "--command-os" not in log.read_text()


def test_a_missing_oc_is_reported(tmp_path, secret_file):
    secret = prerelease.PullSecret(path=secret_file, origin="test")

    with pytest.raises(PrereleaseError, match="not executable"):
        prerelease.image_accessible(tmp_path / "no-such-oc", V, secret)

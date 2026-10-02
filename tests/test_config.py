from __future__ import annotations

import stat

import pytest

from latest_openshift import paths
from latest_openshift.config import Config, requested_version
from latest_openshift.errors import ConfigError, InvalidVersionError
from latest_openshift.versions import Version


def test_missing_config_loads_as_empty():
    config = Config.load()
    assert config.default_version is None
    assert config.pull_secret is None


def test_round_trip(tmp_path):
    config = Config.load()
    config.default_version = Version.parse("4.22")
    config.pull_secret = tmp_path / "pull-secret.json"
    written = config.save()

    assert written == paths.config_file()
    reloaded = Config.load()
    assert reloaded.default_version == Version.parse("4.22")
    assert reloaded.pull_secret == tmp_path / "pull-secret.json"


def test_saving_keeps_the_config_owner_only(tmp_path):
    config = Config.load()
    config.pull_secret = tmp_path / "pull-secret.json"
    written = config.save()

    assert stat.S_IMODE(written.stat().st_mode) == 0o600

    # A config left behind by an older version is tightened on the next save.
    written.chmod(0o644)
    config.save()
    assert stat.S_IMODE(written.stat().st_mode) == 0o600


def test_clearing_the_default_removes_it():
    config = Config.load()
    config.default_version = Version.parse("4.22")
    config.save()

    config.default_version = None
    config.save()

    assert Config.load().default_version is None


def test_a_broken_config_is_reported_clearly():
    paths.config_file().parent.mkdir(parents=True, exist_ok=True)
    paths.config_file().write_text("default_version = [1, 2\n")

    with pytest.raises(ConfigError):
        Config.load()


def test_an_unparseable_version_is_reported_clearly():
    paths.config_file().parent.mkdir(parents=True, exist_ok=True)
    paths.config_file().write_text('default_version = "banana"\n')

    with pytest.raises(ConfigError):
        Config.load()


def test_requested_version_precedence(monkeypatch):
    config = Config(default_version=Version.parse("4.20"))

    # Explicit beats everything.
    monkeypatch.setenv("OC_VERSION", "4.21")
    assert requested_version("4.22", config=config) == Version.parse("4.22")

    # Then the environment.
    assert requested_version(None, config=config) == Version.parse("4.21")

    # Then the configured default.
    monkeypatch.delenv("OC_VERSION")
    assert requested_version(None, config=config) == Version.parse("4.20")

    # And otherwise nothing, meaning "the newest release".
    assert requested_version(None, config=Config()) is None


def test_ocp_version_beats_oc_version(monkeypatch):
    monkeypatch.setenv("OCP_VERSION", "4.22")
    monkeypatch.setenv("OC_VERSION", "4.21")
    assert requested_version(None, config=Config()) == Version.parse("4.22")

    # A blank OCP_VERSION falls through to OC_VERSION.
    monkeypatch.setenv("OCP_VERSION", "  ")
    assert requested_version(None, config=Config()) == Version.parse("4.21")


def test_a_bad_ocp_version_is_rejected_even_with_oc_version_set(monkeypatch):
    monkeypatch.setenv("OCP_VERSION", "not-a-version")
    monkeypatch.setenv("OC_VERSION", "4.21")
    with pytest.raises(InvalidVersionError):
        requested_version(None, config=Config())


def test_a_blank_environment_variable_is_ignored(monkeypatch):
    monkeypatch.setenv("OC_VERSION", "  ")
    config = Config(default_version=Version.parse("4.20"))
    assert requested_version(None, config=config) == Version.parse("4.20")


def test_a_bad_environment_variable_is_rejected(monkeypatch):
    monkeypatch.setenv("OC_VERSION", "not-a-version")
    with pytest.raises(InvalidVersionError):
        requested_version(None, config=Config())


def test_paths_honour_their_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.CACHE_ENV, str(tmp_path / "c"))
    monkeypatch.setenv(paths.CONFIG_ENV, str(tmp_path / "k"))
    assert paths.cache_root() == tmp_path / "c"
    assert paths.config_file() == tmp_path / "k" / "config.toml"

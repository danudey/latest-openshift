"""Where this tool keeps its cache, its config and the binaries it installs.

Layout under the cache root (``~/.cache/openshift-tools`` by default)::

    bin/linux-amd64/openshift-install-v4.22.13   installed, versioned binaries
    archives/                                    downloaded tarballs
    http/                                        cached directory listings

The config root (``~/.config/openshift-tools`` by default) holds ``config.toml``
with the default version and other persistent settings.
"""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "openshift-tools"

#: Overrides the cache root wholesale.
CACHE_ENV = "OPENSHIFT_TOOLS_CACHE"
#: Overrides the config root wholesale.
CONFIG_ENV = "OPENSHIFT_TOOLS_CONFIG"


def _xdg_dir(env_var: str, default: Path) -> Path:
    value = os.environ.get(env_var)
    if value:
        return Path(value).expanduser()
    return default


def cache_root() -> Path:
    """Root of the download and binary cache."""
    override = os.environ.get(CACHE_ENV)
    if override:
        return Path(override).expanduser()
    base = _xdg_dir("XDG_CACHE_HOME", Path.home() / ".cache")
    return base / APP_NAME


def config_root() -> Path:
    """Directory holding ``config.toml``."""
    override = os.environ.get(CONFIG_ENV)
    if override:
        return Path(override).expanduser()
    base = _xdg_dir("XDG_CONFIG_HOME", Path.home() / ".config")
    return base / APP_NAME


def config_file() -> Path:
    return config_root() / "config.toml"


def bin_root() -> Path:
    """Parent of the per-platform directories holding installed binaries."""
    return cache_root() / "bin"


def platform_bin_dir(os_name: str, arch: str) -> Path:
    """Directory holding installed binaries for one target platform."""
    return bin_root() / f"{os_name}-{arch}"


def archive_dir() -> Path:
    return cache_root() / "archives"


def http_cache_dir() -> Path:
    return cache_root() / "http"


def default_symlink_dir() -> Path:
    """Where ``link`` installs shim symlinks unless told otherwise."""
    override = os.environ.get("OPENSHIFT_TOOLS_BIN")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".local" / "bin"


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path

"""Persistent settings: the default OpenShift version, and the pull secret.

Stored as TOML so it stays editable by hand.  Only flat string keys are
supported, which keeps the writer honest without pulling in a TOML serializer.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import paths
from .errors import ConfigError
from .versions import Version

#: Override the configured default version for a single invocation, checked in
#: this order. The shim reads these too, which is how a shell can pin one
#: command to one version. ``OC_VERSION`` is the older name, kept as a fallback.
VERSION_ENVS = ("OCP_VERSION", "OC_VERSION")

#: Path to a Red Hat pull secret, for prerelease release images.
PULL_SECRET_ENV = "REGISTRY_AUTH_FILE"

_KNOWN_KEYS = ("default_version", "pull_secret")


@dataclass
class Config:
    """The contents of ``config.toml``."""

    default_version: Version | None = None
    pull_secret: Path | None = None
    path: Path | None = None

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        target = path or paths.config_file()
        if not target.is_file():
            return cls(path=target)

        try:
            raw = tomllib.loads(target.read_text("utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"could not read {target}: {exc}") from exc

        default_version = None
        if (value := raw.get("default_version")) is not None:
            if not isinstance(value, str):
                raise ConfigError(f"{target}: default_version must be a string")
            try:
                default_version = Version.parse(value)
            except Exception as exc:
                raise ConfigError(f"{target}: {exc}") from exc

        pull_secret = None
        if (value := raw.get("pull_secret")) is not None:
            if not isinstance(value, str):
                raise ConfigError(f"{target}: pull_secret must be a string")
            pull_secret = Path(value).expanduser()

        return cls(
            default_version=default_version, pull_secret=pull_secret, path=target
        )

    def save(self, path: Path | None = None) -> Path:
        target = path or self.path or paths.config_file()
        paths.ensure_dir(target.parent)

        lines = [
            "# Settings for the latest-openshift CLI and its tool shims.",
            "",
        ]
        if self.default_version is not None:
            lines.append(f'default_version = "{self.default_version}"')
        if self.pull_secret is not None:
            lines.append(f'pull_secret = "{_escape(str(self.pull_secret))}"')
        lines.append("")

        # The file names where the pull secret lives, so keep it owner-only.
        tmp = target.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines))
        # os.open leaves an existing file's mode alone, so set it either way.
        os.chmod(tmp, 0o600)
        tmp.replace(target)
        self.path = target
        return target


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def requested_version(
    explicit: str | None = None,
    *,
    environ: dict[str, str] | None = None,
    config: Config | None = None,
) -> Version | None:
    """The version to use, in precedence order.

    An explicit command-line value wins, then ``OCP_VERSION``, then
    ``OC_VERSION``, then the configured default.  ``None`` means "whatever the
    newest release is".
    """
    env = os.environ if environ is None else environ

    if explicit:
        return Version.parse(explicit)

    for name in VERSION_ENVS:
        from_env = env.get(name, "").strip()
        if from_env:
            return Version.parse(from_env)

    cfg = config if config is not None else Config.load()
    return cfg.default_version

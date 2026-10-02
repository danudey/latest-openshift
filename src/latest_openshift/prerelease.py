"""Obtaining tools for versions that never reached the mirror.

Some OpenShift versions exist only as a release image on ``quay.io``.  Getting
a binary out of one means authenticating with a Red Hat pull secret and running
``oc adm release extract``, which in turn needs an ``oc`` binary -- so a
known-good ``oc`` from the mirror is bootstrapped first.

This is the third and last source tried, after the ``ocp`` and
``ocp-dev-preview`` mirror channels, matching the shell installer it replaces.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .config import PULL_SECRET_ENV, Config
from .errors import PrereleaseError
from .platforms import Platform, current_platform
from .versions import Version

RELEASE_IMAGE_REPO = "quay.io/openshift-release-dev/ocp-release"

#: Conventional locations for a Red Hat pull secret, tried in order after
#: REGISTRY_AUTH_FILE and the configured path.
FALLBACK_PULL_SECRETS = (
    Path("~/.config/openshift-tools/pull-secret.json"),
    Path("~/.docker/config.json"),
    Path("~/.config/containers/auth.json"),
)

_INFO_TIMEOUT = 120
_EXTRACT_TIMEOUT = 900


@dataclass(frozen=True)
class PullSecret:
    """A resolved pull secret file and where it was found."""

    path: Path
    origin: str


def release_image(version: Version) -> str:
    """The multi-arch release image reference for a version."""
    return f"{RELEASE_IMAGE_REPO}:{version}-multi"


def find_pull_secret(config: Config | None = None) -> PullSecret | None:
    """Locate a usable pull secret, or ``None``.

    ``REGISTRY_AUTH_FILE`` always wins, so a caller can override whatever is
    configured, exactly as the shell installer does.
    """
    from_env = os.environ.get(PULL_SECRET_ENV, "").strip()
    if from_env:
        path = Path(from_env).expanduser()
        if not path.is_file():
            raise PrereleaseError(
                f"{PULL_SECRET_ENV} is set to {from_env!r} but that file does not exist"
            )
        return PullSecret(path=path, origin=PULL_SECRET_ENV)

    cfg = config if config is not None else Config.load()
    if cfg.pull_secret is not None:
        path = cfg.pull_secret.expanduser()
        if path.is_file():
            return PullSecret(path=path, origin="config pull_secret")

    for candidate in FALLBACK_PULL_SECRETS:
        path = candidate.expanduser()
        if path.is_file():
            return PullSecret(path=path, origin=str(candidate))

    return None


def describe_missing_pull_secret() -> str:
    """The message shown when no pull secret could be found."""
    searched = ", ".join(str(path) for path in FALLBACK_PULL_SECRETS)
    return (
        "OpenShift prereleases require a Red Hat pull secret. Set "
        f"{PULL_SECRET_ENV}, or run 'latest-openshift default set-pull-secret "
        f"<path>'. Looked in: {searched}"
    )


def image_accessible(oc_binary: Path, version: Version, secret: PullSecret) -> bool:
    """Whether the release image for ``version`` can actually be read."""
    result = _run(
        [
            str(oc_binary),
            "adm",
            "release",
            "info",
            "-a",
            str(secret.path),
            release_image(version),
        ],
        timeout=_INFO_TIMEOUT,
    )
    return result.returncode == 0


def extract_commands(
    oc_binary: Path,
    version: Version,
    secret: PullSecret,
    commands: Iterable[str],
    destination: Path,
    *,
    platform: Platform | None = None,
) -> list[Path]:
    """Extract named commands from a release image into ``destination``.

    ``oc adm release extract`` takes one ``--command`` at a time, so this runs
    once per binary.  Returns the files it produced.
    """
    destination.mkdir(parents=True, exist_ok=True)
    target = platform or current_platform()
    extracted: list[Path] = []

    for command in commands:
        argv = [
            str(oc_binary),
            "adm",
            "release",
            "extract",
            "-a",
            str(secret.path),
            "--command",
            command,
            "--to",
            str(destination),
        ]
        if target != current_platform():
            # Only newer oc releases know this flag; if it is rejected there is
            # no way to cross-extract, and the error below says so.
            argv += ["--command-os", f"{_go_os(target.os)}/{target.arch}"]
        argv.append(release_image(version))

        result = _run(argv, timeout=_EXTRACT_TIMEOUT)
        if result.returncode != 0:
            detail = _last_line(result.stderr) or "oc adm release extract failed"
            hint = ""
            if "unauthorized" in detail or "authentication" in detail:
                # Reading the release manifest and pulling its payload need
                # different entitlements, so a secret that got this far can
                # still fail here.
                hint = (
                    f" The pull secret in use ({secret.path}, from "
                    f"{secret.origin}) can read the release image but not its "
                    "payload; a full Red Hat pull secret is needed."
                )
            raise PrereleaseError(
                f"could not extract {command!r} from {release_image(version)}: "
                f"{detail}.{hint}"
            )
        produced = destination / command
        if not produced.is_file():
            raise PrereleaseError(
                f"{release_image(version)} did not provide a {command!r} binary"
            )
        extracted.append(produced)

    return extracted


def temporary_extract_dir() -> tempfile.TemporaryDirectory:
    """A scratch directory for release-image extraction."""
    return tempfile.TemporaryDirectory(prefix="openshift-release-")


def _go_os(mirror_os: str) -> str:
    """Translate the mirror's OS token into the Go one ``oc`` expects."""
    return {"mac": "darwin"}.get(mirror_os, mirror_os)


def _run(argv: Sequence[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except OSError as exc:
        # Covers a missing file and, just as usefully, a cached binary built
        # for the wrong architecture ("Exec format error").
        raise PrereleaseError(f"{argv[0]} is not executable: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise PrereleaseError(f"{argv[0]} timed out after {timeout}s") from exc


def _last_line(text: str | None) -> str:
    if not text:
        return ""
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""

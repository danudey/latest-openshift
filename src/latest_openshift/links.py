"""Managing the symlinks that make the shim answer to tool names.

A link called ``openshift-install`` in the user's ``PATH`` points at the
``openshift-shim`` executable, which looks at ``argv[0]`` to decide which
binary to run.  The links carry no version themselves: the version is decided
at run time from ``OCP_VERSION``, ``OC_VERSION`` or the configured default, so
a link installed once keeps working as the default moves.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from . import paths
from .errors import OpenShiftToolsError

SHIM_EXECUTABLE_NAME = "openshift-shim"

# Outcomes reported back to the CLI so it can summarise what changed.
CREATED = "created"
UPDATED = "updated"
UNCHANGED = "unchanged"
SKIPPED = "skipped"
REMOVED = "removed"
ABSENT = "absent"


@dataclass(frozen=True)
class LinkResult:
    """What happened to one symlink."""

    name: str
    path: Path
    action: str
    detail: str = ""


def shim_executable() -> Path:
    """Locate the installed ``openshift-shim`` entry point.

    Looks beside the running interpreter first so that a virtualenv or ``uv
    tool install`` picks up its own copy rather than one earlier in ``PATH``.
    """
    candidates = [
        Path(sys.executable).parent / SHIM_EXECUTABLE_NAME,
        Path(sys.argv[0]).resolve().parent / SHIM_EXECUTABLE_NAME,
    ]
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate

    found = shutil.which(SHIM_EXECUTABLE_NAME)
    if found:
        return Path(found)

    raise OpenShiftToolsError(
        f"could not find the {SHIM_EXECUTABLE_NAME} executable. Install this "
        "package with 'uv tool install' or 'pip install' so its entry points "
        "are on disk, then try again."
    )


def link_path(directory: Path, name: str) -> Path:
    return directory / name


def install_links(
    names: Iterable[str],
    directory: Path | None = None,
    *,
    shim: Path | None = None,
    force: bool = False,
) -> list[LinkResult]:
    """Create a symlink per name pointing at the shim.

    An existing symlink that already points at the shim is left alone.  A real
    file, or a symlink to something else, is only replaced with ``force``, so
    a system-installed ``oc`` is never silently overwritten.
    """
    target_dir = paths.ensure_dir(directory or paths.default_symlink_dir())
    shim_path = (shim or shim_executable()).resolve()

    results = []
    for name in names:
        destination = link_path(target_dir, name)
        results.append(_install_one(destination, name, shim_path, force=force))
    return results


def _install_one(
    destination: Path, name: str, shim_path: Path, *, force: bool
) -> LinkResult:
    if destination.is_symlink():
        current = _resolve_symlink(destination)
        if current == shim_path:
            return LinkResult(name, destination, UNCHANGED, "already points at the shim")
        if not force:
            return LinkResult(
                name,
                destination,
                SKIPPED,
                f"symlink to {current}; use --force to replace it",
            )
        destination.unlink()
        _symlink(shim_path, destination)
        return LinkResult(name, destination, UPDATED, f"was a symlink to {current}")

    if destination.exists():
        if not force:
            return LinkResult(
                name,
                destination,
                SKIPPED,
                "a real file is already there; use --force to replace it",
            )
        destination.unlink()
        _symlink(shim_path, destination)
        return LinkResult(name, destination, UPDATED, "replaced an existing file")

    _symlink(shim_path, destination)
    return LinkResult(name, destination, CREATED)


def remove_links(
    names: Iterable[str], directory: Path | None = None, *, shim: Path | None = None
) -> list[LinkResult]:
    """Remove symlinks that point at the shim, leaving anything else alone."""
    target_dir = directory or paths.default_symlink_dir()
    shim_path = (shim or shim_executable()).resolve()

    results = []
    for name in names:
        destination = link_path(target_dir, name)
        if not destination.is_symlink():
            action = ABSENT if not destination.exists() else SKIPPED
            detail = "" if action == ABSENT else "not a symlink; left alone"
            results.append(LinkResult(name, destination, action, detail))
            continue
        if _resolve_symlink(destination) != shim_path:
            results.append(
                LinkResult(name, destination, SKIPPED, "does not point at the shim")
            )
            continue
        destination.unlink()
        results.append(LinkResult(name, destination, REMOVED))
    return results


def installed_links(
    directory: Path | None = None, *, shim: Path | None = None
) -> list[Path]:
    """Every symlink in ``directory`` that points at the shim."""
    target_dir = directory or paths.default_symlink_dir()
    if not target_dir.is_dir():
        return []
    shim_path = (shim or shim_executable()).resolve()
    return sorted(
        entry
        for entry in target_dir.iterdir()
        if entry.is_symlink() and _resolve_symlink(entry) == shim_path
    )


def is_on_path(directory: Path) -> bool:
    """Whether ``directory`` is in ``PATH``, so the links will be found."""
    entries = os.environ.get("PATH", "").split(os.pathsep)
    resolved = directory.resolve()
    for entry in entries:
        if not entry:
            continue
        try:
            if Path(entry).resolve() == resolved:
                return True
        except OSError:  # pragma: no cover - unreadable PATH entry
            continue
    return False


def _symlink(source: Path, destination: Path) -> None:
    try:
        destination.symlink_to(source)
    except OSError as exc:
        raise OpenShiftToolsError(f"could not create {destination}: {exc}") from exc


def _resolve_symlink(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:  # pragma: no cover - broken symlink loop
        return Path(os.readlink(path))

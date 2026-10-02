"""Unpacking downloaded archives into the versioned binary cache.

Binaries land at ``<cache>/bin/<os>-<arch>/<name>-v<version>``, for example
``~/.cache/openshift-tools/bin/linux-amd64/openshift-install-v4.22.13``.  The
platform directory keeps a tool downloaded for some other machine from
shadowing the one that actually runs here.
"""

from __future__ import annotations

import os
import shutil
import stat
import tarfile
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from . import paths
from .artifacts import is_binary_member, normalize_member_name
from .errors import OpenShiftToolsError
from .platforms import Platform
from .versions import Version

_EXECUTABLE_MODE = 0o755

# Windows executables keep their extension after the version suffix, so
# oc.exe from a 4.22.13 archive installs as oc-v4.22.13.exe.
_KEPT_EXTENSIONS = (".exe", ".bat", ".cmd")


@dataclass(frozen=True)
class InstalledBinary:
    """One executable in the cache."""

    name: str
    version: Version
    platform: Platform
    path: Path

    @property
    def exists(self) -> bool:
        return self.path.is_file()


def versioned_filename(name: str, version: Version) -> str:
    """The cache filename for a binary, e.g. ``openshift-install-v4.22.13``."""
    for extension in _KEPT_EXTENSIONS:
        if name.lower().endswith(extension):
            return f"{name[: -len(extension)]}-v{version}{name[-len(extension) :]}"
    return f"{name}-v{version}"


def binary_path(name: str, version: Version, platform: Platform) -> Path:
    """Where a binary lives in the cache, whether or not it is there yet."""
    directory = paths.platform_bin_dir(platform.os, platform.arch)
    return directory / versioned_filename(name, version)


def find_installed(name: str, version: Version, platform: Platform) -> Path | None:
    """The cached binary's path, or ``None`` if it has not been installed."""
    path = binary_path(name, version, platform)
    return path if path.is_file() else None


def archive_path(filename: str, version: Version) -> Path:
    """Where a downloaded archive is cached."""
    return paths.archive_dir() / str(version) / filename


def install_archive(
    archive: Path,
    version: Version,
    platform: Platform,
    *,
    only: Iterable[str] | None = None,
) -> list[InstalledBinary]:
    """Extract every executable from ``archive`` into the versioned cache.

    ``only`` restricts installation to binaries with those (pre-version)
    names; everything else in the archive is skipped.
    """
    wanted = set(only) if only is not None else None
    destination = paths.ensure_dir(paths.platform_bin_dir(platform.os, platform.arch))
    installed: list[InstalledBinary] = []

    for member_name, reader in _iter_archive_members(archive):
        if not is_binary_member(member_name):
            continue
        name = normalize_member_name(member_name)
        if wanted is not None and _strip_extension(name) not in wanted:
            continue
        target = destination / versioned_filename(name, version)
        _write_executable(target, reader)
        installed.append(
            InstalledBinary(
                name=_strip_extension(name),
                version=version,
                platform=platform,
                path=target,
            )
        )

    if not installed:
        raise OpenShiftToolsError(f"no executables found inside {archive.name}")
    return installed


def install_files(
    files: Iterable[Path],
    version: Version,
    platform: Platform,
) -> list[InstalledBinary]:
    """Install already-extracted binaries, as produced by a release image."""
    destination = paths.ensure_dir(paths.platform_bin_dir(platform.os, platform.arch))
    installed = []
    for source in files:
        name = normalize_member_name(source.name)
        target = destination / versioned_filename(name, version)
        shutil.copy2(source, target)
        _make_executable(target)
        installed.append(
            InstalledBinary(
                name=_strip_extension(name),
                version=version,
                platform=platform,
                path=target,
            )
        )
    return installed


def list_installed() -> list[InstalledBinary]:
    """Every binary currently in the cache, sorted by name then version."""
    root = paths.bin_root()
    if not root.is_dir():
        return []

    found: list[InstalledBinary] = []
    for platform_dir in sorted(root.iterdir()):
        if not platform_dir.is_dir() or "-" not in platform_dir.name:
            continue
        os_name, _, arch = platform_dir.name.partition("-")
        platform = Platform(os=os_name, arch=arch)
        for entry in sorted(platform_dir.iterdir()):
            if not entry.is_file():
                continue
            parsed = _parse_versioned_filename(entry.name)
            if parsed is None:
                continue
            name, version = parsed
            found.append(
                InstalledBinary(
                    name=name, version=version, platform=platform, path=entry
                )
            )
    return sorted(found, key=lambda item: (item.name, item.version, str(item.platform)))


# ----------------------------------------------------------------------
# Internals
# ----------------------------------------------------------------------


def _parse_versioned_filename(filename: str) -> tuple[str, Version] | None:
    stem = filename
    for extension in _KEPT_EXTENSIONS:
        if stem.lower().endswith(extension):
            stem = stem[: -len(extension)]
            break
    name, separator, version_text = stem.rpartition("-v")
    if not separator or not name:
        return None
    version = Version.try_parse(version_text)
    if version is None or not version.is_complete:
        return None
    return name, version


def _strip_extension(name: str) -> str:
    for extension in _KEPT_EXTENSIONS:
        if name.lower().endswith(extension):
            return name[: -len(extension)]
    return name


def _iter_archive_members(archive: Path) -> Iterator[tuple[str, _MemberReader]]:
    """Yield ``(member name, reader)`` for each regular file in an archive."""
    name = archive.name.lower()
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                yield info.filename, _ZipReader(zf, info)
        return

    with tarfile.open(archive, "r:*") as tf:
        for member in tf:
            # Hard links (kubectl -> oc in the client archive) are real
            # executables too; extractfile resolves them to their target.
            if not (member.isfile() or member.islnk()):
                continue
            yield member.name, _TarReader(tf, member)


class _MemberReader:
    def copy_to(self, handle) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class _TarReader(_MemberReader):
    def __init__(self, tf: tarfile.TarFile, member: tarfile.TarInfo) -> None:
        self._tf = tf
        self._member = member

    def copy_to(self, handle) -> None:
        source = self._tf.extractfile(self._member)
        if source is None:
            raise OpenShiftToolsError(
                f"could not read {self._member.name} from archive"
            )
        with source:
            shutil.copyfileobj(source, handle)


class _ZipReader(_MemberReader):
    def __init__(self, zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> None:
        self._zf = zf
        self._info = info

    def copy_to(self, handle) -> None:
        with self._zf.open(self._info) as source:
            shutil.copyfileobj(source, handle)


def _write_executable(target: Path, reader: _MemberReader) -> None:
    """Write a member to ``target`` atomically and make it executable.

    Writing via a temporary file means a binary that is mid-extraction can
    never be picked up and exec'd by a concurrently running shim.
    """
    temporary = target.with_name(target.name + ".part")
    try:
        with temporary.open("wb") as handle:
            reader.copy_to(handle)
        _make_executable(temporary)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def _make_executable(path: Path) -> None:
    # Some archives (oc-mirror) ship the binary without the execute bit set at
    # all, so grant the standard bits the umask allows and then make sure the
    # owner can at least read, write and run it.
    granted = _EXECUTABLE_MODE & ~_current_umask()
    owner_bits = stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
    os.chmod(path, os.stat(path).st_mode | granted | owner_bits)


def _current_umask() -> int:
    value = os.umask(0)
    os.umask(value)
    return value

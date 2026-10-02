"""Target platform detection and naming.

The mirror names archives with its own OS tokens (``linux``, ``mac``,
``windows``) and architecture tokens (``amd64``, ``arm64``, ``ppc64le``,
``s390x``), neither of which match what Python reports.  This module maps
between them and detects the local glibc version, which decides whether the
``rhel8`` or ``rhel9`` build of a tool is the right one.
"""

from __future__ import annotations

import ctypes
import functools
import platform as _platform
import re
from dataclasses import dataclass

from .errors import PlatformError

# Mirror OS tokens, keyed by the lowercased value of platform.system().
_OS_ALIASES = {
    "linux": "linux",
    "darwin": "mac",
    "mac": "mac",
    "macos": "mac",
    "osx": "mac",
    "windows": "windows",
    "win": "windows",
    "win32": "windows",
    "cygwin": "windows",
}

# Mirror architecture tokens, keyed by the lowercased value of
# platform.machine() and by the aliases the mirror itself uses in paths.
_ARCH_ALIASES = {
    "x86_64": "amd64",
    "x64": "amd64",
    "amd64": "amd64",
    "aarch64": "arm64",
    "arm64": "arm64",
    "ppc64le": "ppc64le",
    "s390x": "s390x",
}

KNOWN_OSES = ("linux", "mac", "windows")
KNOWN_ARCHES = ("amd64", "arm64", "ppc64le", "s390x")

#: The mirror's per-architecture directory for each Linux architecture.
_MIRROR_ARCH_DIRS = {
    "amd64": "x86_64",
    "arm64": "arm64",
    "ppc64le": "ppc64le",
    "s390x": "s390x",
}

#: The directory to read when the architecture does not matter.  Every
#: architecture directory carries the macOS and Windows archives, and those
#: always name their architecture explicitly, so this one serves for both.
CANONICAL_ARCH_DIR = "x86_64"

# glibc 2.31 and older cannot run the default (RHEL 9) builds; the shell
# installer this tool replaces uses the same cutoff.
RHEL8_GLIBC_CUTOFF = (2, 31)


@dataclass(frozen=True)
class Platform:
    """An OS/architecture pair, in the mirror's own vocabulary."""

    os: str
    arch: str

    @classmethod
    def parse(cls, text: str) -> Platform:
        """Parse ``linux/amd64``, ``linux-amd64`` or ``darwin/arm64``."""
        raw = text.strip().lower()
        parts = re.split(r"[/\-_]", raw, maxsplit=1)
        if len(parts) != 2 or not all(parts):
            raise PlatformError(
                f"platform {text!r} must look like 'os/arch', for example 'linux/amd64'"
            )
        return cls(os=normalize_os(parts[0]), arch=normalize_arch(parts[1]))

    @property
    def archive_suffix(self) -> str:
        """The archive extension the mirror uses for this OS."""
        return ".zip" if self.os == "windows" else ".tar.gz"

    def __str__(self) -> str:
        return f"{self.os}/{self.arch}"


def normalize_os(value: str) -> str:
    try:
        return _OS_ALIASES[value.strip().lower()]
    except KeyError:
        raise PlatformError(
            f"unknown operating system {value!r}; expected one of {', '.join(KNOWN_OSES)}"
        ) from None


def normalize_arch(value: str) -> str:
    try:
        return _ARCH_ALIASES[value.strip().lower()]
    except KeyError:
        raise PlatformError(
            f"unknown architecture {value!r}; expected one of {', '.join(KNOWN_ARCHES)}"
        ) from None


def mirror_arch_dir(platform: Platform | None = None) -> str:
    """The mirror architecture directory to read for a target platform.

    This matters more than it looks.  Inside each architecture directory an
    unqualified Linux filename means *that* directory's architecture:
    ``ccoctl-linux-4.22.13.tar.gz`` is an x86-64 binary under ``x86_64/`` and
    an AArch64 one under ``arm64/``.  Tools that only ever publish the
    unqualified name -- ccoctl, opm, oc-mirror -- are therefore reachable for
    a given architecture only from that architecture's own directory.

    macOS and Windows archives are identical in every directory and always
    name their architecture, so they use the canonical one.
    """
    if platform is None or platform.os != "linux":
        return CANONICAL_ARCH_DIR
    return _MIRROR_ARCH_DIRS.get(platform.arch, CANONICAL_ARCH_DIR)


@functools.cache
def current_platform() -> Platform:
    """The platform this process is running on."""
    return Platform(
        os=normalize_os(_platform.system()),
        arch=normalize_arch(_platform.machine()),
    )


@functools.cache
def glibc_version() -> tuple[int, int] | None:
    """The local glibc version, or ``None`` off glibc (macOS, musl, Windows).

    ``platform.libc_ver()`` reads the executable rather than asking the loader
    and is unreliable, so ask glibc itself first and only fall back.
    """
    try:
        gnu_get_libc_version = ctypes.CDLL(None).gnu_get_libc_version
    except (OSError, AttributeError):
        pass
    else:
        gnu_get_libc_version.restype = ctypes.c_char_p
        parsed = _parse_glibc(gnu_get_libc_version().decode("ascii", "replace"))
        if parsed:
            return parsed

    name, version = _platform.libc_ver()
    if name == "glibc":
        return _parse_glibc(version)
    return None


def _parse_glibc(text: str) -> tuple[int, int] | None:
    match = re.match(r"^(\d+)\.(\d+)", text.strip())
    if not match:
        return None
    return (int(match[1]), int(match[2]))


def prefers_rhel8(target: Platform) -> bool:
    """Whether ``target`` should get the RHEL 8 build of a tool.

    Only answerable for the machine we are running on: the glibc version of
    some other machine we are merely downloading for is unknowable, so those
    targets get the mirror's default (RHEL 9) build.
    """
    if target.os != "linux" or target != current_platform():
        return False
    version = glibc_version()
    return version is not None and version <= RHEL8_GLIBC_CUTOFF

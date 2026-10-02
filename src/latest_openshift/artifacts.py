"""Understanding the archive filenames published in a mirror release directory.

The mirror's naming is only mostly consistent.  A single release directory
contains all of::

    openshift-client-linux-4.22.13.tar.gz            (linux/amd64, glibc default)
    openshift-client-linux-amd64-rhel8-4.22.13.tar.gz
    openshift-client-linux-arm64-4.22.13.tar.gz
    openshift-client-mac-arm64-4.22.13.tar.gz
    openshift-client-windows-4.22.13.zip
    openshift-install-rhel9-amd64.tar.gz             (no OS token at all)
    opm-windows-4.22.13.tar.gz                       (windows, but .tar.gz)
    oc-mirror.tar.gz                                 (no OS, arch or version)

Rather than construct URLs and hope, this module parses whatever the directory
actually lists into :class:`Artifact` records and picks the best match for a
requested tool and platform.  New tools appearing on the mirror are therefore
picked up without a code change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .platforms import Platform, normalize_arch
from .versions import Version

_ARCH_TOKENS = "amd64|arm64|aarch64|x86_64|ppc64le|s390x"
_OS_TOKENS = "linux|mac|windows|src"
_LIBC_TOKENS = "rhel8|rhel9"

_FILENAME_RE = re.compile(
    rf"""
    ^
    (?P<tool>.+?)
    (?:-(?P<os>{_OS_TOKENS}))?
    (?:-(?P<arch>{_ARCH_TOKENS}))?
    (?:[-.](?P<libc>{_LIBC_TOKENS}))?
    # A version's prerelease suffix must not swallow a trailing architecture
    # token, as in openshift-client-src-4.22.13-x86_64.tar.gz.
    (?:-(?P<version>\d+\.\d+(?:\.\d+)?
        (?:-(?!(?:{_ARCH_TOKENS})$)[0-9A-Za-z][0-9A-Za-z.\-]*)?))?
    (?:-(?P<tail_arch>{_ARCH_TOKENS}))?
    $
    """,
    re.VERBOSE,
)

_EXTENSIONS = (".tar.gz", ".tgz", ".tar.xz", ".zip")

# Archive members that are documentation rather than a program.
_NON_BINARY_MEMBERS = re.compile(r"(?i)^(readme|license|notice|changelog)(\.[a-z]+)?$")

# Some archives ship the binary under a libc-qualified name (opm-linux ships
# ``opm-rhel8``); the installed binary should still be called ``opm``.
_MEMBER_SUFFIX_RE = re.compile(rf"-(?:{_LIBC_TOKENS})$")

# Source archives are published alongside the binaries and are never wanted by
# a tool that installs executables.
SOURCE_OS = "src"

# Binaries that a tool's archive provides but which are not named after the
# tool. Anything not listed here is assumed to be named after its archive.
_EXTRA_BINARIES: dict[str, tuple[str, ...]] = {
    "openshift-client": ("oc", "kubectl"),
}

# Friendly aliases accepted on the command line for the tool names the mirror
# actually uses.
TOOL_ALIASES: dict[str, str] = {
    "oc": "openshift-client",
    "kubectl": "openshift-client",
    "client": "openshift-client",
    "install": "openshift-install",
    "installer": "openshift-install",
}


@dataclass(frozen=True)
class Artifact:
    """One downloadable archive in a release directory."""

    filename: str
    tool: str
    platform: Platform
    extension: str
    libc: str | None = None
    version: Version | None = None

    @property
    def is_source(self) -> bool:
        return self.platform.os == SOURCE_OS

    def url(self, directory_url: str) -> str:
        return f"{directory_url.rstrip('/')}/{self.filename}"


def parse_filename(
    filename: str,
    *,
    default_arch: str = "amd64",
    default_os: str = "linux",
) -> Artifact | None:
    """Parse one mirror filename, or return ``None`` if it is not an archive.

    ``default_arch`` and ``default_os`` fill in the tokens the mirror omits.
    Within a given architecture directory an unqualified *Linux* name means
    that directory's own architecture, so the caller must pass the matching
    ``default_arch``.  macOS and Windows archives are the same files in every
    directory and always spell out anything other than amd64, so an
    unqualified one of those is amd64 regardless.
    """
    for extension in _EXTENSIONS:
        if filename.endswith(extension):
            stem = filename[: -len(extension)]
            break
    else:
        return None

    match = _FILENAME_RE.match(stem)
    if not match:
        return None

    tool = match["tool"]
    if not tool:
        return None

    os_name = match["os"] or default_os
    arch_token = match["arch"] or match["tail_arch"]
    if arch_token:
        arch = normalize_arch(arch_token)
    elif os_name == "linux":
        arch = default_arch
    else:
        arch = "amd64"

    version_text = match["version"]
    version = Version.try_parse(version_text) if version_text else None

    return Artifact(
        filename=filename,
        tool=tool,
        platform=Platform(os=os_name, arch=arch),
        extension=extension,
        libc=match["libc"],
        version=version,
    )


def parse_listing(
    filenames: list[str],
    *,
    default_arch: str = "amd64",
    default_os: str = "linux",
) -> list[Artifact]:
    """Parse every archive in a directory listing, skipping anything else."""
    artifacts = []
    for name in filenames:
        artifact = parse_filename(
            name, default_arch=default_arch, default_os=default_os
        )
        if artifact is not None:
            artifacts.append(artifact)
    return artifacts


def canonical_tool_name(name: str) -> str:
    """Resolve a user-supplied tool name or alias to the mirror's name."""
    return TOOL_ALIASES.get(name.strip().lower(), name.strip().lower())


def available_tools(
    artifacts: list[Artifact], platform: Platform | None = None
) -> list[str]:
    """Sorted names of the non-source tools in a release directory.

    Restricted to those published for ``platform`` when one is given.
    """
    names = {
        artifact.tool
        for artifact in artifacts
        if not artifact.is_source
        and (platform is None or artifact.platform == platform)
    }
    return sorted(names)


def binaries_for_tool(tool: str) -> tuple[str, ...]:
    """The executables a tool's archive is expected to provide."""
    return _EXTRA_BINARIES.get(tool, (tool,))


def tool_for_binary(binary: str) -> str:
    """The tool whose archive provides ``binary``.

    Used by the shim to work out what to download when it is invoked through a
    symlink called, say, ``kubectl``.
    """
    for tool, binaries in _EXTRA_BINARIES.items():
        if binary in binaries:
            return tool
    return canonical_tool_name(binary)


def normalize_member_name(member_name: str) -> str:
    """The name a binary should be installed under, given its archive member.

    Strips any directory prefix and the ``-rhel8``/``-rhel9`` suffix that some
    archives carry, so ``opm-rhel8`` installs as ``opm``.
    """
    base = member_name.rsplit("/", 1)[-1]
    return _MEMBER_SUFFIX_RE.sub("", base)


def is_binary_member(member_name: str) -> bool:
    """Whether an archive member is a program rather than documentation."""
    base = member_name.rsplit("/", 1)[-1]
    if not base:
        return False
    return not _NON_BINARY_MEMBERS.match(base)


def select_artifact(
    artifacts: list[Artifact],
    tool: str,
    platform: Platform,
    *,
    prefer_rhel8: bool = False,
    version: Version | None = None,
) -> Artifact | None:
    """Pick the best archive of ``tool`` for ``platform``, or ``None``.

    Preference order is libc flavour first (the RHEL 9 default, unless the
    local glibc is too old), then explicitly versioned filenames over the
    unversioned aliases that sit beside them.
    """
    tool = canonical_tool_name(tool)
    matches = [
        artifact
        for artifact in artifacts
        if artifact.tool == tool
        and not artifact.is_source
        and artifact.platform == platform
        and (version is None or artifact.version is None or artifact.version == version)
    ]
    if not matches:
        return None

    if prefer_rhel8:
        libc_rank = {"rhel8": 0, None: 1, "rhel9": 2}
    else:
        libc_rank = {None: 0, "rhel9": 1, "rhel8": 2}

    def sort_key(artifact: Artifact) -> tuple[int, int, str]:
        return (
            libc_rank.get(artifact.libc, 3),
            0 if artifact.version is not None else 1,
            artifact.filename,
        )

    return min(matches, key=sort_key)

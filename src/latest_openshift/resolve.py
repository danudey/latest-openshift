"""Turning a version the user typed into a specific, published release.

A bare version directory on the mirror is not proof of a release: ``4.22.13/``
can exist before 4.22.13 is production-ready.  The ``latest-X.Y`` pointer
directories are the mirror's own statement of what the current release of a
stream is, so every partial version the user gives -- ``4``, ``4.22``, or
nothing at all -- is resolved through one of those.

An explicit ``major.minor.patch`` is taken at face value, and is looked for in
the order the shell installer this replaces uses:

1. the standard ``ocp`` channel,
2. the ``ocp-dev-preview`` channel,
3. the ``quay.io`` release image, which needs a Red Hat pull secret.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import MirrorError, VersionNotFoundError
from .mirror import CHANNEL_DEV_PREVIEW, CHANNEL_STANDARD, Mirror
from .versions import Version

#: Where the artifacts for a resolved release come from.
SOURCE_MIRROR = "mirror"
SOURCE_RELEASE_IMAGE = "release-image"


@dataclass(frozen=True)
class Release:
    """A specific, obtainable OpenShift release.

    ``tree_major`` and ``directory`` locate it on the mirror without fixing an
    architecture, because which architecture directory to read depends on the
    platform being downloaded for, not on the release.
    """

    version: Version
    source: str
    channel: str | None = None
    tree_major: int | None = None
    directory: str | None = None

    @property
    def is_prerelease_image(self) -> bool:
        return self.source == SOURCE_RELEASE_IMAGE

    @property
    def is_dev_preview(self) -> bool:
        return self.channel == CHANNEL_DEV_PREVIEW

    @property
    def on_mirror(self) -> bool:
        return self.directory is not None and self.tree_major is not None

    def directory_url(self, mirror: Mirror, arch_dir: str | None = None) -> str | None:
        """The release directory's URL for one architecture directory."""
        if not self.on_mirror:
            return None
        return mirror.directory_url(
            self.tree_major,
            self.directory,
            channel=self.channel or CHANNEL_STANDARD,
            arch_dir=arch_dir,
        )

    def __str__(self) -> str:
        return str(self.version)


def tree_major(mirror: Mirror, major: int, *, refresh: bool = False) -> int:
    """The ``/pub/openshift-vN`` tree to look in for ``major``.

    Each tree mirrors the full set of client directories, so a major with no
    tree of its own (a brand new one, say) is still findable in the newest
    tree that does exist.
    """
    majors = mirror.list_majors(refresh=refresh)
    if major in majors:
        return major
    return majors[0]


def list_streams(mirror: Mirror, *, refresh: bool = False) -> list[Version]:
    """Every released ``major.minor`` stream across all mirror trees, ascending."""
    streams: set[Version] = set()
    for major in mirror.list_majors(refresh=refresh):
        streams.update(mirror.list_release_streams(major, refresh=refresh))
    if not streams:
        raise MirrorError("the mirror lists no released versions at all")
    return sorted(streams)


def latest_in_stream(
    mirror: Mirror, stream: Version, *, refresh: bool = False
) -> Version | None:
    """The current release of a ``major.minor`` stream, per its ``latest-`` dir."""
    major = tree_major(mirror, stream.major, refresh=refresh)
    url = mirror.directory_url(major, f"latest-{stream.major}.{stream.minor}")
    return mirror.read_release_name(url, refresh=refresh)


def recent_streams(
    mirror: Mirror, count: int = 4, *, refresh: bool = False
) -> list[Version]:
    """The newest ``count`` released streams, newest first."""
    streams = list_streams(mirror, refresh=refresh)
    return list(reversed(streams[-count:]))


def resolve(
    mirror: Mirror, spec: Version | None = None, *, refresh: bool = False
) -> Release:
    """Resolve ``spec`` to a concrete, published :class:`Release`.

    ``None`` means "the newest release there is".
    """
    if spec is None:
        return _resolve_stream(
            mirror, _newest_stream(mirror, refresh=refresh), refresh=refresh
        )
    if spec.is_complete:
        return _resolve_exact(mirror, spec, refresh=refresh)
    if spec.minor is None:
        return _resolve_stream(
            mirror,
            _newest_stream_in_major(mirror, spec.major, refresh=refresh),
            refresh=refresh,
        )
    return _resolve_stream(mirror, spec, refresh=refresh)


def _newest_stream(mirror: Mirror, *, refresh: bool) -> Version:
    return list_streams(mirror, refresh=refresh)[-1]


def _newest_stream_in_major(mirror: Mirror, major: int, *, refresh: bool) -> Version:
    candidates = [s for s in list_streams(mirror, refresh=refresh) if s.major == major]
    if not candidates:
        raise VersionNotFoundError(
            f"no released OpenShift {major}.x versions found on the mirror"
        )
    return candidates[-1]


def _resolve_stream(mirror: Mirror, stream: Version, *, refresh: bool) -> Release:
    """Resolve a ``major.minor`` stream through its ``latest-`` directory."""
    major = tree_major(mirror, stream.major, refresh=refresh)
    directory = f"latest-{stream.major}.{stream.minor}"
    url = mirror.directory_url(major, directory)
    version = mirror.read_release_name(url, refresh=refresh)
    if version is None:
        raise VersionNotFoundError(
            f"no released OpenShift {stream} found on the mirror "
            f"(no release.txt under {url})"
        )
    return Release(
        version=version,
        source=SOURCE_MIRROR,
        channel=CHANNEL_STANDARD,
        tree_major=major,
        directory=directory,
    )


def _resolve_exact(mirror: Mirror, version: Version, *, refresh: bool) -> Release:
    """Locate one exact release, falling back through the prerelease sources."""
    major = tree_major(mirror, version.major, refresh=refresh)

    for channel in (CHANNEL_STANDARD, CHANNEL_DEV_PREVIEW):
        url = mirror.directory_url(major, str(version), channel=channel)
        if mirror.list_directory(url, refresh=refresh) is not None:
            return Release(
                version=version,
                source=SOURCE_MIRROR,
                channel=channel,
                tree_major=major,
                directory=str(version),
            )

    # Neither mirror channel has it, so the only remaining source is the
    # release image on quay.io. Whether that is actually reachable depends on a
    # pull secret, which is checked when the artifacts are fetched.
    return Release(version=version, source=SOURCE_RELEASE_IMAGE, channel=None)


def describe_mirror_misses(mirror: Mirror, version: Version) -> list[str]:
    """The mirror URLs that were tried for ``version``, for error messages."""
    major = tree_major(mirror, version.major)
    return [
        mirror.directory_url(major, str(version), channel=channel)
        for channel in (CHANNEL_STANDARD, CHANNEL_DEV_PREVIEW)
    ]

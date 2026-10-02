"""The layer that turns "I want oc for 4.22" into a path on disk.

Both the CLI's ``download`` command and the shim go through
:class:`Provisioner`, so a binary fetched one way is reused by the other.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import install, prerelease, resolve
from .artifacts import (
    Artifact,
    available_tools,
    binaries_for_tool,
    canonical_tool_name,
    parse_listing,
    select_artifact,
    tool_for_binary,
)
from .config import Config
from .errors import (
    MirrorError,
    PrereleaseError,
    ToolNotFoundError,
    VersionNotFoundError,
)
from .mirror import Mirror
from .platforms import (
    Platform,
    current_platform,
    mirror_arch_dir,
    normalize_arch,
    prefers_rhel8,
)
from .resolve import Release
from .ui import UI
from .versions import Version


def _arch_of_dir(arch_dir: str) -> str:
    """The architecture an unqualified Linux filename means in a directory."""
    return normalize_arch(arch_dir)


@dataclass(frozen=True)
class ResolvedArtifact:
    """An archive that has been located, but not necessarily downloaded."""

    release: Release
    artifact: Artifact
    url: str


class Provisioner:
    """Resolves versions, finds archives and installs binaries."""

    def __init__(
        self,
        mirror: Mirror,
        ui: UI,
        *,
        config: Config | None = None,
        refresh: bool = False,
    ) -> None:
        self.mirror = mirror
        self.ui = ui
        self.config = config if config is not None else Config.load()
        self.refresh = refresh
        self._listings: dict[str, list[Artifact]] = {}

    # ------------------------------------------------------------------
    # Versions
    # ------------------------------------------------------------------

    def resolve_release(self, spec: Version | None) -> Release:
        """Resolve a possibly partial version to a published release."""
        if spec is not None and spec.is_complete:
            message = f"Locating OpenShift {spec}"
        elif spec is not None:
            message = f"Finding the latest OpenShift {spec} release"
        else:
            message = "Finding the latest OpenShift release"
        with self.ui.status(message):
            return resolve.resolve(self.mirror, spec, refresh=self.refresh)

    # ------------------------------------------------------------------
    # Artifacts
    # ------------------------------------------------------------------

    def artifacts(
        self, release: Release, platform: Platform | None = None
    ) -> list[Artifact]:
        """Every archive published for a release, parsed.

        Reads the architecture directory that matches ``platform``: an
        unqualified Linux filename means a different architecture in each one.
        Empty for a release that exists only as a release image.
        """
        directory_url = self.directory_url(release, platform)
        if directory_url is None:
            return []

        cached = self._listings.get(directory_url)
        if cached is not None:
            return cached

        with self.ui.status(f"Listing artifacts for OpenShift {release.version}"):
            listing = self.mirror.list_directory(directory_url, refresh=self.refresh)
        if listing is None:
            raise MirrorError(f"release directory disappeared: {directory_url}")

        parsed = parse_listing(
            list(listing.files),
            default_arch=_arch_of_dir(mirror_arch_dir(platform)),
        )
        self._listings[directory_url] = parsed
        return parsed

    def directory_url(
        self, release: Release, platform: Platform | None = None
    ) -> str | None:
        """The release directory URL to use for a platform, if it is on the mirror."""
        return release.directory_url(self.mirror, mirror_arch_dir(platform))

    def tools(self, release: Release, platform: Platform | None = None) -> list[str]:
        """Names of the tools a release publishes, optionally for one platform."""
        return available_tools(self.artifacts(release, platform), platform)

    def locate(self, release: Release, tool: str, platform: Platform) -> ResolvedArtifact:
        """Find the archive of ``tool`` for ``platform`` in ``release``."""
        directory_url = self.directory_url(release, platform)
        if directory_url is None:
            raise ToolNotFoundError(
                f"OpenShift {release.version} is not on the mirror, so there is no "
                f"archive to download; use --install to extract {tool} from its "
                "release image instead"
            )

        artifacts = self.artifacts(release, platform)
        canonical = canonical_tool_name(tool)
        chosen = select_artifact(
            artifacts,
            canonical,
            platform,
            prefer_rhel8=prefers_rhel8(platform),
            version=release.version,
        )
        if chosen is None:
            raise ToolNotFoundError(
                self._explain_missing(release, canonical, platform, artifacts)
            )
        return ResolvedArtifact(
            release=release,
            artifact=chosen,
            url=chosen.url(directory_url),
        )

    def _explain_missing(
        self,
        release: Release,
        tool: str,
        platform: Platform,
        artifacts: list[Artifact],
    ) -> str:
        for_tool = [a for a in artifacts if a.tool == tool and not a.is_source]
        if not for_tool:
            known = ", ".join(available_tools(artifacts)) or "none"
            return (
                f"OpenShift {release.version} does not publish a tool called "
                f"{tool!r}. Available: {known}"
            )
        platforms = sorted({str(a.platform) for a in for_tool})
        return (
            f"OpenShift {release.version} publishes {tool!r}, but not for "
            f"{platform}. Available: {', '.join(platforms)}"
        )

    # ------------------------------------------------------------------
    # Downloading and installing
    # ------------------------------------------------------------------

    def fetch_archive(
        self,
        located: ResolvedArtifact,
        *,
        destination: Path | None = None,
        force: bool = False,
    ) -> Path:
        """Download an archive, reusing the cached copy when there is one."""
        target = destination or install.archive_path(
            located.artifact.filename, located.release.version
        )
        if not force and target.is_file() and target.stat().st_size > 0:
            self.ui.info(f"Using cached {target.name}")
            return target

        with self.ui.download(located.artifact.filename) as reporter:
            self.mirror.download(
                located.url,
                target,
                on_start=reporter.start,
                on_progress=reporter.advance,
            )
        return target

    def install_tool(
        self,
        release: Release,
        tool: str,
        platform: Platform,
        *,
        force: bool = False,
        only: list[str] | None = None,
    ) -> list[install.InstalledBinary]:
        """Make a tool's binaries available in the versioned cache."""
        canonical = canonical_tool_name(tool)

        if not force:
            wanted = only or list(binaries_for_tool(canonical))
            existing = [
                install.InstalledBinary(
                    name=name,
                    version=release.version,
                    platform=platform,
                    path=path,
                )
                for name in wanted
                if (path := install.find_installed(name, release.version, platform))
            ]
            if len(existing) == len(wanted):
                return existing

        if release.is_prerelease_image:
            return self._install_from_release_image(
                release, canonical, platform, only=only
            )

        located = self.locate(release, canonical, platform)
        archive = self.fetch_archive(located, force=force)
        self.ui.info(f"Extracting {located.artifact.filename}")
        return install.install_archive(archive, release.version, platform, only=only)

    def ensure_binary(
        self,
        binary: str,
        spec: Version | None,
        platform: Platform | None = None,
        *,
        force: bool = False,
    ) -> tuple[Path, Release]:
        """Return the path to ``binary`` for ``spec``, downloading if needed.

        This is the shim's entry point: it short-circuits to a cached binary
        whenever the version is already fully specified.
        """
        target = platform or current_platform()

        if spec is not None and spec.is_complete and not force:
            cached = install.find_installed(binary, spec, target)
            if cached is not None:
                return cached, Release(version=spec, source=resolve.SOURCE_MIRROR)

        release = self.resolve_release(spec)
        cached = (
            None if force else install.find_installed(binary, release.version, target)
        )
        if cached is not None:
            return cached, release

        tool = tool_for_binary(binary)
        installed = self.install_tool(release, tool, target, force=force, only=[binary])
        for item in installed:
            if item.name == binary:
                return item.path, release

        raise ToolNotFoundError(
            f"{tool} for OpenShift {release.version} does not contain a {binary!r} binary"
        )

    # ------------------------------------------------------------------
    # Release images
    # ------------------------------------------------------------------

    def _install_from_release_image(
        self,
        release: Release,
        tool: str,
        platform: Platform,
        *,
        only: list[str] | None = None,
    ) -> list[install.InstalledBinary]:
        secret = prerelease.find_pull_secret(self.config)
        if secret is None:
            tried = ", ".join(
                resolve.describe_mirror_misses(self.mirror, release.version)
            )
            raise PrereleaseError(
                f"OpenShift {release.version} is not published on the mirror "
                f"(tried {tried}). " + prerelease.describe_missing_pull_secret()
            )

        oc_binary = self._bootstrap_oc()
        image = prerelease.release_image(release.version)

        with self.ui.status(f"Checking {image}"):
            accessible = prerelease.image_accessible(oc_binary, release.version, secret)
        if not accessible:
            raise PrereleaseError(
                f"OpenShift {release.version} is not on the mirror and {image} could "
                f"not be read with the pull secret at {secret.path} ({secret.origin}). "
                "The version may not exist, or the secret may lack access."
            )

        commands = only or list(binaries_for_tool(tool))
        self.ui.info(f"Extracting {', '.join(commands)} from {image}")
        with prerelease.temporary_extract_dir() as scratch:
            with self.ui.status(f"Extracting from {image}"):
                files = prerelease.extract_commands(
                    oc_binary,
                    release.version,
                    secret,
                    commands,
                    Path(scratch),
                    platform=platform,
                )
            return install.install_files(files, release.version, platform)

    def _bootstrap_oc(self) -> Path:
        """An ``oc`` binary good enough to query a release image.

        Any recent ``oc`` will do, so the newest mirror release is used; it is
        cached like any other, so this costs a download at most once.
        """
        native = current_platform()
        try:
            release = resolve.resolve(self.mirror, None, refresh=self.refresh)
        except (MirrorError, VersionNotFoundError) as exc:
            raise PrereleaseError(
                f"need an 'oc' binary to read release images, but the mirror could "
                f"not be queried for one: {exc}"
            ) from exc

        cached = install.find_installed("oc", release.version, native)
        if cached is not None:
            return cached

        self.ui.info(f"Fetching oc {release.version} to query the release image")
        installed = self.install_tool(release, "openshift-client", native, only=["oc"])
        for item in installed:
            if item.name == "oc":
                return item.path
        raise PrereleaseError(  # pragma: no cover - client archive always has oc
            "could not obtain an 'oc' binary from the mirror"
        )

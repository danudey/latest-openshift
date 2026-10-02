"""The ``latest-openshift`` command line interface.

Output contract: anything a script would want lands on stdout, one value per
line, with no decoration when stdout is not a terminal.  Progress, status and
errors go to stderr.
"""

from __future__ import annotations

import contextlib
import shutil
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import click

from . import install, links, paths, prerelease, resolve
from .artifacts import binaries_for_tool, canonical_tool_name
from .config import Config, requested_version
from .errors import OpenShiftToolsError
from .mirror import DEFAULT_TTL_SECONDS, Mirror
from .platforms import Platform, current_platform
from .provision import Provisioner
from .resolve import Release
from .ui import UI
from .versions import Version

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"], "max_content_width": 100}


class ErrorHandlingGroup(click.Group):
    """A group that renders expected failures as one-line errors.

    Doing this inside the group, rather than only around it, means the CLI
    behaves the same whether it is run as a console script or invoked
    directly -- including from the tests.
    """

    def invoke(self, ctx: click.Context):
        try:
            return super().invoke(ctx)
        except OpenShiftToolsError as exc:
            raise click.ClickException(str(exc)) from exc


@dataclass
class AppContext:
    """Global options shared by every subcommand."""

    ui: UI
    refresh: bool
    ttl: int
    config: Config

    @contextlib.contextmanager
    def session(self) -> Iterator[Provisioner]:
        """Open a mirror client and a provisioner for one command."""
        with Mirror(ttl_seconds=self.ttl) as mirror:
            yield Provisioner(mirror, self.ui, config=self.config, refresh=self.refresh)


def _parse_version_option(value: str | None) -> Version | None:
    """Parse ``--version``, treating an empty value as unset."""
    if value is None or not value.strip():
        return None
    return Version.parse(value)


def _target_platform(value: str | None) -> Platform:
    return Platform.parse(value) if value else current_platform()


version_option = click.option(
    "--version",
    "-V",
    "version_text",
    metavar="VERSION",
    help="OpenShift version: a major (4), a stream (4.22) or an exact release "
    "(4.22.13). Defaults to $OCP_VERSION, then $OC_VERSION, then the configured "
    "default, then the newest release.",
)
platform_option = click.option(
    "--platform",
    "-p",
    "platform_text",
    metavar="OS/ARCH",
    help="Target platform, such as linux/amd64, mac/arm64 or windows/amd64. "
    "Defaults to this machine.",
)


@click.group(cls=ErrorHandlingGroup, context_settings=CONTEXT_SETTINGS)
@click.option("--refresh", is_flag=True, help="Ignore cached mirror listings.")
@click.option(
    "--cache-ttl",
    type=int,
    default=DEFAULT_TTL_SECONDS,
    show_default=True,
    metavar="SECONDS",
    help="How long to reuse cached mirror listings. 0 disables the cache.",
)
@click.option(
    "--quiet", "-q", is_flag=True, help="Suppress progress and status output."
)
@click.version_option(package_name="latest-openshift", prog_name="latest-openshift")
@click.pass_context
def main(ctx: click.Context, refresh: bool, cache_ttl: int, quiet: bool) -> None:
    """Query mirror.openshift.com and manage versioned OpenShift client tools."""
    ctx.obj = AppContext(
        ui=UI(quiet=quiet),
        refresh=refresh,
        ttl=cache_ttl,
        config=Config.load(),
    )


# ----------------------------------------------------------------------
# Version queries
# ----------------------------------------------------------------------


@main.command("latest")
@click.argument("spec", required=False)
@click.pass_obj
def latest_command(app: AppContext, spec: str | None) -> None:
    """Print the newest released OpenShift version.

    With no argument, the newest release there is. With a major (4) or a
    stream (4.22), the newest release under that prefix.

    \b
    Only versions the mirror advertises through a 'latest-X.Y' directory are
    considered, so an unreleased build sitting in a bare version directory is
    never reported.
    """
    requested = Version.parse(spec) if spec else None
    if requested is not None and requested.is_complete:
        raise click.BadParameter(
            f"{spec} is already an exact version; pass a major (4) or a stream (4.22)"
        )

    with app.session() as provisioner:
        release = provisioner.resolve_release(requested)
    app.ui.value(str(release.version))


@main.command("list")
@click.option(
    "--count",
    "-n",
    type=click.IntRange(min=1),
    default=4,
    show_default=True,
    help="How many streams to list.",
)
@click.option("--major", type=int, metavar="N", help="Restrict to one major version.")
@click.pass_obj
def list_command(app: AppContext, count: int, major: int | None) -> None:
    """Print the newest release of each of the last few minor versions."""
    with app.session() as provisioner:
        with app.ui.status("Finding release streams"):
            streams = resolve.list_streams(provisioner.mirror, refresh=app.refresh)
        if major is not None:
            streams = [stream for stream in streams if stream.major == major]
            if not streams:
                raise OpenShiftToolsError(
                    f"no released OpenShift {major}.x versions found on the mirror"
                )
        selected = list(reversed(streams[-count:]))

        rows = []
        with app.ui.status("Reading release pointers"):
            for stream in selected:
                version = resolve.latest_in_stream(
                    provisioner.mirror, stream, refresh=app.refresh
                )
                if version is not None:
                    rows.append((str(stream), str(version)))

    if app.ui.rich_stdout:
        app.ui.values(rows, headers=("STREAM", "LATEST"))
    else:
        for _, version in rows:
            app.ui.value(version)


@main.command("tools")
@version_option
@platform_option
@click.option("--all-platforms", is_flag=True, help="List tools for every platform.")
@click.pass_obj
def tools_command(
    app: AppContext,
    version_text: str | None,
    platform_text: str | None,
    all_platforms: bool,
) -> None:
    """Print the tools a release publishes."""
    spec = requested_version(version_text, config=app.config)
    platform = None if all_platforms else _target_platform(platform_text)

    with app.session() as provisioner:
        release = provisioner.resolve_release(spec)
        if release.is_prerelease_image:
            raise OpenShiftToolsError(
                f"OpenShift {release.version} is not on the mirror, so its tool list "
                "is not published; ask for a specific tool instead"
            )
        names = provisioner.tools(release, platform)

    if not names:
        raise OpenShiftToolsError(
            f"OpenShift {release.version} publishes nothing for {platform}"
        )
    for name in names:
        app.ui.value(name)


# ----------------------------------------------------------------------
# Downloading
# ----------------------------------------------------------------------


@main.command("download")
@click.argument("tools", nargs=-1, required=True, metavar="TOOL...")
@version_option
@platform_option
@click.option(
    "--url", "print_url", is_flag=True, help="Print the download URL and exit."
)
@click.option(
    "--install",
    "do_install",
    is_flag=True,
    help="Extract the binaries into the versioned cache instead of leaving an archive.",
)
@click.option(
    "--print-location",
    is_flag=True,
    help="With --install, print the absolute path of each installed binary.",
)
@click.option(
    "--dest",
    "-d",
    type=click.Path(file_okay=False, path_type=Path),
    metavar="DIR",
    help="Directory to save archives in. Defaults to the current directory, or "
    "the archive cache when --install is given.",
)
@click.option(
    "--force", is_flag=True, help="Re-download and re-extract even if cached."
)
@click.pass_obj
def download_command(
    app: AppContext,
    tools: tuple[str, ...],
    version_text: str | None,
    platform_text: str | None,
    print_url: bool,
    do_install: bool,
    print_location: bool,
    dest: Path | None,
    force: bool,
) -> None:
    """Download one or more OpenShift tools.

    TOOL is a name the mirror publishes -- openshift-client, openshift-install,
    ccoctl, opm, oc-mirror -- or a familiar alias such as 'oc'. Run
    'latest-openshift tools' to see what a release offers.

    \b
    Examples:
      latest-openshift download oc --version 4.22
      latest-openshift download openshift-install --url
      latest-openshift download oc openshift-install --install --print-location
      latest-openshift download oc --platform mac/arm64 --dest ~/Downloads
    """
    if print_url and do_install:
        raise click.UsageError("--url and --install do the opposite of each other")
    if print_location and not do_install:
        raise click.UsageError("--print-location only makes sense with --install")
    if dest is not None and print_url:
        raise click.UsageError("--dest has no effect with --url")

    spec = requested_version(version_text, config=app.config)
    platform = _target_platform(platform_text)

    with app.session() as provisioner:
        release = provisioner.resolve_release(spec)
        _announce_release(app.ui, release)

        if print_url:
            for tool in tools:
                located = provisioner.locate(release, tool, platform)
                app.ui.value(located.url)
            return

        if do_install:
            for tool in tools:
                installed = provisioner.install_tool(
                    release, tool, platform, force=force
                )
                for binary in installed:
                    if print_location:
                        app.ui.value(str(binary.path))
                    else:
                        app.ui.success(f"Installed {binary.name} {release.version}")
                        app.ui.info(str(binary.path))
            return

        destination = dest or Path.cwd()
        paths.ensure_dir(destination)
        for tool in tools:
            located = provisioner.locate(release, tool, platform)
            target = destination / located.artifact.filename
            provisioner.fetch_archive(located, destination=target, force=force)
            app.ui.value(str(target))


@main.command("path")
@click.argument("binary")
@version_option
@platform_option
@click.option("--force", is_flag=True, help="Re-download even if already cached.")
@click.pass_obj
def path_command(
    app: AppContext,
    binary: str,
    version_text: str | None,
    platform_text: str | None,
    force: bool,
) -> None:
    """Print the path to a binary, downloading it first if necessary.

    \b
    Example:
      export KUBECTL="$(latest-openshift path kubectl --version 4.22)"
    """
    spec = requested_version(version_text, config=app.config)
    platform = _target_platform(platform_text)

    with app.session() as provisioner:
        path, _ = provisioner.ensure_binary(binary, spec, platform, force=force)
    app.ui.value(str(path))


# ----------------------------------------------------------------------
# Default version
# ----------------------------------------------------------------------


@main.group("default", invoke_without_command=True)
@click.pass_context
def default_group(ctx: click.Context) -> None:
    """Get or set the default OpenShift version used by the tool shims."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(default_show)


@default_group.command("show")
@click.option(
    "--resolved",
    is_flag=True,
    help="Resolve the default to an exact release instead of printing it as stored.",
)
@click.pass_obj
def default_show(app: AppContext, resolved: bool) -> None:
    """Print the configured default version."""
    value = app.config.default_version
    if value is None:
        app.ui.info("No default version is set; the newest release is used.")
        raise SystemExit(1)

    if not resolved:
        app.ui.value(str(value))
        return

    with app.session() as provisioner:
        release = provisioner.resolve_release(value)
    app.ui.value(str(release.version))


@default_group.command("set")
@click.argument("version")
@click.pass_obj
def default_set(app: AppContext, version: str) -> None:
    """Set the default OpenShift version.

    VERSION may be partial: setting it to '4.22' tracks the newest 4.22 patch
    release as it moves, while '4.22.13' pins exactly.
    """
    parsed = Version.parse(version)
    with app.session() as provisioner:
        release = provisioner.resolve_release(parsed)

    app.config.default_version = parsed
    location = app.config.save()
    app.ui.success(
        f"Default OpenShift version set to {parsed} (currently {release.version})"
    )
    app.ui.info(str(location))


@default_group.command("clear")
@click.pass_obj
def default_clear(app: AppContext) -> None:
    """Remove the default version, falling back to the newest release."""
    if app.config.default_version is None:
        app.ui.info("No default version was set.")
        return
    app.config.default_version = None
    app.config.save()
    app.ui.success("Default OpenShift version cleared.")


@default_group.command("set-pull-secret")
@click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.pass_obj
def default_set_pull_secret(app: AppContext, path: Path) -> None:
    """Record a Red Hat pull secret, used for prerelease release images."""
    app.config.pull_secret = path.expanduser().resolve()
    location = app.config.save()
    app.ui.success(f"Pull secret set to {app.config.pull_secret}")
    app.ui.info(str(location))


# ----------------------------------------------------------------------
# Symlinks
# ----------------------------------------------------------------------


@main.command("link")
@version_option
@click.option(
    "--dir",
    "-d",
    "directory",
    type=click.Path(file_okay=False, path_type=Path),
    help="Where to create the symlinks. Defaults to $OPENSHIFT_TOOLS_BIN, "
    "else ~/.local/bin.",
)
@click.option(
    "--tool",
    "only_tools",
    multiple=True,
    metavar="TOOL",
    help="Link only this tool's binaries. Repeatable. Defaults to everything the "
    "release publishes for this platform.",
)
@click.option(
    "--force", is_flag=True, help="Replace existing files and foreign symlinks."
)
@click.option(
    "--set-default",
    is_flag=True,
    help="Also record --version as the default version for the shims.",
)
@click.pass_obj
def link_command(
    app: AppContext,
    version_text: str | None,
    directory: Path | None,
    only_tools: tuple[str, ...],
    force: bool,
    set_default: bool,
) -> None:
    """Install shim symlinks for a release's binaries.

    The symlinks are version-independent: each one runs the shim, which picks
    the version from $OCP_VERSION, $OC_VERSION or the configured default at run
    time. The version here only decides which binaries exist to be linked.
    """
    spec = requested_version(version_text, config=app.config)
    platform = current_platform()

    with app.session() as provisioner:
        release = provisioner.resolve_release(spec)
        if release.is_prerelease_image:
            raise OpenShiftToolsError(
                f"OpenShift {release.version} is not on the mirror, so its tool list "
                "is unknown; pass --tool to say what to link"
            )
        tool_names = (
            [canonical_tool_name(name) for name in only_tools]
            if only_tools
            else provisioner.tools(release, platform)
        )

    binaries = sorted({name for tool in tool_names for name in binaries_for_tool(tool)})
    if not binaries:
        raise OpenShiftToolsError(f"nothing to link for OpenShift {release.version}")

    target_dir = directory or paths.default_symlink_dir()
    results = links.install_links(binaries, target_dir, force=force)

    rows = [(result.name, result.action, result.detail) for result in results]
    if app.ui.rich_stdout:
        app.ui.values(rows, headers=("BINARY", "STATUS", "NOTE"))
    else:
        for result in results:
            app.ui.value(str(result.path))

    skipped = [result for result in results if result.action == links.SKIPPED]
    if skipped:
        app.ui.warn(
            f"{len(skipped)} link(s) skipped: "
            + ", ".join(result.name for result in skipped)
        )
    if not links.is_on_path(target_dir):
        app.ui.warn(f"{target_dir} is not on your PATH; the links will not be found.")

    if set_default and spec is not None:
        app.config.default_version = spec
        app.config.save()
        app.ui.success(f"Default OpenShift version set to {spec}")


@main.command("unlink")
@click.option(
    "--dir",
    "-d",
    "directory",
    type=click.Path(file_okay=False, path_type=Path),
    help="Where the symlinks live. Defaults to $OPENSHIFT_TOOLS_BIN or ~/.local/bin.",
)
@click.argument("names", nargs=-1, metavar="[BINARY]...")
@click.pass_obj
def unlink_command(
    app: AppContext, directory: Path | None, names: tuple[str, ...]
) -> None:
    """Remove shim symlinks. With no arguments, removes all of them."""
    target_dir = directory or paths.default_symlink_dir()
    wanted = list(names) or [path.name for path in links.installed_links(target_dir)]
    if not wanted:
        app.ui.info(f"No shim symlinks found in {target_dir}.")
        return

    results = links.remove_links(wanted, target_dir)
    for result in results:
        if result.action == links.REMOVED:
            app.ui.value(str(result.path))
        elif result.action == links.SKIPPED:
            app.ui.warn(f"{result.path}: {result.detail}")


# ----------------------------------------------------------------------
# Cache
# ----------------------------------------------------------------------


@main.group("cache")
def cache_group() -> None:
    """Inspect and clean the local download cache."""


@cache_group.command("path")
@click.pass_obj
def cache_path(app: AppContext) -> None:
    """Print the cache directory."""
    app.ui.value(str(paths.cache_root()))


@cache_group.command("list")
@click.pass_obj
def cache_list(app: AppContext) -> None:
    """List the binaries currently in the cache."""
    entries = install.list_installed()
    if not entries:
        app.ui.info("The cache is empty.")
        return

    rows = [
        (entry.name, str(entry.version), str(entry.platform), str(entry.path))
        for entry in entries
    ]
    if app.ui.rich_stdout:
        app.ui.values(rows, headers=("BINARY", "VERSION", "PLATFORM", "PATH"))
    else:
        for entry in entries:
            app.ui.value(str(entry.path))


@cache_group.command("clear")
@click.option("--binaries", is_flag=True, help="Also delete installed binaries.")
@click.option("--archives", is_flag=True, help="Also delete downloaded archives.")
@click.confirmation_option(prompt="Clear the cache?")
@click.pass_obj
def cache_clear(app: AppContext, binaries: bool, archives: bool) -> None:
    """Clear cached mirror listings, and optionally downloads too."""
    with Mirror(ttl_seconds=app.ttl) as mirror:
        removed = mirror.clear_cache()
    app.ui.success(f"Removed {removed} cached listing(s).")

    if archives:
        shutil.rmtree(paths.archive_dir(), ignore_errors=True)
        app.ui.success("Removed downloaded archives.")
    if binaries:
        count = len(install.list_installed())
        shutil.rmtree(paths.bin_root(), ignore_errors=True)
        app.ui.success(f"Removed {count} installed binary(ies).")


# ----------------------------------------------------------------------
# Diagnostics
# ----------------------------------------------------------------------


@main.command("info")
@version_option
@click.pass_obj
def info_command(app: AppContext, version_text: str | None) -> None:
    """Show how this tool is configured and what it would resolve to."""
    spec = requested_version(version_text, config=app.config)
    secret = None
    with contextlib.suppress(OpenShiftToolsError):
        secret = prerelease.find_pull_secret(app.config)

    with app.session() as provisioner:
        release = provisioner.resolve_release(spec)

    rows = [
        ("platform", str(current_platform())),
        ("requested version", str(spec) if spec else "(newest)"),
        ("resolved version", str(release.version)),
        ("source", release.channel or release.source),
        ("default version", str(app.config.default_version or "(unset)")),
        ("config file", str(paths.config_file())),
        ("cache", str(paths.cache_root())),
        ("symlink dir", str(paths.default_symlink_dir())),
        ("pull secret", str(secret.path) if secret else "(none found)"),
    ]
    app.ui.values(rows, headers=("SETTING", "VALUE"))


def _announce_release(ui: UI, release: Release) -> None:
    if release.is_prerelease_image:
        ui.info(f"OpenShift {release.version} (release image, not on the mirror)")
    elif release.is_dev_preview:
        ui.info(f"OpenShift {release.version} (dev preview)")
    else:
        ui.info(f"OpenShift {release.version}")


def run() -> int:
    """Console-script entry point, returning a process exit code."""
    try:
        main.main(standalone_mode=False)
    except click.ClickException as exc:
        exc.show()
        return exc.exit_code
    except click.exceptions.Abort:
        print("Aborted.", file=sys.stderr)
        return 130
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run())

"""The dispatching entry point that symlinks point at.

Symlink ``openshift-shim`` to ``oc``, ``openshift-install`` or any other
OpenShift binary and running that name will:

1. work out which version is wanted -- ``$OCP_VERSION`` first, then
   ``$OC_VERSION``, then the configured default, then the newest release;
2. resolve a partial version such as ``4.22`` to the current release of that
   stream;
3. download and unpack the binary if the cache does not already have it;
4. replace itself with the real binary, passing every argument through.

Everything the shim itself prints goes to stderr, so the wrapped program's
stdout stays exactly as it would have been.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from . import install
from .config import Config, requested_version
from .errors import OpenShiftToolsError
from .mirror import Mirror
from .platforms import Platform, current_platform
from .provision import Provisioner
from .ui import UI
from .versions import Version

#: Names that mean "the shim was run directly", not through a symlink.
_SELF_NAMES = {"openshift-shim", "openshift-shim.exe", "shim.py", "__main__.py"}

USAGE = """\
usage: openshift-shim <binary> [arguments...]

Normally the shim is not run under this name. Symlink it to the binary you
want instead:

    ln -s "$(command -v openshift-shim)" ~/.local/bin/openshift-install

or let the CLI do it for you:

    latest-openshift link
"""


def main(argv: list[str] | None = None) -> int:
    """Resolve the requested binary and exec it."""
    args = list(sys.argv if argv is None else argv)
    invoked = Path(args[0]).name

    if invoked in _SELF_NAMES:
        if len(args) < 2 or args[1] in {"-h", "--help"}:
            sys.stderr.write(USAGE)
            return 0 if len(args) >= 2 else 2
        binary = args[1]
        argv0 = args[1]
        passthrough = args[2:]
    else:
        binary = _strip_executable_suffix(invoked)
        # The wrapped program sees the name it was actually invoked as, which
        # some tools use to decide how to behave and what to print in usage.
        argv0 = invoked
        passthrough = args[1:]

    ui = UI()
    try:
        path = _locate(binary, ui)
    except OpenShiftToolsError as exc:
        ui.error(str(exc))
        return 1
    except KeyboardInterrupt:
        return 130

    return _exec(path, argv0, passthrough)


def _locate(binary: str, ui: UI) -> Path:
    """Find the binary, downloading it if the cache does not have it."""
    config = Config.load()
    spec = requested_version(config=config)
    platform = current_platform()

    # The overwhelmingly common case: the wanted version is already pinned and
    # already downloaded. Answer it without opening a mirror client at all.
    if spec is not None and spec.is_complete:
        cached = install.find_installed(binary, spec, platform)
        if cached is not None:
            return cached

    try:
        with Mirror() as mirror:
            provisioner = Provisioner(mirror, ui, config=config)
            path, _ = provisioner.ensure_binary(binary, spec, platform)
            return path
    except OpenShiftToolsError as exc:
        fallback = _newest_cached(binary, spec, platform)
        if fallback is None:
            raise
        ui.warn(f"{exc}")
        ui.warn(f"falling back to the cached {fallback.name}")
        return fallback.path


def _newest_cached(
    binary: str, spec: Version | None, platform: Platform
) -> install.InstalledBinary | None:
    """The newest cached build of ``binary`` that satisfies ``spec``.

    Used when the mirror cannot be reached: a tool that already works offline
    should keep working offline.
    """
    candidates = [
        entry
        for entry in install.list_installed()
        if entry.name == binary
        and entry.platform == platform
        and (spec is None or spec.matches(entry.version))
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda entry: entry.version)


def _strip_executable_suffix(name: str) -> str:
    lowered = name.lower()
    for suffix in (".exe", ".cmd", ".bat"):
        if lowered.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _exec(path: Path, argv0: str, arguments: list[str]) -> int:
    """Hand control to the real binary.

    On POSIX this replaces the process, so signals, exit codes and terminal
    control all behave as if the binary had been run directly. Windows has no
    equivalent, so there the shim waits and forwards the exit code.
    """
    command = [argv0, *arguments]
    if os.name == "nt":  # pragma: no cover - not exercised on Linux
        completed = subprocess.run([str(path), *arguments], check=False)
        return completed.returncode
    try:
        os.execv(str(path), command)
    except OSError as exc:
        print(f"error: could not run {path}: {exc}", file=sys.stderr)
        return 126
    return 0  # pragma: no cover - execv does not return


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

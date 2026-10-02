# latest-openshift

Query [mirror.openshift.com](https://mirror.openshift.com/pub/openshift-v4/) for
OpenShift releases, download the client tools, and run any of them at any
version through a symlink.

```console
$ latest-openshift latest
4.22.13

$ latest-openshift latest 4.20
4.20.37

$ OCP_VERSION=4.19 oc version --client
Client Version: 4.19.45
```

## Install

```console
uv tool install /path/to/latest-openshift
```

This installs two commands: `latest-openshift` (aliased to `ocp`) and
`openshift-shim`, the dispatcher the symlinks point at.

## What counts as a release

A version directory on the mirror is not proof of a release: `4.22.13/` can
appear before 4.22.13 is production-ready. The `latest-X.Y/` pointer
directories are the mirror's own statement of the current release of a stream,
so every partial version — `4`, `4.22`, or nothing at all — is resolved through
one of those.

An exact `major.minor.patch` is taken at face value and looked for in the order
the Red Hat installer scripts use:

1. the standard `ocp` channel,
2. the `ocp-dev-preview` channel, for engineering candidates,
3. the `quay.io` release image, which needs a Red Hat pull secret.

## Output contract

Anything a script would want goes to **stdout**, one value per line, with no
decoration when stdout is not a terminal. Progress bars, spinners, status
messages and errors go to **stderr**.

```console
$ latest-openshift latest 4.22 | cat
4.22.13
```

Spinners and progress bars appear only when stderr is a terminal and `CI` is
unset or false.

## Commands

### Versions

```console
$ latest-openshift latest             # newest release there is
4.22.13

$ latest-openshift latest 4.21        # newest release of a stream
4.21.32

$ latest-openshift list               # the last four streams
4.22.13
4.21.32
4.20.37
4.19.45

$ latest-openshift list -n 6          # ...or as many as you like
```

On a terminal, `list` shows a table with the stream alongside each version.

### Downloading

```console
# Just tell me the URL
$ latest-openshift download openshift-install --url
https://mirror.openshift.com/.../latest-4.22/openshift-install-linux-4.22.13.tar.gz

# Save the archive here
$ latest-openshift download oc opm

# Unpack into the versioned cache and print where it went
$ latest-openshift download oc --install --print-location
/home/you/.cache/openshift-tools/bin/linux-amd64/oc-v4.22.13
/home/you/.cache/openshift-tools/bin/linux-amd64/kubectl-v4.22.13

# Any published platform, not just this one
$ latest-openshift download oc --platform mac/arm64 --dest ~/Downloads
```

`--version` accepts a major (`4`), a stream (`4.22`) or an exact release
(`4.22.13`). `latest-openshift tools` lists what a release publishes;
`oc`, `client`, `install` and `installer` work as aliases.

Binaries are cached at
`~/.cache/openshift-tools/bin/<os>-<arch>/<name>-v<version>`. The platform
directory keeps a tool downloaded for another machine from shadowing the one
that runs here.

### The default version

```console
$ latest-openshift default set 4.22       # tracks the newest 4.22 patch
$ latest-openshift default set 4.22.13    # pins exactly
$ latest-openshift default show
4.22
$ latest-openshift default show --resolved
4.22.13
$ latest-openshift default clear
```

### Symlinks

```console
$ latest-openshift link
$ which openshift-install
/home/you/.local/bin/openshift-install
$ openshift-install version
```

`link` creates one symlink per binary the release publishes, each pointing at
`openshift-shim`. The links carry no version: the shim picks one at run time,
so a link installed once keeps working as the default moves. The `--version`
here only decides *which binaries exist to be linked* (4.14 and 4.22 publish
different sets).

Existing files and foreign symlinks are never replaced without `--force`, so a
system-installed `oc` is safe. `latest-openshift unlink` removes only the links
that point at the shim.

Useful flags: `--dir` (default `$OPENSHIFT_TOOLS_BIN`, else `~/.local/bin`),
`--tool` to link one tool's binaries, `--set-default` to record the version too.

### How the shim picks a version

In order: `$OCP_VERSION`, then `$OC_VERSION`, then the configured default, then
the newest release.
A partial version is resolved to the current release of that stream, and the
binary is downloaded if the cache does not have it.

```console
$ OCP_VERSION=4.20 openshift-install version    # newest 4.20 patch
$ OCP_VERSION=4.20.5 oc version --client        # exactly 4.20.5
```

The shim writes nothing to stdout, so the wrapped program's output is exactly
what it would have been. When the version is fully pinned and already cached it
does no network I/O at all; if the mirror is unreachable it falls back to the
newest cached build that satisfies the request, so a tool that worked offline
keeps working offline.

### Cache

```console
$ latest-openshift cache list
$ latest-openshift cache path
$ latest-openshift cache clear --archives --binaries
```

### Diagnostics

```console
$ latest-openshift info
```

Shows the resolved version, where the config and cache live, and which pull
secret was found.

## Prereleases

Versions that never reached the mirror are extracted from
`quay.io/openshift-release-dev/ocp-release:<version>-multi`, which needs a Red
Hat pull secret. It is looked for in `$REGISTRY_AUTH_FILE`, then the configured
path, then `~/.config/openshift-tools/pull-secret.json`, `~/.docker/config.json`
and `~/.config/containers/auth.json`.

```console
$ latest-openshift default set-pull-secret ~/redhat-pull-secret.txt
```

Reading a release image and pulling its payload need different entitlements, so
a secret that can list a release may still fail to extract from it. A full Red
Hat pull secret is needed for extraction.

## Platform notes

- **glibc.** On Linux with glibc 2.31 or older, the `rhel8` build of a tool is
  chosen over the default `rhel9` one. This applies only to the machine you are
  running on; the glibc of a machine you are merely cross-downloading for is
  unknowable, so those get the mirror's default.
- **Architecture directories.** Inside each architecture directory an
  unqualified Linux filename means *that* directory's architecture:
  `ccoctl-linux-4.22.13.tar.gz` is x86-64 under `x86_64/` and AArch64 under
  `arm64/`. Tools that only publish the unqualified name — ccoctl, opm,
  oc-mirror — are reachable for a given architecture only from its own
  directory, so downloads read the matching one.
- Not every tool exists for every platform. `ccoctl` is Linux-only, and there
  is no Windows `openshift-install`.

## Environment variables

| Variable | Effect |
| --- | --- |
| `OCP_VERSION` | Version for this invocation; beats `OC_VERSION` and the configured default |
| `OC_VERSION` | Fallback for `OCP_VERSION`; beats the configured default |
| `REGISTRY_AUTH_FILE` | Pull secret for release images; beats the configured one |
| `OPENSHIFT_TOOLS_CACHE` | Cache root (default `~/.cache/openshift-tools`) |
| `OPENSHIFT_TOOLS_CONFIG` | Config directory (default `~/.config/openshift-tools`) |
| `OPENSHIFT_TOOLS_BIN` | Where `link` puts symlinks (default `~/.local/bin`) |
| `CI` | When truthy, suppresses spinners, progress bars and tables |

## Development

```console
uv sync
uv run pytest
```

The tests run against a synthetic mirror served through an httpx mock
transport, so the suite is offline and fast.

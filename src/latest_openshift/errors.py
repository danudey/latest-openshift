"""Exception types raised by this package.

Every error the CLI expects to hit derives from :class:`OpenShiftToolsError`, so
``cli.main`` can turn them into a one-line message on stderr and a non-zero exit
status instead of a traceback.
"""

from __future__ import annotations


class OpenShiftToolsError(Exception):
    """Base class for expected, user-facing failures."""


class InvalidVersionError(OpenShiftToolsError):
    """A version string could not be parsed."""


class VersionNotFoundError(OpenShiftToolsError):
    """No release on the mirror matches the requested version."""


class ToolNotFoundError(OpenShiftToolsError):
    """A release exists, but it does not publish the requested tool."""


class PlatformError(OpenShiftToolsError):
    """The requested platform is not understood or not published."""


class MirrorError(OpenShiftToolsError):
    """The mirror could not be reached or returned something unusable."""


class PrereleaseError(OpenShiftToolsError):
    """A prerelease could not be obtained from the release image registry."""


class ConfigError(OpenShiftToolsError):
    """The on-disk configuration file is unusable."""

"""Query mirror.openshift.com and manage versioned OpenShift client tools."""

from __future__ import annotations

from .errors import (
    ConfigError,
    InvalidVersionError,
    MirrorError,
    OpenShiftToolsError,
    PlatformError,
    PrereleaseError,
    ToolNotFoundError,
    VersionNotFoundError,
)
from .platforms import Platform, current_platform
from .versions import Version

__all__ = [
    "ConfigError",
    "InvalidVersionError",
    "MirrorError",
    "OpenShiftToolsError",
    "Platform",
    "PlatformError",
    "PrereleaseError",
    "ToolNotFoundError",
    "Version",
    "VersionNotFoundError",
    "current_platform",
]

__version__ = "0.1.0"

"""Parsing and ordering of OpenShift version numbers.

OpenShift versions on the mirror look like ``4.22.13``, ``4.22.0-rc.5`` or
``5.0.0-ec.6``.  Users also refer to *partial* versions -- ``4`` or ``4.22`` --
meaning "whatever the newest release under that prefix is".  One class covers
both: a :class:`Version` with ``minor``/``patch`` set to ``None`` is a partial
spec, and :meth:`Version.is_complete` distinguishes the two.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import total_ordering

from .errors import InvalidVersionError

_VERSION_RE = re.compile(
    r"""
    ^v?
    (?P<major>\d+)
    (?:\.(?P<minor>\d+))?
    (?:\.(?P<patch>\d+))?
    (?:-(?P<pre>[0-9A-Za-z.\-]+))?
    $
    """,
    re.VERBOSE,
)

# Identifier sort keys are 3-tuples so numeric and alphanumeric prerelease
# identifiers can be compared without TypeError. Numeric identifiers sort
# before alphanumeric ones, per semver.
_NUMERIC_ID = 0
_ALPHA_ID = 1


def _identifier_key(identifier: str) -> tuple[int, int, str]:
    if identifier.isdigit():
        return (_NUMERIC_ID, int(identifier), "")
    return (_ALPHA_ID, 0, identifier)


@total_ordering
@dataclass(frozen=True)
class Version:
    """A full or partial OpenShift version.

    ``4`` parses to ``Version(4, None, None)``, ``4.22`` to
    ``Version(4, 22, None)`` and ``4.22.0-rc.5`` to
    ``Version(4, 22, 0, ("rc", "5"))``.
    """

    major: int
    minor: int | None = None
    patch: int | None = None
    pre: tuple[str, ...] = field(default=())

    @classmethod
    def parse(cls, text: str) -> Version:
        """Parse ``text`` into a :class:`Version`.

        Raises :class:`InvalidVersionError` if it is not a version at all, or
        if it has a gap in it (``4..3``, or a prerelease on a partial version).
        """
        candidate = text.strip()
        match = _VERSION_RE.match(candidate)
        if not match:
            raise InvalidVersionError(f"not a valid OpenShift version: {text!r}")

        minor = match["minor"]
        patch = match["patch"]
        pre = match["pre"]

        if patch is not None and minor is None:  # pragma: no cover - regex forbids
            raise InvalidVersionError(f"version {text!r} has a patch but no minor")
        if pre is not None and patch is None:
            raise InvalidVersionError(
                f"version {text!r} has a prerelease suffix but is not a complete "
                "major.minor.patch version"
            )

        return cls(
            major=int(match["major"]),
            minor=int(minor) if minor is not None else None,
            patch=int(patch) if patch is not None else None,
            pre=tuple(pre.split(".")) if pre else (),
        )

    @classmethod
    def try_parse(cls, text: str) -> Version | None:
        """Like :meth:`parse`, but return ``None`` instead of raising."""
        try:
            return cls.parse(text)
        except InvalidVersionError:
            return None

    @property
    def is_complete(self) -> bool:
        """True when this names one specific release, not a family of them."""
        return self.minor is not None and self.patch is not None

    @property
    def is_prerelease(self) -> bool:
        return bool(self.pre)

    @property
    def stream(self) -> Version:
        """The ``major.minor`` version this release belongs to.

        Only meaningful once ``minor`` is known; for a major-only spec this
        returns the spec unchanged.
        """
        if self.minor is None:
            return Version(self.major)
        return Version(self.major, self.minor)

    def matches(self, other: Version) -> bool:
        """True if ``other`` falls under this (possibly partial) spec.

        ``Version.parse("4.22").matches(Version.parse("4.22.13"))`` is True;
        the reverse is not.
        """
        if other.major != self.major:
            return False
        if self.minor is not None and other.minor != self.minor:
            return False
        if self.patch is not None and other.patch != self.patch:
            return False
        # Kept as a guard like the three above rather than folded into the
        # return, so the components read as one uniform list of checks.
        if self.pre and other.pre != self.pre:  # noqa: SIM103
            return False
        return True

    def _sort_key(self) -> tuple:
        # Unspecified components sort low so that 4 < 4.22 < 4.22.13, and a
        # prerelease sorts below the release it leads up to.
        release_rank = 0 if self.pre else 1
        return (
            self.major,
            -1 if self.minor is None else self.minor,
            -1 if self.patch is None else self.patch,
            release_rank,
            tuple(_identifier_key(part) for part in self.pre),
        )

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self._sort_key() < other._sort_key()

    def __str__(self) -> str:
        parts = [str(self.major)]
        if self.minor is not None:
            parts.append(str(self.minor))
        if self.patch is not None:
            parts.append(str(self.patch))
        text = ".".join(parts)
        if self.pre:
            text += "-" + ".".join(self.pre)
        return text

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Version({self!s})"

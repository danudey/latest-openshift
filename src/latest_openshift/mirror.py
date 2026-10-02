"""HTTP access to mirror.openshift.com.

The mirror serves plain Apache-style directory indexes, so everything here is
built on listing a directory and reading ``release.txt``.  Listings are cached
on disk with a short TTL: the shim consults the mirror on every invocation of
``oc`` or ``openshift-install``, and it must not pay for a round trip each time.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Self

import httpx

from . import paths, platforms
from .errors import MirrorError
from .versions import Version

DEFAULT_BASE_URL = "https://mirror.openshift.com/pub"

#: Released and release-candidate builds.
CHANNEL_STANDARD = "ocp"
#: Engineering-candidate and dev-preview builds.
CHANNEL_DEV_PREVIEW = "ocp-dev-preview"

#: The architecture directory used when the architecture does not matter --
#: for reading ``release.txt`` and enumerating the ``latest-*`` pointers, both
#: of which are identical in every architecture directory.
CANONICAL_ARCH_DIR = platforms.CANONICAL_ARCH_DIR

#: How long a cached directory listing stays fresh.
DEFAULT_TTL_SECONDS = 1800

#: OpenShift 3 predates the ``<arch>/clients/<channel>`` layout entirely, so
#: its tree is skipped rather than fetched and found wanting.
MIN_MAJOR = 4

_HREF_RE = re.compile(r'href="([^"?][^"]*)"', re.IGNORECASE)
_RELEASE_NAME_RE = re.compile(r"^Name:\s*(\S+)\s*$", re.MULTILINE)
_LATEST_DIR_RE = re.compile(r"^latest-(\d+)\.(\d+)/$")

_USER_AGENT = "latest-openshift (+https://mirror.openshift.com)"


@dataclass(frozen=True)
class Listing:
    """The parsed contents of one mirror directory index."""

    url: str
    directories: tuple[str, ...]
    files: tuple[str, ...]


class Mirror:
    """A client for the OpenShift mirror, with an on-disk listing cache."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        arch_dir: str = CANONICAL_ARCH_DIR,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        cache_dir: Path | None = None,
        client: httpx.Client | None = None,
        timeout: float = 30.0,
        retries: int = 3,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.arch_dir = arch_dir
        self.ttl_seconds = ttl_seconds
        self.cache_dir = cache_dir if cache_dir is not None else paths.http_cache_dir()
        self.retries = retries
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
            transport=httpx.HTTPTransport(retries=retries),
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    # ------------------------------------------------------------------
    # URL construction
    # ------------------------------------------------------------------

    def clients_url(
        self,
        major: int,
        channel: str = CHANNEL_STANDARD,
        arch_dir: str | None = None,
    ) -> str:
        """URL of the clients directory for one major version and channel."""
        return (
            f"{self.base_url}/openshift-v{major}"
            f"/{arch_dir or self.arch_dir}/clients/{channel}"
        )

    def directory_url(
        self,
        major: int,
        name: str,
        channel: str = CHANNEL_STANDARD,
        arch_dir: str | None = None,
    ) -> str:
        """URL of a named directory, such as ``latest-4.22`` or ``4.22.13``."""
        return f"{self.clients_url(major, channel, arch_dir)}/{name}"

    # ------------------------------------------------------------------
    # Fetching
    # ------------------------------------------------------------------

    def list_directory(self, url: str, *, refresh: bool = False) -> Listing | None:
        """List a mirror directory, or return ``None`` if it does not exist.

        Both hits and misses are cached, so a repeated lookup for a version
        that is not published does not re-request it.
        """
        cached = None if refresh else self._read_cache(url)
        if cached is not None:
            if cached.get("missing"):
                return None
            return Listing(
                url=url,
                directories=tuple(cached["directories"]),
                files=tuple(cached["files"]),
            )

        body = self._get_text(url if url.endswith("/") else url + "/")
        if body is None:
            self._write_cache(url, {"missing": True})
            return None

        directories: list[str] = []
        files: list[str] = []
        for href in _HREF_RE.findall(body):
            if href.startswith((".", "/", "http:", "https:")):
                continue
            if href.endswith("/"):
                directories.append(href)
            else:
                files.append(href)

        listing = Listing(
            url=url,
            directories=tuple(sorted(set(directories))),
            files=tuple(sorted(set(files))),
        )
        self._write_cache(
            url,
            {"directories": list(listing.directories), "files": list(listing.files)},
        )
        return listing

    def read_release_name(
        self, directory_url: str, *, refresh: bool = False
    ) -> Version | None:
        """The version a release directory documents, from its ``release.txt``.

        This is the authoritative answer for the ``latest-X.Y`` directories:
        the directory name says which stream it belongs to, and ``release.txt``
        says which patch release is currently in it.
        """
        cache_key = directory_url + "#release.txt"
        cached = None if refresh else self._read_cache(cache_key)
        if cached is not None:
            name = cached.get("name")
            return Version.try_parse(name) if name else None

        body = self._get_text(f"{directory_url.rstrip('/')}/release.txt")
        name = None
        if body is not None:
            match = _RELEASE_NAME_RE.search(body)
            if match:
                name = match[1]
        self._write_cache(cache_key, {"name": name})
        return Version.try_parse(name) if name else None

    def list_release_streams(self, major: int, *, refresh: bool = False) -> list[Version]:
        """Every ``major.minor`` stream with a ``latest-*`` pointer directory.

        A stream only gets a ``latest-X.Y`` directory once it has an actual
        release, which is exactly the distinction the mirror's bare version
        directories fail to make.
        """
        listing = self.list_directory(self.clients_url(major), refresh=refresh)
        if listing is None:
            return []
        streams = []
        for entry in listing.directories:
            match = _LATEST_DIR_RE.match(entry)
            if match:
                streams.append(Version(int(match[1]), int(match[2])))
        return sorted(streams)

    def list_majors(self, *, refresh: bool = False) -> list[int]:
        """Major versions that have a tree under ``/pub``, newest first."""
        listing = self.list_directory(self.base_url, refresh=refresh)
        if listing is None:
            raise MirrorError(f"could not list {self.base_url}")
        majors = []
        for entry in listing.directories:
            match = re.match(r"^openshift-v(\d+)/$", entry)
            if match and int(match[1]) >= MIN_MAJOR:
                majors.append(int(match[1]))
        if not majors:
            raise MirrorError(f"no openshift-v* trees found under {self.base_url}")
        return sorted(majors, reverse=True)

    def download(
        self,
        url: str,
        destination: Path,
        *,
        on_start: Callable[[int | None], None] | None = None,
        on_progress: Callable[[int], None] | None = None,
        chunk_size: int = 1 << 20,
    ) -> Path:
        """Stream ``url`` to ``destination``.

        Writes to a sibling ``.part`` file and renames on success, so an
        interrupted download never leaves a truncated archive in the cache.
        """
        paths.ensure_dir(destination.parent)
        partial = destination.with_suffix(destination.suffix + ".part")

        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                with self._client.stream("GET", url) as response:
                    if response.status_code == 404:
                        raise MirrorError(f"not found on the mirror: {url}")
                    response.raise_for_status()
                    total = response.headers.get("content-length")
                    if on_start is not None:
                        on_start(int(total) if total is not None else None)
                    with partial.open("wb") as handle:
                        for chunk in response.iter_bytes(chunk_size):
                            handle.write(chunk)
                            if on_progress is not None:
                                on_progress(len(chunk))
                break
            except MirrorError:
                partial.unlink(missing_ok=True)
                raise
            except httpx.HTTPError as exc:
                last_error = exc
                partial.unlink(missing_ok=True)
                if attempt == self.retries - 1:
                    raise MirrorError(f"failed to download {url}: {exc}") from exc
                time.sleep(2**attempt)
        else:  # pragma: no cover - loop always breaks or raises
            raise MirrorError(f"failed to download {url}: {last_error}")

        partial.replace(destination)
        return destination

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _get_text(self, url: str) -> str | None:
        """GET ``url`` as text, returning ``None`` for a 404."""
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                response = self._client.get(url)
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt == self.retries - 1:
                    raise MirrorError(f"could not reach {url}: {exc}") from exc
                time.sleep(2**attempt)
                continue
            if response.status_code == 404:
                return None
            if response.is_success:
                return response.text
            if response.is_server_error and attempt < self.retries - 1:
                time.sleep(2**attempt)
                continue
            raise MirrorError(f"{url} returned HTTP {response.status_code}")
        raise MirrorError(f"could not reach {url}: {last_error}")  # pragma: no cover

    def _cache_path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        return self.cache_dir / f"{digest}.json"

    def _read_cache(self, key: str) -> dict | None:
        if self.ttl_seconds <= 0:
            return None
        path = self._cache_path(key)
        try:
            raw = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(raw, dict):
            return None
        if time.time() - raw.get("fetched_at", 0) > self.ttl_seconds:
            return None
        payload = raw.get("payload")
        return payload if isinstance(payload, dict) else None

    def _write_cache(self, key: str, payload: dict) -> None:
        if self.ttl_seconds <= 0:
            return
        path = self._cache_path(key)
        try:
            paths.ensure_dir(path.parent)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps({"key": key, "fetched_at": time.time(), "payload": payload}),
                encoding="utf-8",
            )
            tmp.replace(path)
        except OSError:
            # A cache we cannot write is a slow tool, not a broken one.
            pass

    def clear_cache(self) -> int:
        """Delete every cached listing. Returns the number of files removed."""
        removed = 0
        for entry in _iter_files(self.cache_dir):
            try:
                entry.unlink()
                removed += 1
            except OSError:
                pass
        return removed


def _iter_files(directory: Path) -> Iterator[Path]:
    if not directory.is_dir():
        return
    for entry in directory.iterdir():
        if entry.is_file():
            yield entry

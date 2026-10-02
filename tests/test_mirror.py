from __future__ import annotations

import httpx
import pytest

import fake_mirror
from latest_openshift.errors import MirrorError
from latest_openshift.mirror import CHANNEL_DEV_PREVIEW, Mirror
from latest_openshift.versions import Version

OCP = f"{fake_mirror.BASE}/openshift-v4/x86_64/clients/ocp"


def test_list_directory_parses_an_index(mirror):
    listing = mirror.list_directory(f"{OCP}/latest-4.22")

    assert "openshift-client-linux-4.22.13.tar.gz" in listing.files
    assert "release.txt" in listing.files
    # The parent-directory link must not be mistaken for an entry.
    assert "../" not in listing.directories


def test_list_directory_separates_files_from_directories(mirror):
    listing = mirror.list_directory(OCP)

    assert "latest-4.22/" in listing.directories
    assert listing.files == ()


def test_list_directory_returns_none_for_a_missing_directory(mirror):
    assert mirror.list_directory(f"{OCP}/4.99.0") is None


def test_listings_are_cached(fake):
    with Mirror(client=fake.client()) as mirror:
        mirror.list_directory(f"{OCP}/latest-4.22")
        first = len(fake.requests)
        mirror.list_directory(f"{OCP}/latest-4.22")

    assert len(fake.requests) == first


def test_misses_are_cached_too(fake):
    """A version that is not published should be asked about only once."""
    with Mirror(client=fake.client()) as mirror:
        assert mirror.list_directory(f"{OCP}/4.99.0") is None
        first = len(fake.requests)
        assert mirror.list_directory(f"{OCP}/4.99.0") is None

    assert len(fake.requests) == first


def test_refresh_bypasses_the_cache(fake):
    with Mirror(client=fake.client()) as mirror:
        mirror.list_directory(f"{OCP}/latest-4.22")
        first = len(fake.requests)
        mirror.list_directory(f"{OCP}/latest-4.22", refresh=True)

    assert len(fake.requests) > first


def test_zero_ttl_disables_the_cache(fake):
    with Mirror(client=fake.client(), ttl_seconds=0) as mirror:
        mirror.list_directory(f"{OCP}/latest-4.22")
        first = len(fake.requests)
        mirror.list_directory(f"{OCP}/latest-4.22")

    assert len(fake.requests) > first


def test_read_release_name(mirror):
    assert mirror.read_release_name(f"{OCP}/latest-4.22") == Version.parse("4.22.13")


def test_read_release_name_is_none_when_absent(mirror):
    assert mirror.read_release_name(f"{OCP}/unreleased") is None


def test_list_release_streams_finds_only_latest_pointers(mirror):
    streams = mirror.list_release_streams(4)

    assert [str(s) for s in streams] == ["4.20", "4.21", "4.22"]


def test_list_majors_is_newest_first(mirror):
    assert mirror.list_majors() == [5, 4]


def test_directory_url_honours_the_arch_directory(mirror):
    assert mirror.directory_url(4, "latest-4.22").endswith(
        "openshift-v4/x86_64/clients/ocp/latest-4.22"
    )
    assert mirror.directory_url(4, "latest-4.22", arch_dir="arm64").endswith(
        "openshift-v4/arm64/clients/ocp/latest-4.22"
    )
    assert "ocp-dev-preview" in mirror.directory_url(
        4, "5.0.0-ec.1", channel=CHANNEL_DEV_PREVIEW
    )


def test_download_streams_to_a_file_and_reports_progress(tmp_path, fake):
    url = f"{OCP}/latest-4.22/openshift-client-linux-4.22.13.tar.gz"
    fake.blobs[url] = b"x" * 4096
    seen = []

    with Mirror(client=fake.client()) as mirror:
        target = mirror.download(
            url,
            tmp_path / "client.tar.gz",
            on_start=lambda total: seen.append(("start", total)),
            on_progress=lambda n: seen.append(("chunk", n)),
        )

    assert target.read_bytes() == b"x" * 4096
    assert seen[0] == ("start", 4096)
    assert sum(n for kind, n in seen if kind == "chunk") == 4096


def test_download_leaves_no_partial_file_on_failure(tmp_path, fake):
    url = f"{OCP}/latest-4.22/missing.tar.gz"
    target = tmp_path / "missing.tar.gz"

    with Mirror(client=fake.client()) as mirror:
        with pytest.raises(MirrorError, match="not found"):
            mirror.download(url, target)

    assert not target.exists()
    assert not target.with_suffix(".gz.part").exists()


def test_transport_errors_become_mirror_errors():
    def explode(request):
        raise httpx.ConnectError("no route to host")

    client = httpx.Client(transport=httpx.MockTransport(explode))
    with Mirror(client=client, retries=1) as mirror:
        with pytest.raises(MirrorError, match="could not reach"):
            mirror.list_directory(OCP)


def test_server_errors_become_mirror_errors():
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(403))
    )
    with Mirror(client=client, retries=1) as mirror:
        with pytest.raises(MirrorError, match="HTTP 403"):
            mirror.list_directory(OCP)


def test_clear_cache(fake):
    with Mirror(client=fake.client()) as mirror:
        mirror.list_directory(f"{OCP}/latest-4.22")
        assert mirror.clear_cache() >= 1
        assert mirror.clear_cache() == 0

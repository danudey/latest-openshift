from __future__ import annotations

import pytest

from latest_openshift import resolve
from latest_openshift.errors import VersionNotFoundError
from latest_openshift.mirror import CHANNEL_DEV_PREVIEW, CHANNEL_STANDARD
from latest_openshift.resolve import SOURCE_MIRROR, SOURCE_RELEASE_IMAGE
from latest_openshift.versions import Version


def test_list_streams(mirror):
    assert [str(s) for s in resolve.list_streams(mirror)] == ["4.20", "4.21", "4.22"]


def test_recent_streams_is_newest_first(mirror):
    assert [str(s) for s in resolve.recent_streams(mirror, 2)] == ["4.22", "4.21"]


def test_recent_streams_copes_with_asking_for_too_many(mirror):
    assert len(resolve.recent_streams(mirror, 99)) == 3


def test_latest_in_stream(mirror):
    version = resolve.latest_in_stream(mirror, Version.parse("4.21"))
    assert str(version) == "4.21.32"


def test_resolve_nothing_gives_the_newest_release(mirror):
    release = resolve.resolve(mirror, None)
    assert str(release.version) == "4.22.13"
    assert release.channel == CHANNEL_STANDARD


def test_resolve_a_major(mirror):
    assert str(resolve.resolve(mirror, Version.parse("4")).version) == "4.22.13"


def test_resolve_a_stream(mirror):
    assert str(resolve.resolve(mirror, Version.parse("4.20")).version) == "4.20.37"


def test_resolve_a_stream_uses_the_latest_pointer_not_the_bare_directory(mirror, fake):
    """4.22.99 is published but not released, so 4.22 must resolve to 4.22.13."""
    release = resolve.resolve(mirror, Version.parse("4.22"))

    assert str(release.version) == "4.22.13"
    assert release.directory == "latest-4.22"


def test_resolve_an_exact_version_is_taken_at_face_value(mirror):
    """An explicitly requested patch is used even if it is not the released one."""
    release = resolve.resolve(mirror, Version.parse("4.22.99"))

    assert str(release.version) == "4.22.99"
    assert release.directory == "4.22.99"
    assert release.channel == CHANNEL_STANDARD


def test_resolve_falls_back_to_dev_preview(mirror):
    release = resolve.resolve(mirror, Version.parse("5.0.0-ec.1"))

    assert release.channel == CHANNEL_DEV_PREVIEW
    assert release.is_dev_preview
    assert release.source == SOURCE_MIRROR


def test_resolve_falls_back_to_the_release_image(mirror):
    release = resolve.resolve(mirror, Version.parse("4.30.1"))

    assert release.source == SOURCE_RELEASE_IMAGE
    assert release.is_prerelease_image
    assert not release.on_mirror


def test_resolve_an_unreleased_major_fails(mirror):
    with pytest.raises(VersionNotFoundError, match="no released OpenShift 6"):
        resolve.resolve(mirror, Version.parse("6"))


def test_resolve_an_unreleased_stream_fails(mirror):
    with pytest.raises(VersionNotFoundError, match="no released OpenShift 4.99"):
        resolve.resolve(mirror, Version.parse("4.99"))


def test_release_directory_url_varies_by_architecture(mirror):
    release = resolve.resolve(mirror, Version.parse("4.22"))

    assert release.directory_url(mirror, "x86_64").endswith(
        "openshift-v4/x86_64/clients/ocp/latest-4.22"
    )
    assert release.directory_url(mirror, "arm64").endswith(
        "openshift-v4/arm64/clients/ocp/latest-4.22"
    )


def test_tree_major_falls_back_to_the_newest_tree(mirror):
    assert resolve.tree_major(mirror, 4) == 4
    assert resolve.tree_major(mirror, 5) == 5
    assert resolve.tree_major(mirror, 9) == 5

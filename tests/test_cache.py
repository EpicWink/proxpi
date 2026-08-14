"""Test ``proxpi`` cache internals."""

import datetime
from unittest import mock

import pytest

from proxpi import _cache


@pytest.mark.parametrize(("upload_time", "expected"), [
    pytest.param("2020-01-01T00:00:00Z", False, id="old_file"),
    pytest.param(None, False, id="unknown_included_by_default"),
])  # fmt: skip
def test_is_excluded_newer(upload_time, expected):
    """Test excluding files by upload time against a fixed window."""
    with mock.patch.object(_cache, "EXCLUDE_NEWER", datetime.timedelta(hours=1)):
        assert _cache._is_excluded_newer(upload_time) is expected


def test_is_excluded_newer_recent_file():
    """Test a recently-uploaded file is excluded."""
    upload_time = (
        datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    )
    with mock.patch.object(_cache, "EXCLUDE_NEWER", datetime.timedelta(hours=1)):
        assert _cache._is_excluded_newer(upload_time) is True


def test_is_excluded_newer_unknown_excluded():
    """Test files with unknown upload time are excluded when configured to."""
    exclude_newer_patch = mock.patch.object(
        _cache, "EXCLUDE_NEWER", datetime.timedelta(hours=1)
    )
    unknown_patch = mock.patch.object(_cache, "EXCLUDE_NEWER_UNKNOWN", True)
    with exclude_newer_patch, unknown_patch:
        assert _cache._is_excluded_newer(None) is True

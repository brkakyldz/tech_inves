"""Tests for FileCache: normal get/set round-trip, atomic writes, and
resilience to a corrupted cache file on disk."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from techinves.data.cache import FileCache


@pytest.fixture
def cache(tmp_path: Path) -> FileCache:
    return FileCache(cache_dir=tmp_path, ttl_seconds=3600)


def test_set_then_get_round_trips(cache: FileCache) -> None:
    cache.set("profile", "AAPL", {"price": 150.0}, params={"period": "annual"})
    assert cache.get("profile", "AAPL", params={"period": "annual"}) == {"price": 150.0}


def test_get_missing_key_returns_none(cache: FileCache) -> None:
    assert cache.get("profile", "MSFT") is None


def test_get_expired_entry_returns_none(tmp_path: Path) -> None:
    cache = FileCache(cache_dir=tmp_path, ttl_seconds=0)
    cache.set("profile", "AAPL", {"price": 150.0})
    assert cache.get("profile", "AAPL") is None


def test_corrupted_cache_file_is_treated_as_a_miss(cache: FileCache) -> None:
    path = cache._key_path("profile", "AAPL", None)
    path.write_text('{"cached_at": 12345, "data": {truncated garbage', encoding="utf-8")

    assert cache.get("profile", "AAPL") is None


def test_cache_file_with_unexpected_shape_is_treated_as_a_miss(cache: FileCache) -> None:
    path = cache._key_path("profile", "AAPL", None)
    path.write_text('{"not_an_envelope": true}', encoding="utf-8")

    assert cache.get("profile", "AAPL") is None


def test_set_writes_via_temp_file_and_replace(cache: FileCache) -> None:
    final_path = cache._key_path("profile", "AAPL", None)

    with patch("techinves.data.cache.os.replace") as mock_replace:
        cache.set("profile", "AAPL", {"price": 150.0})

    mock_replace.assert_called_once()
    (tmp_arg, dest_arg), _ = mock_replace.call_args
    # The write went to a temp file in the same directory, then replace()
    # was asked to move it onto the real destination path atomically.
    assert Path(tmp_arg).parent == cache.cache_dir
    assert Path(dest_arg) == final_path
    # Since os.replace was mocked out, nothing actually landed at the final path.
    assert not final_path.exists()


def test_interrupted_write_leaves_original_file_untouched(cache: FileCache) -> None:
    cache.set("profile", "AAPL", {"price": 150.0})
    path = cache._key_path("profile", "AAPL", None)
    original_contents = path.read_text(encoding="utf-8")

    with patch("techinves.data.cache.os.replace", side_effect=OSError("simulated crash")):
        with pytest.raises(OSError):
            cache.set("profile", "AAPL", {"price": 999.0})

    # Original file is untouched, and no leftover temp files remain.
    assert path.read_text(encoding="utf-8") == original_contents
    assert cache.get("profile", "AAPL") == {"price": 150.0}
    leftover_tmp_files = [p for p in cache.cache_dir.iterdir() if p != path]
    assert leftover_tmp_files == []

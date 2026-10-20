"""Comprehensive tests for smart_albums.py (groups), face worker utils, and thumbnails.py — part 2
of 2: TestThumbnailCache."""

from __future__ import annotations

import os

from bpp.web.thumbnails import ThumbnailCache
from tests._smart_album_coverage_shared import (  # noqa: F401
    _add_face,
    _add_pet,
    _make_jpeg,
    _make_photo,
    conn,
    db_path,
)

# ---------------------------------------------------------------------------
# ThumbnailCache tests
# ---------------------------------------------------------------------------


class TestThumbnailCache:
    """Tests for the ThumbnailCache class."""

    def test_register_and_get_hash(self, tmp_path):
        """Registering a file via build_map then get_hash should return a consistent hash."""
        cache_dir = str(tmp_path / "thumbs")
        cache = ThumbnailCache(cache_dir)

        img_path = tmp_path / "test.jpg"
        _make_jpeg(img_path)

        cache.build_map([{"filepath": str(img_path)}])
        h = cache.get_hash(str(img_path))
        assert h is not None
        assert len(h) == 32  # sha256[:32]

    def test_get_filepath_returns_original(self, tmp_path):
        """After build_map, get_filepath should return the original path."""
        cache_dir = str(tmp_path / "thumbs")
        cache = ThumbnailCache(cache_dir)

        img_path = tmp_path / "original.jpg"
        _make_jpeg(img_path)
        fp = str(img_path)

        cache.build_map([{"filepath": fp}])
        h = cache.get_hash(fp)
        assert cache.get_filepath(h) == fp

    def test_unknown_hash_returns_none(self, tmp_path):
        """get_thumbnail and get_filepath for an unknown hash should return None."""
        cache_dir = str(tmp_path / "thumbs")
        cache = ThumbnailCache(cache_dir)

        assert cache.get_thumbnail("nonexistent1234") is None
        assert cache.get_filepath("nonexistent1234") is None

    def test_clear_removes_thumbnails(self, tmp_path):
        """After clear(), cached thumbnail files should be gone."""
        cache_dir = str(tmp_path / "thumbs")
        cache = ThumbnailCache(cache_dir)

        img_path = tmp_path / "clearme.jpg"
        _make_jpeg(img_path)

        cache.build_map([{"filepath": str(img_path)}])
        h = cache.get_hash(str(img_path))

        # Generate the thumbnail
        thumb_result = cache.get_thumbnail(h)
        assert thumb_result is not None
        assert os.path.exists(thumb_result)

        # Clear
        removed = cache.clear()
        assert removed >= 1
        assert not os.path.exists(thumb_result)

    def test_register_same_file_twice(self, tmp_path):
        """Registering the same file twice should produce the same hash."""
        cache_dir = str(tmp_path / "thumbs")
        cache = ThumbnailCache(cache_dir)

        img_path = tmp_path / "dupe.jpg"
        _make_jpeg(img_path)
        fp = str(img_path)

        cache.build_map([{"filepath": fp}])
        h1 = cache.get_hash(fp)

        cache.build_map([{"filepath": fp}])
        h2 = cache.get_hash(fp)

        assert h1 == h2

    def test_get_thumbnail_generates_file(self, tmp_path):
        """get_thumbnail should lazily generate a thumbnail JPEG on disk."""
        cache_dir = str(tmp_path / "thumbs")
        cache = ThumbnailCache(cache_dir)

        img_path = tmp_path / "gen.jpg"
        _make_jpeg(img_path)

        cache.build_map([{"filepath": str(img_path)}])
        h = cache.get_hash(str(img_path))

        thumb = cache.get_thumbnail(h)
        assert thumb is not None
        assert os.path.isfile(thumb)
        assert thumb.endswith(".jpg")

    def test_get_thumbnail_returns_cached(self, tmp_path):
        """Second call to get_thumbnail should return the cached version."""
        cache_dir = str(tmp_path / "thumbs")
        cache = ThumbnailCache(cache_dir)

        img_path = tmp_path / "cached.jpg"
        _make_jpeg(img_path)

        cache.build_map([{"filepath": str(img_path)}])
        h = cache.get_hash(str(img_path))

        thumb1 = cache.get_thumbnail(h)
        thumb2 = cache.get_thumbnail(h)
        assert thumb1 == thumb2
        assert os.path.isfile(thumb2)

    def test_multiple_files_distinct_hashes(self, tmp_path):
        """Different files should have different hashes."""
        cache_dir = str(tmp_path / "thumbs")
        cache = ThumbnailCache(cache_dir)

        img1 = tmp_path / "a.jpg"
        img2 = tmp_path / "b.jpg"
        _make_jpeg(img1)
        _make_jpeg(img2)

        cache.build_map(
            [
                {"filepath": str(img1)},
                {"filepath": str(img2)},
            ]
        )
        h1 = cache.get_hash(str(img1))
        h2 = cache.get_hash(str(img2))
        assert h1 != h2

    def test_cache_version_clears_on_mismatch(self, tmp_path):
        """If cache version file is stale, __init__ should clear old thumbnails."""
        cache_dir = str(tmp_path / "thumbs")
        os.makedirs(cache_dir, exist_ok=True)

        # Write a stale version
        with open(os.path.join(cache_dir, ".version"), "w") as f:
            f.write("0")

        # Place a fake cached file
        fake_thumb = os.path.join(cache_dir, "old_thumb.jpg")
        with open(fake_thumb, "w") as f:
            f.write("stale")

        assert os.path.exists(fake_thumb)

        # Creating cache should clear stale files
        ThumbnailCache(cache_dir)
        assert not os.path.exists(fake_thumb), (
            "Stale thumbnails should be cleared on version mismatch"
        )

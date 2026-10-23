"""Dropping one photo's cached variants must not list the cache folder.

The cache holds a few files per photo, so a glob per invalidation reads
the whole folder (hundreds of thousands of entries) for every edit save
or delete. The variant names are known from PHOTO_CACHE_SUFFIXES.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest


@pytest.fixture()
def cache(tmp_path):
    from bpp.web.thumbnails import ThumbnailCache

    return ThumbnailCache(str(tmp_path / "cache"))


def _seed(cache, h):
    from bpp.constants import PHOTO_CACHE_SUFFIXES

    names = [f"{h}{s}.jpg" for s in PHOTO_CACHE_SUFFIXES] + [
        f"{h}{s}.png" for s in PHOTO_CACHE_SUFFIXES
    ]
    for n in names:
        with open(os.path.join(cache.cache_dir, n), "wb") as f:
            f.write(b"x")
    return names


def _forbid_listing(monkeypatch):
    def _no(*a, **k):
        raise AssertionError("cleanup listed the cache directory")

    monkeypatch.setattr(os, "listdir", _no)
    monkeypatch.setattr(os, "scandir", _no)


def test_invalidate_removes_every_variant_without_listing(cache, monkeypatch):
    h, other = "a" * 32, "b" * 32
    names = _seed(cache, h)
    content_names = _seed(cache, "c" * 32)
    keep = _seed(cache, other)
    from bpp.web.photo_cache import invalidate_photo_cache

    _forbid_listing(monkeypatch)
    invalidate_photo_cache(SimpleNamespace(thumbs=cache), h, content_hash="c" * 32)
    gone = [n for n in names + content_names if os.path.exists(os.path.join(cache.cache_dir, n))]
    assert gone == []
    assert all(os.path.exists(os.path.join(cache.cache_dir, n)) for n in keep)


def test_remove_for_hash_removes_every_variant_without_listing(cache, monkeypatch):
    h = "d" * 32
    names = _seed(cache, h)
    _forbid_listing(monkeypatch)
    removed = cache.remove_for_hash(h)
    assert removed == len(names)
    assert not any(os.path.exists(os.path.join(cache.cache_dir, n)) for n in names)

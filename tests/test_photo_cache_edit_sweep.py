"""An edit save sweeps only the edit-dependent cache variants; the full-size
conversion (slow for a camera RAW) and the other derivatives stay."""

from __future__ import annotations

import os
from types import SimpleNamespace

from bpp.constants import (
    PHOTO_CACHE_SUFFIX_EDITED,
    PHOTO_CACHE_SUFFIX_EDITED_THUMB,
    PHOTO_CACHE_SUFFIX_FULL,
    PHOTO_CACHE_SUFFIXES,
)
from bpp.web.photo_cache import cached_variant_path, invalidate_photo_cache


def _ctx(cache_dir):
    thumbs = SimpleNamespace(cache_dir=str(cache_dir), invalidate=lambda h: None)
    return SimpleNamespace(thumbs=thumbs)


def _make_all(cache_dir, h):
    paths = [cached_variant_path(str(cache_dir), h, s) for s in PHOTO_CACHE_SUFFIXES]
    for p in paths:
        with open(p, "wb") as f:
            f.write(b"x")
    return paths


def test_edits_only_keeps_the_conversion(tmp_path):
    paths = _make_all(tmp_path, "abc")
    invalidate_photo_cache(_ctx(tmp_path), "abc", edits_only=True)
    gone = {os.path.basename(p) for p in paths if not os.path.exists(p)}
    assert gone == {
        f"abc{PHOTO_CACHE_SUFFIX_EDITED}.jpg",
        f"abc{PHOTO_CACHE_SUFFIX_EDITED_THUMB}.jpg",
    }
    assert os.path.exists(cached_variant_path(str(tmp_path), "abc", PHOTO_CACHE_SUFFIX_FULL))


def test_a_full_sweep_still_removes_everything(tmp_path):
    paths = _make_all(tmp_path, "abc")
    invalidate_photo_cache(_ctx(tmp_path), "abc")
    assert not any(os.path.exists(p) for p in paths)

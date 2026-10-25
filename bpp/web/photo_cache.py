"""Dropping a photo's cached derivatives (thumbnail, full-size, edited,
inpainted, sprite) after the file or its edits changed."""

from __future__ import annotations

import contextlib
import os
from typing import Any

from bpp.constants import (
    PHOTO_CACHE_SUFFIX_EDITED,
    PHOTO_CACHE_SUFFIX_EDITED_THUMB,
    PHOTO_CACHE_SUFFIXES,
)

_CACHE_EXTS = (".jpg", ".png")

#: The variants that depend on the photo's saved edits and nothing else.
#: An edit save sweeps these only: the full-size conversion (``_full``) is
#: edit-independent, and for a camera RAW it is a slow decode.
EDIT_VARIANT_SUFFIXES = (PHOTO_CACHE_SUFFIX_EDITED, PHOTO_CACHE_SUFFIX_EDITED_THUMB)


def cached_variant_path(cache_dir: str, path_hash: str, suffix: str) -> str:
    """Where one cached derivative of a photo lives (JPEG)."""
    return os.path.join(cache_dir, f"{path_hash}{suffix}.jpg")


def cached_variant_paths(cache_dir: str, hashes: set[str]) -> list[str]:
    """Every file a photo's cached derivatives can live at: one per
    registered suffix and extension. Removing these needs no directory
    listing, which matters because the cache folder holds a few files
    per photo of the library and is swept on every edit save or delete."""
    return [
        os.path.join(cache_dir, f"{h}{suffix}{ext}")
        for h in hashes
        for suffix in PHOTO_CACHE_SUFFIXES
        for ext in _CACHE_EXTS
    ]


def invalidate_photo_cache(
    ctx: Any, path_hash: str, *, content_hash: str | None = None, edits_only: bool = False
) -> None:
    """Remove cached full photo and thumbnail variants for a hash.

    The variant names come from ``PHOTO_CACHE_SUFFIXES`` (a new variant
    is one registry entry, checked by tests/test_cache_suffix_registry.py),
    so the sweep is a handful of unlinks and never lists the folder.

    ``content_hash`` is the truncated SHA-256 of the file content: the
    inpaint cache is content-addressed, so when it is given both hashes
    are swept. ``edits_only`` (an edit save, enhance or reset) removes
    just the edit-dependent variants and keeps the full-size conversion.
    """
    if not ctx.thumbs:
        return
    ctx.thumbs.invalidate(path_hash)
    cache_dir = ctx.thumbs.cache_dir

    hashes = {path_hash}
    if content_hash:
        hashes.add(content_hash)
    if edits_only:
        targets = [cached_variant_path(cache_dir, path_hash, s) for s in EDIT_VARIANT_SUFFIXES]
    else:
        targets = cached_variant_paths(cache_dir, hashes)
    for cached in targets:
        # A variant that won't go (locked by a sync client, read-only
        # folder) must not fail the edit save or delete that called us.
        with contextlib.suppress(OSError):
            os.remove(cached)

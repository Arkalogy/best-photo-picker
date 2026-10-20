"""Dropping a photo's cached derivatives (thumbnail, full-size, edited,
inpainted, sprite) after the file or its edits changed."""

from __future__ import annotations

import contextlib
import glob
import os
from typing import Any


def invalidate_photo_cache(ctx: Any, path_hash: str, *, content_hash: str | None = None) -> None:
    """Remove cached full photo and thumbnail variants for a hash.

    Glob-based cleanup: matches ``{path_hash}*.jpg`` and ``{path_hash}*.png``
    so any current OR future suffix variant is caught automatically.
    The ``PHOTO_CACHE_SUFFIXES`` registry in bpp.constants stays as
    documentation but is no longer load-bearing for cleanup correctness.

    ``content_hash`` is the truncated SHA-256 of the file content: the
    inpaint cache is content-addressed, so when it is given both hashes
    are swept.
    """
    if not ctx.thumbs:
        return
    ctx.thumbs.invalidate(path_hash)
    cache_dir = ctx.thumbs.cache_dir

    hashes = {path_hash}
    if content_hash:
        hashes.add(content_hash)
    for h in hashes:
        # Glob matches the base thumb ({h}.jpg) and every suffix variant
        # ({h}_full.jpg, {h}_edited.jpg, {h}_inpainted.png, …).
        for ext in (".jpg", ".png"):
            for cached in glob.glob(os.path.join(cache_dir, f"{h}*{ext}")):
                with contextlib.suppress(OSError):
                    os.remove(cached)

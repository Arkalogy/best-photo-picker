"""Two requests for the same cached image must not corrupt it.

The full-size and edited caches are built on demand from the lightbox,
compare, slideshow, preview and editor, so two requests for one photo
overlap easily. Each writer now uses its own temp name and the output
path is written under a per-path lock, with a freshness re-check inside
it so the second writer reuses the first's result.
"""

from __future__ import annotations

import os
import threading

from PIL import Image

from bpp.web.photo_edits import _generate_cached_image


def test_concurrent_writers_produce_one_valid_file_and_no_leftovers(tmp_path):
    src = str(tmp_path / "src.jpg")
    Image.new("RGB", (64, 64), "blue").save(src, "JPEG")
    out = str(tmp_path / "cache" / "out.jpg")
    os.makedirs(os.path.dirname(out))

    results: list[bool] = []
    start = threading.Barrier(4)

    def write():
        start.wait()
        results.append(_generate_cached_image(src, out, edits=None))

    threads = [threading.Thread(target=write) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert results == [True] * 4
    with Image.open(out) as img:
        img.verify()  # a half-written or interleaved JPEG fails here
    assert [f for f in os.listdir(os.path.dirname(out)) if f.endswith(".tmp")] == []

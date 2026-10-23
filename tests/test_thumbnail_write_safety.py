"""Second-pass reliability review (v0.2.0): thumbnails are written to a
temp file and renamed into place; the cache warmer survives a delete
while it runs."""

from __future__ import annotations

import os

from PIL import Image


def test_thumbnail_is_renamed_into_place(tmp_path, monkeypatch):
    from PIL import Image as PILImage

    from bpp.output.gallery import _make_thumbnail

    src = str(tmp_path / "a.jpg")
    PILImage.new("RGB", (64, 48)).save(src, "JPEG")
    dest = str(tmp_path / "thumb.jpg")
    seen: list[str] = []
    real_save = PILImage.Image.save

    def spy(self, fp, *a, **k):
        seen.append(str(fp))
        return real_save(self, fp, *a, **k)

    monkeypatch.setattr(PILImage.Image, "save", spy)
    assert _make_thumbnail(src, dest, size=32)
    assert os.path.exists(dest)
    assert seen and seen[-1] != dest, "written straight to the final name"
    assert not os.path.exists(seen[-1]), "temp file left behind"
    with Image.open(dest) as out:
        assert out.size == (32, 24)


def test_warmer_survives_a_delete_during_the_loop(tmp_path):
    from bpp.web.thumbnails import ThumbnailCache

    cache = ThumbnailCache(str(tmp_path / "cache"))
    paths = []
    for i in range(6):
        p = tmp_path / f"p{i}.jpg"
        Image.new("RGB", (8, 8)).save(p, "JPEG")
        paths.append(str(p))
    cache.build_map_from_paths(paths)
    hashes = [cache.get_hash(p) for p in paths]
    import bpp.web.thumbnails as th

    real = th._make_thumbnail
    calls = {"n": 0}

    def make(src, dest, size=None):
        calls["n"] += 1
        if calls["n"] == 2:  # the user empties the trash mid-warm
            cache.remove_for_hash(hashes[-1])
        return real(src, dest, size=size)

    th._make_thumbnail = make
    try:
        n = cache.warm_cache()
    finally:
        th._make_thumbnail = real
    assert n >= 4

"""A file the app creates in a user-visible folder or a cache is a new
file of its own: a link planted at the name never redirects the
write, and the writers that generate images into caches use it."""

from __future__ import annotations

import errno
import os
import threading

import numpy as np
import pytest
from PIL import Image


def test_a_fresh_file_is_always_this_process_own_inode(tmp_path):
    from bpp.utils.fresh_file import open_fresh

    victim = tmp_path / "victim"
    victim.write_bytes(b"v")
    target = tmp_path / "out"
    target.symlink_to(victim)
    with open_fresh(str(target)) as fh:
        fh.write(b"new")
    assert victim.read_bytes() == b"v" and not target.is_symlink() and target.read_bytes() == b"new"
    # A stale regular file at the name is replaced, in text mode too.
    with open_fresh(str(target), "w", encoding="utf-8") as fh:
        fh.write("text")
    assert target.read_text() == "text"


def test_a_folder_in_the_way_is_one_failed_write_on_every_platform(tmp_path):
    from bpp.utils.fresh_file import open_fresh

    (tmp_path / "taken").mkdir()
    with pytest.raises(OSError) as exc:
        open_fresh(str(tmp_path / "taken"))
    assert exc.value.errno == errno.EEXIST
    assert (tmp_path / "taken").is_dir()


def test_replace_fresh_lands_whole_or_not_at_all(tmp_path):
    from bpp.utils.fresh_file import replace_fresh

    target = tmp_path / "report.json"
    target.write_text("old")
    with pytest.raises(RuntimeError), replace_fresh(str(target), "w") as fh:
        fh.write("half")
        raise RuntimeError("disk went away")
    assert target.read_text() == "old" and not (tmp_path / "report.json.part").exists()
    with replace_fresh(str(target), "w", part=str(tmp_path / "mine.tmp")) as fh:
        fh.write("new")
    assert target.read_text() == "new" and not (tmp_path / "mine.tmp").exists()


def _tag() -> str:
    return f"{os.getpid()}.{threading.get_ident()}"


def test_the_raw_converter_and_thumbnail_writer_never_follow_a_planted_temp(tmp_path, monkeypatch):
    from bpp.output import gallery
    from bpp.utils import raw

    victim = tmp_path / "unrelated.bin"
    victim.write_bytes(b"keep")

    out = tmp_path / "converted.jpg"
    (tmp_path / f"converted.jpg.{_tag()}.tmp").symlink_to(victim)
    monkeypatch.setattr(raw, "decode_raw_rgb", lambda _p: np.zeros((8, 8, 3), np.uint8))
    assert raw.convert_raw_to_jpeg(str(tmp_path / "x.dng"), str(out)) == str(out)
    assert victim.read_bytes() == b"keep" and not out.is_symlink()

    src = tmp_path / "src.jpg"
    Image.new("RGB", (16, 16), "red").save(src)
    thumb = tmp_path / "thumb.jpg"
    (tmp_path / f"thumb.jpg.{_tag()}.tmp").symlink_to(victim)
    assert gallery._make_thumbnail(str(src), str(thumb))
    assert victim.read_bytes() == b"keep" and not thumb.is_symlink()


def test_the_cached_image_and_video_sprite_writers_never_follow_a_planted_temp(
    tmp_path, monkeypatch
):
    from bpp.utils import video
    from bpp.web.photo_edits import _generate_cached_image

    victim = tmp_path / "unrelated.bin"
    victim.write_bytes(b"keep")
    src = tmp_path / "src.jpg"
    Image.new("RGB", (16, 16), "red").save(src)

    cached = tmp_path / "abc_edit.jpg"
    (tmp_path / f"abc_edit.jpg.{_tag()}.tmp").symlink_to(victim)
    assert _generate_cached_image(str(src), str(cached))
    assert victim.read_bytes() == b"keep" and not cached.is_symlink()

    class _Cap:
        def __init__(self, *_a, **_k):
            self.n = 0

        def isOpened(self):
            return True

        def get(self, _prop):
            return 10.0

        def set(self, *_a):
            return True

        def read(self):
            self.n += 1
            return (self.n <= 2, np.zeros((8, 8, 3), np.uint8))

        def release(self):
            pass

    monkeypatch.setattr(video, "open_capture", lambda _p: _Cap(), raising=False)
    monkeypatch.setattr(video.cv2, "VideoCapture", _Cap)
    sprite = tmp_path / "sprite.jpg"
    (tmp_path / "sprite.jpg.part").symlink_to(victim)
    (tmp_path / f"sprite.jpg.{_tag()}.tmp").symlink_to(victim)
    if video.generate_video_sprite(str(src), str(sprite)):
        assert not sprite.is_symlink()
    assert victim.read_bytes() == b"keep"

"""RAW photos go through the RAW decoder in every reader, not just scoring.

Near-duplicate hashing, the face and pet crop generators and the face
overlay dimensions used to open files with PIL / cv2 directly, so a
camera RAW photo got no hash (never grouped with its JPEG twin), no
crops, and (0, 0) overlay dims. Each now routes a RAW path through
``bpp.utils.raw`` and fails quietly when RAW support is missing (that
is warned about once, by the decoder).
"""

from __future__ import annotations

import logging
import os
import pathlib
import sys
import types

import numpy as np
import pytest
from PIL import Image

from bpp.dedupe import phash
from bpp.utils import raw
from bpp.web import box_crop, bp_faces_photo, bp_pets, face_crop


def _rgb(w: int = 400, h: int = 300) -> np.ndarray:
    """A wxh RGB image: grey, with a red block at (100, 50)-(200, 150)."""
    img = np.full((h, w, 3), 128, dtype=np.uint8)
    img[50:150, 100:200] = (255, 0, 0)
    return img


# ── near-duplicate hashing ───────────────────────────────────────────


def test_hashing_decodes_a_raw_photo_at_half_size(monkeypatch):
    seen = {}

    def fake_decode(path, *, half_size=False):
        seen["half_size"] = half_size
        return _rgb()

    monkeypatch.setattr(raw, "decode_raw_rgb", fake_decode)
    dhash, ahash = phash.compute_hashes_from_file("/photos/a.CR2")
    assert seen["half_size"] is True, "an 8x8 hash doesn't need the full decode"
    gray = np.array(Image.fromarray(_rgb()).convert("L"))
    assert (dhash, ahash) == (phash.compute_dhash(gray), phash.compute_ahash(gray))


def test_hashing_without_raw_support_is_quiet(monkeypatch, caplog):
    def fake_decode(path, *, half_size=False):
        raise raw.RawSupportMissing(path)

    monkeypatch.setattr(raw, "decode_raw_rgb", fake_decode)
    with caplog.at_level(logging.WARNING, logger="bpp.dedupe.phash"):
        assert phash.compute_hashes_from_file("/photos/a.NEF") == (None, None)
        assert phash.compute_dhash_from_file("/photos/a.NEF") is None
    assert caplog.records == [], "the decoder already warned once; no per-file noise"


# ── face and pet crops ───────────────────────────────────────────────


def _crop_pixels(path: str) -> np.ndarray:
    with Image.open(path) as im:
        return np.array(im.convert("RGB"))


def test_face_and_pet_crops_come_from_the_raw_decoder(tmp_path, monkeypatch):
    monkeypatch.setattr(raw, "decode_raw_rgb", lambda p, **kw: _rgb())
    crop_dir = str(tmp_path / "crops")
    os.makedirs(crop_dir)
    # The box is stored in the 200-pixel extraction space; the 400-wide
    # decode scales it x2 back onto the red block.
    bbox = (50, 25, 50, 50)

    face = face_crop.generate_face_crop("/photos/a.NEF", bbox, crop_dir, "h", 0, max_long_side=200)
    pet = bp_pets._generate_pet_crop("/photos/a.NEF", bbox, crop_dir, "h", 0, max_long_side=200)

    assert face is not None and pet is not None
    face_px = _crop_pixels(face)
    mid = face_px[face_px.shape[0] // 2, face_px.shape[1] // 2]
    assert mid[0] > 200 and mid[1] < 60, f"crop centre should be the red block, got {mid}"


def test_crops_without_raw_support_say_so_without_a_traceback(tmp_path, monkeypatch, caplog):
    def fake_decode(path, **kw):
        raise raw.RawSupportMissing(path)

    monkeypatch.setattr(raw, "decode_raw_rgb", fake_decode)
    crop_dir = str(tmp_path / "crops")
    os.makedirs(crop_dir)
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(box_crop.RawUnreadable):
            face_crop.generate_face_crop("/photos/a.ARW", (0, 0, 10, 10), crop_dir, "h", 0)
        with pytest.raises(box_crop.RawUnreadable):
            bp_pets._generate_pet_crop("/photos/a.ARW", (0, 0, 10, 10), crop_dir, "h", 0)
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert not [r for r in caplog.records if r.exc_info]


def test_jpeg_crops_still_apply_exif_orientation(tmp_path):
    """A sideways phone photo: the crop box is in display space."""
    img = Image.fromarray(_rgb())
    exif = img.getexif()
    exif[0x0112] = 6  # rotate 90 deg CW to display
    path = str(tmp_path / "p.jpg")
    img.save(path, "JPEG", exif=exif.tobytes(), quality=95)
    crop_dir = str(tmp_path / "crops")
    os.makedirs(crop_dir)
    # Displayed image is 300x400; the red block lands at x 150-250, y 100-200.
    out = face_crop.generate_face_crop(
        path, (150, 100, 100, 100), crop_dir, "h", 0, max_long_side=400
    )
    assert out is not None
    px = _crop_pixels(out)
    mid = px[px.shape[0] // 2, px.shape[1] // 2]
    assert mid[0] > 200 and mid[1] < 60, f"expected the red block, got {mid}"


def test_both_crop_generators_share_one_box_crop(monkeypatch):
    calls = []
    monkeypatch.setattr(
        box_crop, "generate_box_crop", lambda *a, **kw: calls.append(kw.get("label")) or "x"
    )
    assert face_crop.generate_face_crop("/p.jpg", (0, 0, 1, 1), "/crops", "h", 0) == "x"
    assert bp_pets._generate_pet_crop("/p.jpg", (0, 0, 1, 1), "/crops", "h", 0) == "x"
    assert calls == ["face_crop", "pet_crop"]


# ── overlay dimensions ───────────────────────────────────────────────


def test_overlay_dims_for_a_raw_photo_come_from_the_raw_header(monkeypatch):
    monkeypatch.setattr(raw, "raw_dimensions", lambda p: (6000, 4000))
    assert bp_faces_photo._oriented_dims("/photos/a.RAF") == (6000, 4000)


def test_overlay_dims_without_raw_support_are_zero_and_quiet(monkeypatch, caplog):
    def fake_dims(path):
        raise raw.RawSupportMissing(path)

    monkeypatch.setattr(raw, "raw_dimensions", fake_dims)
    with caplog.at_level(logging.WARNING, logger="bpp.web.bp_faces_photo"):
        assert bp_faces_photo._oriented_dims("/photos/a.RAF") == (0, 0)
    assert caplog.records == []


@pytest.mark.parametrize(
    ("flip", "expected"),
    [
        (0, (600, 400)),
        (3, (600, 400)),
        (4, (400, 600)),
        (5, (400, 600)),
        (6, (400, 600)),
        (7, (400, 600)),
    ],
)
def test_raw_dimensions_follow_the_camera_flip(monkeypatch, flip, expected):
    opened = []

    class _RawPy:
        sizes = types.SimpleNamespace(width=600, height=400, flip=flip)

        def open_file(self, path):
            opened.append(path)

        def unpack(self):
            raise AssertionError("the size read must not unpack the frame")

        def close(self):
            opened.append("closed")

    monkeypatch.setitem(sys.modules, "rawpy", types.SimpleNamespace(RawPy=_RawPy))
    monkeypatch.setattr(raw, "RAWPY_AVAILABLE", True)
    assert raw.raw_dimensions("/photos/a.CR3") == expected
    assert opened == ["/photos/a.CR3", "closed"]


def test_raw_dimensions_without_raw_support_raise(monkeypatch):
    monkeypatch.setattr(raw, "RAWPY_AVAILABLE", False)
    with pytest.raises(raw.RawSupportMissing):
        raw.raw_dimensions("/photos/a.CR3")


# ── one conversion per RAW photo ─────────────────────────────────────


def test_crops_of_one_raw_photo_share_one_conversion(tmp_path, monkeypatch):
    decodes = []

    def fake_decode(path, **kw):
        decodes.append(path)
        return _rgb()

    monkeypatch.setattr(raw, "decode_raw_rgb", fake_decode)
    nef = str(tmp_path / "a.NEF")
    pathlib.Path(nef).write_bytes(b"raw")
    full = str(tmp_path / "h_full.jpg")
    crop_dir = str(tmp_path / "crops")
    os.makedirs(crop_dir)
    a = face_crop.generate_face_crop(
        nef, (50, 25, 50, 50), crop_dir, "h", 0, max_long_side=200, raw_full_jpeg=full
    )
    b = bp_pets._generate_pet_crop(
        nef, (0, 0, 40, 40), crop_dir, "h", 1, max_long_side=200, raw_full_jpeg=full
    )
    assert a is not None and b is not None
    assert decodes == [nef], "the second crop reads the cached conversion"
    assert os.path.exists(full)
    px = _crop_pixels(a)
    mid = px[px.shape[0] // 2, px.shape[1] // 2]
    assert mid[0] > 200 and mid[1] < 60, f"crop from the conversion is still the red block: {mid}"


def test_a_failed_conversion_is_reported_as_unreadable_without_a_traceback(
    tmp_path, monkeypatch, caplog
):
    monkeypatch.setattr(raw, "RAWPY_AVAILABLE", True)
    monkeypatch.setattr(raw, "ensure_raw_jpeg", lambda p, j: None)
    with caplog.at_level(logging.DEBUG), pytest.raises(box_crop.RawUnreadable):
        face_crop.generate_face_crop(
            "/photos/a.NEF",
            (0, 0, 10, 10),
            str(tmp_path),
            "h",
            0,
            raw_full_jpeg=str(tmp_path / "f.jpg"),
        )
    assert not [r for r in caplog.records if r.exc_info]


def test_a_box_outside_a_readable_raw_is_a_crop_failure_not_a_raw_failure(tmp_path, monkeypatch):
    """The endpoint must not blame the RAW conversion for a bad box."""
    monkeypatch.setattr(raw, "decode_raw_rgb", lambda p, **kw: _rgb())
    out = face_crop.generate_face_crop("/photos/a.NEF", (900, 900, 10, 10), str(tmp_path), "h", 0)
    assert out is None


def test_ensure_raw_jpeg_reuses_a_fresh_conversion_and_redoes_a_stale_one(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(raw, "convert_raw_to_jpeg", lambda p, j, **kw: calls.append(p) or j)
    raw_path = tmp_path / "a.ARW"
    jpeg = tmp_path / "a_full.jpg"
    raw_path.write_bytes(b"raw")
    os.utime(raw_path, (1_000_000_000, 1_000_000_000))
    assert raw.ensure_raw_jpeg(str(raw_path), str(jpeg)) == str(jpeg)
    assert calls == [str(raw_path)], "no conversion on disk yet"
    jpeg.write_bytes(b"jpg")
    os.utime(jpeg, (1_050_000_000, 1_050_000_000))
    assert raw.ensure_raw_jpeg(str(raw_path), str(jpeg)) == str(jpeg)
    assert len(calls) == 1, "a conversion newer than the RAW file is reused"
    os.utime(raw_path, (1_100_000_000, 1_100_000_000))
    raw.ensure_raw_jpeg(str(raw_path), str(jpeg))
    assert len(calls) == 2, "a RAW file edited after the conversion is converted again"


def test_a_drive_hiccup_on_the_raw_file_still_serves_the_conversion(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(raw, "convert_raw_to_jpeg", lambda p, j, **kw: calls.append(p) or j)
    jpeg = tmp_path / "a_full.jpg"
    jpeg.write_bytes(b"jpg")
    # The RAW can't be stat'ed right now (stale handle, drive gone):
    # the conversion on disk is served, not thrown away.
    assert raw.ensure_raw_jpeg(str(tmp_path / "gone.ARW"), str(jpeg)) == str(jpeg)
    assert calls == []


def test_concurrent_requests_convert_a_raw_photo_once(tmp_path, monkeypatch):
    import threading
    import time

    calls = []

    def slow_convert(p, j, **kw):
        calls.append(p)
        time.sleep(0.05)
        pathlib.Path(j).write_bytes(b"jpg")
        return j

    monkeypatch.setattr(raw, "convert_raw_to_jpeg", slow_convert)
    raw_path = tmp_path / "a.CR2"
    raw_path.write_bytes(b"raw")
    jpeg = str(tmp_path / "a_full.jpg")
    results = []
    start = threading.Barrier(6)

    def one():
        start.wait()
        results.append(raw.ensure_raw_jpeg(str(raw_path), jpeg))

    threads = [threading.Thread(target=one) for _ in range(6)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert results == [jpeg] * 6
    assert len(calls) == 1, "the other five waited for the first conversion"


def test_a_damaged_raw_is_not_decoded_again_until_it_changes(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(raw, "convert_raw_to_jpeg", lambda p, j, **kw: calls.append(p))
    monkeypatch.setattr(raw, "_failed_conversions", {})
    raw_path = tmp_path / "bad.NEF"
    raw_path.write_bytes(b"truncated")
    jpeg = str(tmp_path / "bad_full.jpg")
    assert raw.ensure_raw_jpeg(str(raw_path), jpeg) is None
    assert raw.ensure_raw_jpeg(str(raw_path), jpeg) is None
    assert len(calls) == 1, "the second request didn't decode the same damaged file"
    raw_path.write_bytes(b"truncated, then fixed")
    assert raw.ensure_raw_jpeg(str(raw_path), jpeg) is None
    assert len(calls) == 2, "a changed file gets another try"
    # The failure may have been the environment's (disk full, drive
    # gone): after a while the same file is tried again.
    monkeypatch.setattr(raw, "FAILED_CONVERSION_RETRY_SECONDS", 0.0)
    assert raw.ensure_raw_jpeg(str(raw_path), jpeg) is None
    assert len(calls) == 3


def test_a_raw_dated_in_the_future_counts_as_converted(tmp_path, monkeypatch):
    calls = []

    def convert(p, j, **kw):
        calls.append(p)
        pathlib.Path(j).write_bytes(b"jpg")
        return j

    monkeypatch.setattr(raw, "convert_raw_to_jpeg", convert)
    raw_path = tmp_path / "ahead.CR2"
    raw_path.write_bytes(b"raw")
    os.utime(raw_path, (4_000_000_000, 4_000_000_000))  # camera clock far ahead
    jpeg = str(tmp_path / "ahead_full.jpg")
    assert raw.ensure_raw_jpeg(str(raw_path), jpeg) == jpeg
    assert raw.ensure_raw_jpeg(str(raw_path), jpeg) == jpeg
    assert len(calls) == 1, "not converted again on every request"
    # Replaced by another future-dated file (same camera, same name):
    # the size changed, so the old conversion is stale.
    raw_path.write_bytes(b"raw, but a different frame")
    os.utime(raw_path, (4_000_000_000, 4_000_000_000))
    assert raw.ensure_raw_jpeg(str(raw_path), jpeg) == jpeg
    assert len(calls) == 2
    # Rewritten later with a sane date: the old conversion is stale.
    os.utime(raw_path, None)
    os.utime(jpeg, (1_000_000_000, 1_000_000_000))
    assert raw.ensure_raw_jpeg(str(raw_path), jpeg) == jpeg
    assert len(calls) == 3


def test_a_direct_raw_decode_without_support_is_unreadable(monkeypatch):
    def fake_decode(path, **kw):
        raise raw.RawSupportMissing(path)

    monkeypatch.setattr(raw, "decode_raw_rgb", fake_decode)
    with pytest.raises(box_crop.RawUnreadable), box_crop.open_oriented("/p/a.CR2", label="t"):
        pass


def test_raw_handle_is_closed_when_open_fails(monkeypatch):
    closed = []

    class _RawPy:
        def open_file(self, path):
            raise OSError("drive gone")

        def close(self):
            closed.append(True)

    monkeypatch.setitem(sys.modules, "rawpy", types.SimpleNamespace(RawPy=_RawPy))
    monkeypatch.setattr(raw, "RAWPY_AVAILABLE", True)
    with pytest.raises(OSError):
        raw._read_sizes("/photos/a.CR3")
    assert closed == [True]


# ── the crop endpoints' failure toast ────────────────────────────────


def test_a_raw_crop_failure_names_the_reason_and_the_fix(monkeypatch):
    from bpp.web import media_support

    monkeypatch.setattr(raw, "RAWPY_AVAILABLE", False)
    err = media_support.raw_preview_error("h", "/photos/a.NEF", face_index=2)
    reason, recovery = raw.preview_failure()
    assert err.user_message == reason
    assert err.context["recovery"] == recovery
    assert err.context["face_index"] == 2


def test_a_jpeg_crop_failure_still_says_what_and_what_to_do():
    from bpp.web import media_support

    err = media_support.crop_failure_error("h", "diag", detection_index=1)
    assert "crop" in err.user_message
    assert err.context["recovery"] == raw.CHECK_FILE_RECOVERY
    assert err.context["detection_index"] == 1
    assert err.user_message != "Failed to generate crop"

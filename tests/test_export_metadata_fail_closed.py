"""An export that was asked to remove metadata never ships it quietly.

The copy export strips EXIF by re-encoding JPEG/PNG/WebP/TIFF. When the
re-encode failed it copied the original bytes, metadata and all, with
only a log line; HEIC, camera RAW and video files were copied with their
metadata every time. Now HEIC and RAW are converted to JPEG for the
strip, a photo whose metadata can't be removed fails the export, and a
file that keeps its metadata by nature (a video, anything in a link
mode) is counted in ``metadata_kept`` so the result says so.
"""

from __future__ import annotations

import errno
import os

import pytest
from PIL import Image

from bpp.output import export_modes
from bpp.output.export import export_selected


def _jpeg(path):
    Image.new("RGB", (8, 8), "red").save(path, "JPEG")
    return str(path)


def _export(items, tmp_path, **kw):
    return export_selected(items, items, str(tmp_path / "out"), mode="copy", gallery=False, **kw)


def test_a_raw_is_converted_to_a_metadata_free_jpeg(tmp_path, monkeypatch):
    raw = tmp_path / "shot.CR2"
    raw.write_bytes(b"raw bytes")

    def fake_convert(src, out, quality=92):
        Image.new("RGB", (4, 4), "blue").save(out, "JPEG", quality=quality)
        return out

    monkeypatch.setattr("bpp.utils.raw.RAWPY_AVAILABLE", True)
    monkeypatch.setattr("bpp.utils.raw.convert_raw_to_jpeg", fake_convert)
    result = _export([{"filepath": str(raw), "aggregate_score": 0.9}], tmp_path)
    assert (result.exported, result.failed, result.metadata_kept) == (1, 0, 0)
    assert (tmp_path / "out" / "selected" / "001_shot.jpg").exists()
    assert not (tmp_path / "out" / "selected" / "001_shot.CR2").exists()


def test_a_raw_without_a_decoder_fails_instead_of_shipping_its_metadata(tmp_path, monkeypatch):
    raw = tmp_path / "shot.CR2"
    raw.write_bytes(b"raw bytes")
    monkeypatch.setattr("bpp.utils.raw.RAWPY_AVAILABLE", False)
    result = _export([{"filepath": str(raw), "aggregate_score": 0.9}], tmp_path)
    assert (result.exported, result.failed) == (0, 1)
    assert not (tmp_path / "out" / "selected" / "001_shot.CR2").exists()
    assert "RAW support" in result.failures[0]["error"]


def test_a_failed_re_encode_fails_the_photo_and_keeps_an_earlier_export(tmp_path, monkeypatch):
    src = _jpeg(tmp_path / "private.jpg")
    out = tmp_path / "out" / "selected"
    out.mkdir(parents=True)
    (out / "001_private.jpg").write_bytes(b"from the earlier export")

    def broken_strip(_src, _dest, _quality):
        raise OSError("encoder exploded")

    monkeypatch.setattr(export_modes, "_strip_and_save", broken_strip)
    result = _export([{"filepath": src, "aggregate_score": 0.9}], tmp_path)
    assert (result.exported, result.failed) == (0, 1)
    assert (out / "001_private.jpg").read_bytes() == b"from the earlier export"
    assert [f for f in os.listdir(out) if f.endswith(".part")] == []


def test_a_full_disk_during_the_re_encode_still_aborts_the_export(tmp_path, monkeypatch):
    srcs = [_jpeg(tmp_path / f"p{i}.jpg") for i in range(3)]

    def disk_full(_src, _dest, _quality):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(export_modes, "_strip_and_save", disk_full)
    result = _export([{"filepath": s, "aggregate_score": 0.9} for s in srcs], tmp_path)
    assert result.disk_error is not None and result.disk_error["category"] == "no_space"
    assert result.failed == 1, "the loop stops at the first write every later one repeats"


def test_stripped_files_get_the_usual_permissions(tmp_path):
    import stat

    old = os.umask(0o022)
    try:
        src = _jpeg(tmp_path / "a.jpg")
        _export([{"filepath": src, "aggregate_score": 0.9}], tmp_path)
    finally:
        os.umask(old)
    mode = stat.S_IMODE(os.stat(tmp_path / "out" / "selected" / "001_a.jpg").st_mode)
    assert mode == 0o644, f"an export must be readable like any file, got {oct(mode)}"


def test_a_raw_that_cannot_be_decoded_says_so(tmp_path, monkeypatch):
    raw = tmp_path / "shot.CR2"
    raw.write_bytes(b"raw bytes")
    monkeypatch.setattr("bpp.utils.raw.RAWPY_AVAILABLE", True)
    monkeypatch.setattr("bpp.utils.raw.convert_raw_to_jpeg", lambda *a, **k: None)
    result = _export([{"filepath": str(raw), "aggregate_score": 0.9}], tmp_path)
    assert result.failed == 1
    assert "couldn't be decoded" in result.failures[0]["error"]
    assert "RAW support" not in result.failures[0]["error"]


def test_a_multi_picture_jpeg_is_saved_as_a_plain_jpeg_at_the_chosen_quality(tmp_path, monkeypatch):
    from PIL import Image as PILImage

    src = _jpeg(tmp_path / "portrait.jpg")
    saved = {}
    real_open = PILImage.open

    class _Mpo:
        def __init__(self, img):
            self._img = img
            self.format = "MPO"
            self.mode = img.mode

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self._img.close()

        def __getattr__(self, name):
            return getattr(self._img, name)

        def save(self, out, format=None, **kw):
            saved["format"], saved["quality"] = format, kw.get("quality")
            self._img.save(out, format=format, **kw)

    monkeypatch.setattr(export_modes, "_strip_and_save", export_modes._strip_and_save)
    monkeypatch.setattr(
        PILImage,
        "open",
        lambda p, *a, **k: _Mpo(real_open(p, *a, **k)) if str(p) == src else real_open(p, *a, **k),
    )
    monkeypatch.setattr("PIL.ImageOps.exif_transpose", lambda img: img)
    result = _export([{"filepath": src, "aggregate_score": 0.9}], tmp_path, quality=77)
    assert result.exported == 1
    assert saved == {"format": "JPEG", "quality": 77}


def test_a_video_in_a_resize_export_is_counted_too(tmp_path):
    vid = tmp_path / "clip.mp4"
    vid.write_bytes(b"\x00" * 16)
    item = {"filepath": str(vid), "aggregate_score": 0.9, "is_video": True}
    result = _export([item], tmp_path, max_size=800)
    assert (result.exported, result.metadata_kept) == (1, 1)


def test_a_video_is_exported_and_counted_as_keeping_its_metadata(tmp_path):
    vid = tmp_path / "clip.mp4"
    vid.write_bytes(b"\x00" * 16)
    photo = _jpeg(tmp_path / "a.jpg")
    items = [
        {"filepath": str(vid), "aggregate_score": 0.9, "is_video": True},
        {"filepath": photo, "aggregate_score": 0.8},
    ]
    result = _export(items, tmp_path)
    assert (result.exported, result.failed, result.metadata_kept) == (2, 0, 1)


@pytest.mark.parametrize("mode", ["hardlink", "symlink"])
def test_a_link_mode_counts_every_file_as_keeping_its_metadata(tmp_path, mode):
    photo = _jpeg(tmp_path / "a.jpg")
    item = {"filepath": photo, "aggregate_score": 0.8}
    result = export_selected([item], [item], str(tmp_path / "out"), mode=mode, gallery=False)
    assert (result.exported, result.metadata_kept) == (1, 1)


def test_with_stripping_off_everything_is_copied_and_nothing_is_counted(tmp_path):
    raw = tmp_path / "shot.CR2"
    raw.write_bytes(b"raw bytes")
    result = _export(
        [{"filepath": str(raw), "aggregate_score": 0.9}], tmp_path, strip_metadata=False
    )
    assert (result.exported, result.metadata_kept) == (1, 0)


def test_the_export_endpoints_report_metadata_kept(tmp_path):
    from bpp.db.photos import upsert_photo
    from bpp.web.app import create_app

    lib = str(tmp_path / "lib")
    os.makedirs(os.path.join(lib, "photos"))
    app = create_app(library_path=lib)
    app.config["TESTING"] = True
    ctx = app.extensions["bpp"]
    vid = os.path.join(lib, "photos", "clip.mp4")
    with open(vid, "wb") as f:
        f.write(b"\x00" * 16)
    with app.app_context():
        conn = ctx.get_conn()
        upsert_photo(conn, {"filepath": vid, "date": "2024-01-01T00:00:00", "is_video": 1})
        conn.commit()
        ctx.invalidate_analysis()
    with app.test_client() as c:
        r = c.post(
            "/api/v1/export",
            json={"outdir": os.path.join(lib, "export"), "selected_paths": [vid], "mode": "copy"},
        )
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["metadata_kept"] == 1

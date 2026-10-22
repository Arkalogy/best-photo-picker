"""An export never opens an existing destination for writing: an earlier
hardlink export made that name a second name of the library original,
and truncating it would destroy the original's pixels."""

from __future__ import annotations

import os

from PIL import Image


def _original(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    src = lib / "photo.jpg"
    Image.new("RGB", (400, 300), "red").save(src, "JPEG")
    return str(src)


def _export(src, out, **kw):
    from bpp.output.export import export_selected

    item = {"filepath": src, "aggregate_score": 1.0}
    return export_selected([item], [item], str(out), **kw)


def test_a_resize_export_over_a_hardlink_export_keeps_the_original(tmp_path):
    src = _original(tmp_path)
    out = tmp_path / "out"
    _export(src, out, mode="hardlink")
    (dest,) = [p for p in (out / "selected").iterdir() if p.suffix == ".jpg"]
    assert os.path.samefile(dest, src)
    _export(src, out, mode="copy", max_size=100)
    with Image.open(src) as im:
        assert im.size == (400, 300), "the library original was resized through its hard link"
    with Image.open(dest) as im:
        assert max(im.size) == 100


def test_a_copy_export_over_a_hardlink_export_keeps_the_original(tmp_path):
    src = _original(tmp_path)
    other = tmp_path / "lib" / "other.jpg"
    Image.new("RGB", (50, 50), "blue").save(other, "JPEG")
    out = tmp_path / "out"
    _export(src, out, mode="hardlink")
    # A different photo lands at the same numbered name in copy mode.
    from bpp.output.export import export_selected

    item = {"filepath": str(other), "aggregate_score": 1.0}
    export_selected([item], [item], str(out), mode="copy")
    with Image.open(src) as im:
        assert im.size == (400, 300), "the library original was overwritten through its hard link"
    with Image.open(out / "selected" / "001_other.jpg") as im:
        assert im.size == (50, 50)


def test_a_jpeg_conversion_over_a_hardlink_export_keeps_the_original(tmp_path):
    src = _original(tmp_path)
    out = tmp_path / "out"
    _export(src, out, mode="hardlink")
    _export(src, out, mode="copy", fmt="jpeg", quality=30)
    with Image.open(src) as im:
        assert im.size == (400, 300)
    assert os.path.getsize(src) > 1000

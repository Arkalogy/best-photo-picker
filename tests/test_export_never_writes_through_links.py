"""An export never opens an existing destination for writing: an earlier
hardlink export made that name a second name of the library original,
and truncating it would destroy the original's pixels."""

from __future__ import annotations

import os

import pytest
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


@pytest.mark.parametrize("mode", ["hardlink", "symlink", "copy"])
def test_exporting_a_photo_onto_itself_is_refused_and_keeps_the_file(tmp_path, mode):
    from bpp.output import export_modes as em

    src = tmp_path / "a.jpg"
    src.write_bytes(b"original")
    handler = {
        "hardlink": em._export_hardlink,
        "symlink": em._export_symlink,
        "copy": em._export_copy,
    }[mode]
    with pytest.raises(OSError):
        handler(str(src), str(src))
    assert src.read_bytes() == b"original"
    # A second hard link at the destination is a different entry: replaced, original kept.
    twin = tmp_path / "twin.jpg"
    os.link(src, twin)
    em._export_hardlink(str(src), str(twin))
    assert src.read_bytes() == b"original" and twin.read_bytes() == b"original"
    # So is a symlink left by an earlier symlink-mode export.
    link = tmp_path / "link.jpg"
    os.symlink(src, link)
    em._export_copy(str(src), str(link))
    assert not os.path.islink(link) and link.read_bytes() == b"original"
    assert src.read_bytes() == b"original"


def test_a_folder_that_received_a_symlink_export_takes_the_next_export(tmp_path):
    from bpp.utils.paths import safe_join

    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "a.jpg").write_bytes(b"x")
    out = tmp_path / "out" / "selected"
    out.mkdir(parents=True)
    os.symlink(lib / "a.jpg", out / "001_a.jpg")
    assert safe_join(str(out), "001_a.jpg") == str(out / "001_a.jpg")
    with pytest.raises(ValueError):
        safe_join(str(out), "..")

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


def _published(out):
    return sorted(p for p in (out / "selected").iterdir() if not p.name.startswith("."))


@pytest.mark.parametrize(
    "kw, planted",
    [
        ({}, ".001_photo.jpg.part"),  # copy, metadata stripped
        ({"strip_metadata": False}, ".001_photo.jpg.part"),  # the bytes copied
        ({"max_size": 8}, ".001_photo.jpg.part"),  # resized
        ({"fmt": "png"}, ".001_photo.png.part"),  # converted
    ],
)
def test_a_link_planted_at_the_export_temp_name_is_not_written_through(tmp_path, kw, planted):
    src = _original(tmp_path)
    victim = tmp_path / "lib" / "unrelated.jpg"
    Image.new("RGB", (20, 20), "blue").save(victim, "JPEG")
    before = victim.read_bytes()
    out = tmp_path / "out"
    (out / "selected").mkdir(parents=True)
    (out / "selected" / planted).symlink_to(victim)

    result = _export(src, out, **kw)

    assert result.exported == 1 and result.failed == 0
    assert victim.read_bytes() == before, "the export wrote through the planted link"
    (published,) = _published(out)
    assert not published.is_symlink()
    with Image.open(published) as im:
        assert max(im.size) <= 400
    assert not (out / "selected" / planted).exists(), "the temp entry was left behind"


@pytest.mark.parametrize(
    "planted, kw",
    [
        ("manifest.json", {"write_manifest": True}),
        ("manifest.json.part", {"write_manifest": True}),
        ("report.json", {}),
        ("report.json.part", {}),
        ("report.csv", {}),
        ("gallery.html", {"gallery": True}),
        ("thumbnails/001_photo.jpg", {"gallery": True}),
        ("selected/001_photo.xmp", {"write_xmp": True}),
        ("best-photos.zip", {"mode": "zip"}),
    ],
)
def test_a_link_planted_at_a_sidecar_name_is_replaced_not_followed(tmp_path, planted, kw):
    src = _original(tmp_path)
    victim = tmp_path / "lib" / "notes.txt"
    victim.write_bytes(b"the owner's own file")
    out = tmp_path / "out"
    link = out / planted
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(victim)

    result = _export(src, out, **kw)

    assert result.exported == 1
    assert victim.read_bytes() == b"the owner's own file", "a sidecar was written through a link"
    if planted.endswith(".part"):
        assert not link.exists()
    else:
        assert link.exists() and not link.is_symlink()


def test_a_folder_at_the_temp_name_fails_that_photo_only(tmp_path):
    src = _original(tmp_path)
    other = tmp_path / "lib" / "other.jpg"
    Image.new("RGB", (50, 50), "blue").save(other, "JPEG")
    out = tmp_path / "out"
    (out / "selected" / ".001_photo.jpg.part").mkdir(parents=True)
    from bpp.output.export import export_selected

    items = [{"filepath": p, "aggregate_score": 1.0} for p in (src, str(other))]
    result = export_selected(items, items, str(out))
    assert result.exported == 1 and result.failed == 1 and result.disk_error is None


def test_a_copy_export_keeps_the_source_timestamp(tmp_path):
    src = _original(tmp_path)
    os.utime(src, (1_000_000_000, 1_000_000_000))
    out = tmp_path / "out"
    _export(src, out, strip_metadata=False)
    (published,) = _published(out)
    assert int(published.stat().st_mtime) == 1_000_000_000

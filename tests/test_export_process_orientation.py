"""A converted or resized export is upright and keeps its EXIF unless
metadata removal was asked for."""

from __future__ import annotations

from PIL import Image

from bpp.output.export_modes import _process_image


def _portrait_jpeg(tmp_path):
    p = str(tmp_path / "p.jpg")
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90° to display
    exif[0x010F] = "Camera Co"
    Image.new("RGB", (400, 300), "white").save(p, "JPEG", exif=exif.tobytes())
    return p


def test_converted_export_is_upright_and_keeps_exif(tmp_path):
    src = _portrait_jpeg(tmp_path)
    dest = _process_image(src, str(tmp_path / "out.jpg"), "jpeg", None, 85, keep_exif=True)
    with Image.open(dest) as out:
        assert out.size == (300, 400)
        exif = out.getexif()
        assert exif.get(0x010F) == "Camera Co"
        assert exif.get(0x0112, 1) == 1, "orientation applied, not re-declared"


def test_resized_export_strips_exif_when_asked(tmp_path):
    src = _portrait_jpeg(tmp_path)
    dest = _process_image(src, str(tmp_path / "out.jpg"), "original", 200, 85, keep_exif=False)
    with Image.open(dest) as out:
        assert out.size == (150, 200)
        assert not out.getexif()


def test_write_one_passes_the_strip_choice(tmp_path):
    from bpp.output.export import _write_one, _WritePlan

    src = _portrait_jpeg(tmp_path)
    plan = _WritePlan(
        mode="copy", fmt="jpeg", max_size=None, quality=85, strip_metadata=True, process=True
    )
    dest = _write_one(src, str(tmp_path / "w.jpg"), False, plan)
    with Image.open(dest) as out:
        assert out.size == (300, 400)
        assert not out.getexif()

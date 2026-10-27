"""A camera RAW exported as JPEG, PNG or at a smaller size is decoded with
the RAW reader, like the default export already does.

Before 0.2.2 these choices opened the RAW with the plain image reader,
which can't decode it: the photo failed the export ("cannot identify
image file") and nothing was written.
"""

from __future__ import annotations

import os

import numpy as np
import pytest
from PIL import Image

from bpp.output import export_modes
from bpp.utils import raw


@pytest.fixture
def nef(tmp_path, monkeypatch):
    monkeypatch.setattr(raw, "RAWPY_AVAILABLE", True)
    monkeypatch.setattr(
        raw, "decode_raw_rgb", lambda _p, **_kw: np.full((20, 40, 3), 200, np.uint8)
    )
    path = tmp_path / "DSC_0001.NEF"
    path.write_bytes(b"not a real raw file")
    out = tmp_path / "out"
    out.mkdir()
    return str(path), out


@pytest.mark.parametrize(
    ("fmt", "max_size", "ext", "size"),
    [
        ("jpeg", None, ".jpg", (40, 20)),
        ("png", None, ".png", (40, 20)),
        ("original", 10, ".jpg", (10, 5)),
    ],
)
def test_a_raw_converts_and_resizes(nef, fmt, max_size, ext, size):
    src, out = nef
    dest = export_modes._process_image(src, str(out / "DSC_0001.NEF"), fmt, max_size, 85)
    assert dest.endswith("DSC_0001" + ext), "a resized RAW can't be written back as RAW: JPEG"
    with Image.open(dest) as img:
        assert img.size == size
    assert sorted(p.name for p in out.iterdir()) == ["DSC_0001" + ext], "no temp file left"


def test_a_raw_without_raw_support_fails_with_the_reason(nef, monkeypatch):
    src, out = nef
    monkeypatch.setattr(raw, "RAWPY_AVAILABLE", False)
    with pytest.raises(OSError, match="RAW support isn't installed"):
        export_modes._process_image(src, str(out / "DSC_0001.NEF"), "jpeg", None, 85)
    assert list(out.iterdir()) == []


def test_a_raw_keeps_its_date_camera_and_gps_when_asked(tmp_path, monkeypatch):
    """A camera RAW's own EXIF (a TIFF-style header Pillow reads) goes along
    when metadata is kept, and stays out when it isn't."""
    monkeypatch.setattr(raw, "RAWPY_AVAILABLE", True)
    monkeypatch.setattr(
        raw, "decode_raw_rgb", lambda _p, **_kw: np.full((20, 40, 3), 200, np.uint8)
    )
    exif = Image.Exif()
    exif[0x010F] = "NIKON CORPORATION"
    exif[0x0112] = 6
    exif.get_ifd(0x8769).update({0x9003: "2017:01:15 21:38:27"})
    exif.get_ifd(0x8825).update({1: "N", 2: (40.0, 0.0, 0.0)})
    src = tmp_path / "DSC_0001.NEF"
    Image.new("RGB", (16, 12)).save(src, "TIFF", exif=exif.tobytes())
    out = tmp_path / "out"
    out.mkdir()
    kept = export_modes._process_image(
        str(src), str(out / "a.NEF"), "jpeg", None, 85, keep_exif=True
    )
    with Image.open(kept) as img:
        got = img.getexif()
        assert got.get(0x010F) == "NIKON CORPORATION"
        assert got.get_ifd(0x8769).get(0x9003) == "2017:01:15 21:38:27"
        assert got.get_ifd(0x8825).get(1) == "N"
        assert got.get(0x0112, 1) == 1, "the decoder already turned the pixels"
        assert img.size == (40, 20), "the decoded pixels, not the header's preview"
    stripped = export_modes._process_image(str(src), str(out / "b.NEF"), "jpeg", None, 85)
    with Image.open(stripped) as img:
        assert not img.getexif()


def test_a_small_raw_export_decodes_at_half_size(tmp_path, monkeypatch):
    seen = {}

    def decode(_p, half_size=False):
        seen["half"] = half_size
        return np.full((20, 40, 3), 200, np.uint8)

    monkeypatch.setattr(raw, "RAWPY_AVAILABLE", True)
    monkeypatch.setattr(raw, "decode_raw_rgb", decode)
    monkeypatch.setattr(raw, "raw_dimensions", lambda _p: (6000, 4000))
    src = tmp_path / "DSC_0001.NEF"
    src.write_bytes(b"not a real raw file")
    out = tmp_path / "out"
    out.mkdir()
    export_modes._process_image(str(src), str(out / "a.NEF"), "jpeg", 1024, 85)
    assert seen["half"] is True
    export_modes._process_image(str(src), str(out / "b.NEF"), "jpeg", 4000, 85)
    assert seen["half"] is False, "half size would be smaller than asked"


def test_a_resize_in_the_original_format_uses_the_chosen_quality(tmp_path):
    src = tmp_path / "p.jpg"
    Image.effect_noise((400, 200), 60).convert("RGB").save(src, "JPEG", quality=95)
    out = tmp_path / "out"
    out.mkdir()
    hi = export_modes._process_image(str(src), str(out / "hi.jpg"), "original", 200, 95)
    lo = export_modes._process_image(str(src), str(out / "lo.jpg"), "original", 200, 40)
    assert os.path.getsize(hi) > os.path.getsize(lo)


def test_a_maker_note_too_big_for_a_jpeg_is_dropped_not_the_export(tmp_path):
    """A Nikon RAW's maker note alone is ~145 KB; a JPEG holds 64 KB of
    EXIF. The note goes, the date and camera stay, the export succeeds."""
    exif = Image.Exif()
    exif[0x010F] = "NIKON CORPORATION"
    exif.get_ifd(0x8769).update({0x9003: "2017:01:15 21:38:27", 0x927C: bytes(150_000)})
    src = tmp_path / "scan.tif"
    Image.new("RGB", (40, 20)).save(src, "TIFF", exif=exif.tobytes())
    out = tmp_path / "out"
    out.mkdir()
    dest = export_modes._process_image(
        str(src), str(out / "s.tif"), "jpeg", None, 85, keep_exif=True
    )
    with Image.open(dest) as img:
        got = img.getexif()
        assert got.get(0x010F) == "NIKON CORPORATION"
        assert got.get_ifd(0x8769).get(0x9003) == "2017:01:15 21:38:27"
        assert 0x927C not in got.get_ifd(0x8769)


def test_a_webp_keeps_large_exif(tmp_path):
    """Only a JPEG caps EXIF at 64 KB: a WebP keeps the whole block."""
    exif = Image.Exif()
    exif[0x010F] = "Camera Co"
    exif.get_ifd(0x8769).update({0x9003: "2026:10:24 10:00:00", 0x9286: b"x" * 70_000})
    src = tmp_path / "big.webp"
    Image.new("RGB", (40, 20)).save(src, "WEBP", exif=exif.tobytes())
    out = tmp_path / "out"
    out.mkdir()
    dest = export_modes._process_image(
        str(src), str(out / "b.webp"), "original", 10, 85, keep_exif=True
    )
    with Image.open(dest) as img:
        got = img.getexif()
        assert got.get(0x010F) == "Camera Co"
        assert len(got.get_ifd(0x8769).get(0x9286, b"")) == 70_000

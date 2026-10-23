"""Thumbnails decode JPEGs at a reduced DCT scale instead of the full
resolution and then shrinking. The analysis input stays a full decode:
a DCT-scaled decode keeps different high-frequency content, and the
sharpness scores of new photos must stay comparable with the ones
already in the library."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image
from PIL.JpegImagePlugin import JpegImageFile


def _jpeg(tmp_path, w=2400, h=1800, orientation=6):
    p = str(tmp_path / "big.jpg")
    exif = Image.Exif()
    exif[0x0112] = orientation
    Image.new("RGB", (w, h), "white").save(p, "JPEG", exif=exif.tobytes())
    return p


@pytest.fixture()
def decoded_sizes(monkeypatch):
    sizes: list[tuple[int, int]] = []
    real = JpegImageFile.draft

    def spy(self, mode, size):
        out = real(self, mode, size)
        sizes.append(self.size)
        return out

    monkeypatch.setattr(JpegImageFile, "draft", spy)
    return sizes


def test_thumbnail_decodes_at_reduced_scale_and_keeps_orientation(tmp_path, decoded_sizes):
    from bpp.output.gallery import _make_thumbnail

    src = _jpeg(tmp_path)
    dest = str(tmp_path / "thumb.jpg")
    assert _make_thumbnail(src, dest, size=400)
    assert Image.open(dest).size == (300, 400), "EXIF rotation applied"
    assert decoded_sizes and max(decoded_sizes[-1]) <= 1200, decoded_sizes


def test_analysis_input_keeps_the_full_decode(tmp_path, decoded_sizes):
    from bpp.scoring.aggregate import load_and_downscale

    img = load_and_downscale(_jpeg(tmp_path), 1024)
    assert isinstance(img, np.ndarray)
    assert img.shape == (1024, 768, 3)
    assert all(max(size) == 2400 for size in decoded_sizes), decoded_sizes

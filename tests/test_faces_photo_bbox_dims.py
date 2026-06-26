"""Parity test for the bbox_pct dimension fast-path.

`_oriented_dims` reads image dimensions from the header + EXIF orientation
tag without a full pixel decode (the old path called ImageOps.exif_transpose
on every /faces/photo request). Face bbox coords are in exif-transposed
space, so the fast path MUST return exactly what `exif_transpose().size`
would — otherwise every overlay on a rotated (portrait phone) photo shifts.
This pins that parity across orientations so the optimization can't silently
regress the coordinate space.
"""

from __future__ import annotations

import pytest
from PIL import Image, ImageOps

from bpp.web.bp_faces_photo import _oriented_dims


def _write_jpeg_with_orientation(path, w, h, orientation):
    """Write a wxh JPEG carrying the given EXIF Orientation tag."""
    img = Image.new("RGB", (w, h), (123, 50, 200))
    exif = img.getexif()
    exif[0x0112] = orientation  # Orientation
    img.save(path, "JPEG", exif=exif.tobytes())


@pytest.mark.parametrize(
    "orientation",
    [1, 2, 3, 4, 5, 6, 7, 8],
)
def test_oriented_dims_matches_exif_transpose(tmp_path, orientation):
    # Non-square so a wrong swap is detectable.
    raw_w, raw_h = 300, 200
    p = tmp_path / f"o{orientation}.jpg"
    _write_jpeg_with_orientation(str(p), raw_w, raw_h, orientation)

    # Ground truth: what the old full-decode path produced.
    with Image.open(str(p)) as im:
        expected = ImageOps.exif_transpose(im).size

    assert _oriented_dims(str(p)) == expected, (
        f"orientation {orientation}: fast path diverged from exif_transpose"
    )


def test_oriented_dims_missing_file_returns_zero():
    assert _oriented_dims("/nope/does/not/exist.jpg") == (0, 0)


def test_oriented_dims_no_exif_is_raw_size(tmp_path):
    p = tmp_path / "plain.png"
    Image.new("RGB", (321, 123), (0, 0, 0)).save(str(p))
    assert _oriented_dims(str(p)) == (321, 123)

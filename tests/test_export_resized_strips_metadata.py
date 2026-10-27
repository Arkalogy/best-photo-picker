"""A converted or resized export with metadata removal carries none: no
EXIF (owner, GPS), no XMP, no comment. A HEIC comes out as JPEG, as the
metadata-removal copy already made it.

Before 0.2.2 removal only left the ``exif`` argument out of the save. The
HEIF encoder then took the photo's own EXIF and XMP anyway, so a HEIC
exported at a smaller size in its original format kept the owner's name
and location while the export reported it clean (and the HEIF encoder can
crash the process once the app's modules are loaded).
"""

from __future__ import annotations

import subprocess
import sys

import pytest
from PIL import Image, ImageCms

from bpp.output import export_modes

pillow_heif = pytest.importorskip("pillow_heif")
pillow_heif.register_heif_opener()

XMP = b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF/>PrivateXmp</x:xmpmeta>'


def _private_exif():
    exif = Image.Exif()
    exif[0x013B] = "PrivateOwner"  # Artist
    exif.get_ifd(0x8825).update({1: "N", 2: (40.0, 0.0, 0.0), 3: "W", 4: (105.0, 0.0, 0.0)})
    return exif


_WRITE_HEIC = """
import sys, pillow_heif
from PIL import Image
pillow_heif.register_heif_opener()
Image.new("RGB", (400, 200), "teal").save(
    sys.argv[1], "HEIF", exif=bytes.fromhex(sys.argv[2]), xmp=bytes.fromhex(sys.argv[3])
)
"""


def _source(tmp_path, ext, fmt):
    path = tmp_path / f"src{ext}"
    if fmt == "HEIF":
        # A clean process: the HEIF encoder crashes after the app's imports.
        args = [str(path), _private_exif().tobytes().hex(), XMP.hex()]
        subprocess.run([sys.executable, "-I", "-c", _WRITE_HEIC, *args], check=True)
        return str(path)
    Image.new("RGB", (400, 200), "teal").save(
        path,
        fmt,
        exif=_private_exif().tobytes(),
        xmp=XMP,
        **({"comment": b"PrivateNote"} if fmt == "JPEG" else {}),
    )
    return str(path)


def _leaks(path):
    with Image.open(path) as img:
        exif = img.getexif()
        found = []
        if exif.get(0x013B) or exif.get_ifd(0x8825):
            found.append("exif")
        if img.info.get("xmp") or img.info.get("XML:com.adobe.xmp"):
            found.append("xmp")
        if img.info.get("comment"):
            found.append("comment")
        return found, img.size


@pytest.mark.parametrize(("ext", "fmt"), [(".heic", "HEIF"), (".jpg", "JPEG")])
@pytest.mark.parametrize("out_fmt", ["original", "jpeg", "png"])
def test_removal_leaves_no_private_metadata(tmp_path, ext, fmt, out_fmt):
    src = _source(tmp_path, ext, fmt)
    out = tmp_path / "out"
    out.mkdir()
    dest = export_modes._process_image(src, str(out / f"p{ext}"), out_fmt, 100, 85, keep_exif=False)
    if ext == ".heic" and out_fmt == "original":
        assert dest.endswith("p.jpg"), "a converted HEIC is written as JPEG"
    found, size = _leaks(dest)
    assert found == [], f"{ext} -> {out_fmt} kept {found}"
    assert size == (100, 50)


def test_keeping_metadata_still_keeps_the_exif(tmp_path):
    src = _source(tmp_path, ".heic", "HEIF")
    out = tmp_path / "out"
    out.mkdir()
    dest = export_modes._process_image(
        src, str(out / "p.heic"), "original", 100, 85, keep_exif=True
    )
    with Image.open(dest) as img:
        assert img.getexif().get(0x013B) == "PrivateOwner"


# --- What a converted export carries: the colour profile always; EXIF, XMP
# and the comment only when the user keeps metadata. Before 0.2.2 JPEG and
# WebP output lost the profile, kept XMP was lost, a TIFF's kept EXIF was
# lost, and the full-size copy and gallery thumbnails kept the comment.

ICC = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def _jpeg_source(tmp_path, name="src.jpg"):
    path = tmp_path / name
    Image.new("RGB", (400, 200), "teal").save(
        path,
        "JPEG",
        exif=_private_exif().tobytes(),
        xmp=XMP,
        comment=b"PrivateNote",
        icc_profile=ICC,
    )
    return str(path)


def _out(tmp_path):
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    return out


@pytest.mark.parametrize("keep", [True, False])
@pytest.mark.parametrize("out_fmt", ["original", "jpeg", "png"])
def test_the_colour_profile_survives_every_conversion(tmp_path, keep, out_fmt):
    src = _jpeg_source(tmp_path)
    dest = export_modes._process_image(
        src, str(_out(tmp_path) / "p.jpg"), out_fmt, 100, 85, keep_exif=keep
    )
    with Image.open(dest) as img:
        assert img.info.get("icc_profile") == ICC


def test_kept_metadata_keeps_xmp_and_the_comment(tmp_path):
    src = _jpeg_source(tmp_path)
    dest = export_modes._process_image(
        src, str(_out(tmp_path) / "p.jpg"), "jpeg", 100, 85, keep_exif=True
    )
    with Image.open(dest) as img:
        assert img.getexif().get(0x013B) == "PrivateOwner"
        assert b"PrivateXmp" in (img.info.get("xmp") or b"")
        assert img.info.get("comment") == b"PrivateNote"


@pytest.mark.parametrize("out_fmt", ["original", "jpeg", "png"])
def test_a_tiff_keeps_its_exif_when_asked(tmp_path, out_fmt):
    src = tmp_path / "src.tif"
    Image.new("RGB", (400, 200), "teal").save(src, "TIFF", exif=_private_exif().tobytes())
    dest = export_modes._process_image(
        str(src), str(_out(tmp_path) / "p.tif"), out_fmt, 100, 85, keep_exif=True
    )
    with Image.open(dest) as img:
        exif = img.getexif()
        assert exif.get(0x013B) == "PrivateOwner"
        assert exif.get_ifd(0x8825), "GPS kept"


def test_the_full_size_copy_without_metadata_drops_the_comment(tmp_path):
    src = _jpeg_source(tmp_path)
    dest = export_modes._copy_image_without_metadata(src, str(_out(tmp_path) / "p.jpg"), 85)
    assert _leaks(dest)[0] == []
    with Image.open(dest) as img:
        assert img.info.get("icc_profile") == ICC


def test_gallery_thumbnails_carry_no_metadata(tmp_path):
    from bpp.output.gallery import _make_thumbnail

    src = _jpeg_source(tmp_path)
    dest = str(_out(tmp_path) / "t.jpg")
    assert _make_thumbnail(src, dest, 64)
    assert _leaks(dest)[0] == []


def test_a_cmyk_copy_converted_to_rgb_drops_the_cmyk_profile(tmp_path):
    src = tmp_path / "print.jpg"
    Image.new("CMYK", (40, 20), (0, 50, 50, 0)).save(src, "JPEG", icc_profile=b"cmyk-profile")
    dest = export_modes._copy_image_without_metadata(str(src), str(_out(tmp_path) / "p.jpg"), 85)
    with Image.open(dest) as img:
        assert img.mode == "RGB"
        assert not img.info.get("icc_profile"), "a CMYK profile can't describe RGB pixels"


def test_a_resized_tiff_with_kept_metadata_is_readable(tmp_path):
    src = tmp_path / "scan.tif"
    Image.new("RGB", (400, 200), "teal").save(src, "TIFF", exif=_private_exif().tobytes())
    dest = export_modes._process_image(
        str(src), str(_out(tmp_path) / "s.tif"), "original", 100, 85, keep_exif=True
    )
    with Image.open(dest) as img:
        img.load()  # the layout fields must describe the resized pixels
        assert img.size == (100, 50)
        assert img.getexif().get(0x013B) == "PrivateOwner"


def test_kept_xmp_does_not_turn_the_photo_twice(tmp_path):
    from PIL import ImageOps

    src = tmp_path / "portrait.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6
    xmp = (
        b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF'
        b' xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description'
        b' xmlns:tiff="http://ns.adobe.com/tiff/1.0/" tiff:Orientation="6"/></rdf:RDF></x:xmpmeta>'
    )
    Image.new("RGB", (80, 40), "teal").save(src, "JPEG", exif=exif.tobytes(), xmp=xmp)
    dest = export_modes._process_image(
        str(src), str(_out(tmp_path) / "p.jpg"), "jpeg", None, 85, keep_exif=True
    )
    with Image.open(dest) as img:
        assert ImageOps.exif_transpose(img).size == (40, 80), "shown upright, not turned again"


def test_a_profile_is_dropped_when_the_pixels_change_colour_space(tmp_path):
    from bpp.output.gallery import _make_thumbnail

    src = tmp_path / "gray.png"
    Image.new("LA", (40, 20), (90, 255)).save(src, "PNG", icc_profile=b"gray-profile")
    out = _out(tmp_path)
    as_jpeg = export_modes._process_image(str(src), str(out / "g.png"), "jpeg", None, 85)
    with Image.open(as_jpeg) as img:
        assert (img.mode, img.info.get("icc_profile")) == ("L", b"gray-profile"), "gray stays gray"
    assert _make_thumbnail(str(src), str(out / "t.jpg"), 16)
    with Image.open(out / "t.jpg") as img:
        assert img.mode == "RGB" and not img.info.get("icc_profile")


def test_png_output_keeps_xmp_and_the_comment(tmp_path):
    src = _jpeg_source(tmp_path)
    dest = export_modes._process_image(
        src, str(_out(tmp_path) / "p.jpg"), "png", None, 85, keep_exif=True
    )
    with Image.open(dest) as img:
        assert b"PrivateXmp" in (img.info.get("xmp") or b"")
        assert img.info.get("Comment") == "PrivateNote"
    stripped = export_modes._process_image(src, str(tmp_path / "out" / "q.jpg"), "png", None, 85)
    assert _leaks(stripped)[0] == [] and "Comment" not in Image.open(stripped).info


def test_a_cmyk_photo_exports_as_png(tmp_path):
    src = tmp_path / "print.jpg"
    Image.new("CMYK", (40, 20), (0, 50, 50, 0)).save(src, "JPEG", icc_profile=b"cmyk-profile")
    dest = export_modes._process_image(str(src), str(_out(tmp_path) / "p.jpg"), "png", None, 85)
    with Image.open(dest) as img:
        assert img.mode == "RGB" and not img.info.get("icc_profile")


def test_exif_kept_in_sub_blocks_only_survives(tmp_path):
    """Date, exposure and GPS live in the Exif and GPS blocks; with nothing
    else at the top level they must still be kept."""
    src = tmp_path / "phone.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6
    exif.get_ifd(0x8769).update({0x9003: "2026:10:24 10:00:00", 0x829A: 0.01})
    exif.get_ifd(0x8825).update({1: "N", 2: (40.0, 0.0, 0.0)})
    Image.new("RGB", (80, 40), "teal").save(src, "JPEG", exif=exif.tobytes())
    for out_fmt in ("jpeg", "png"):
        dest = export_modes._process_image(
            str(src), str(_out(tmp_path) / f"p.{out_fmt}"), out_fmt, None, 85, keep_exif=True
        )
        with Image.open(dest) as img:
            kept = img.getexif()
            assert kept.get_ifd(0x8769).get(0x9003) == "2026:10:24 10:00:00", out_fmt
            assert kept.get_ifd(0x8825).get(1) == "N", out_fmt
            assert kept.get(0x0112, 1) == 1


@pytest.mark.parametrize("size", [(80, 40), (400, 200)])
def test_a_tiff_keeps_its_xmp(tmp_path, size):
    src = tmp_path / "scan.tif"
    exif = Image.Exif()
    exif[0x013B] = "PrivateOwner"
    exif[700] = XMP
    Image.new("RGB", size, "teal").save(src, "TIFF", exif=exif.tobytes())
    dest = export_modes._process_image(
        str(src), str(_out(tmp_path) / "s.tif"), "original", 100, 85, keep_exif=True
    )
    with Image.open(dest) as img:
        img.load()
        assert b"PrivateXmp" in (img.info.get("xmp") or b"")
    stripped = export_modes._process_image(
        str(src), str(tmp_path / "out" / "t.tif"), "original", 100, 85
    )
    with Image.open(stripped) as img:
        assert not img.info.get("xmp")
        assert not img.getexif().get(0x013B)


@pytest.mark.parametrize("out_fmt", ["original", "jpeg", "png"])
def test_a_multipage_tiff_with_gps_and_date_exports_with_them(tmp_path, out_fmt):
    """The kept EXIF is read while the TIFF is open: its blocks are read
    lazily from the file, which is closed by the time the copy is written."""
    src = tmp_path / "scan.tif"
    exif = Image.Exif()
    exif.get_ifd(0x8769).update({0x9003: "2026:10:24 10:00:00"})
    exif.get_ifd(0x8825).update({1: "N", 2: (40.0, 0.0, 0.0)})
    first = Image.new("RGB", (400, 200), "teal")
    first.save(
        src, "TIFF", exif=exif.tobytes(), save_all=True, append_images=[Image.new("RGB", (40, 20))]
    )
    dest = export_modes._process_image(
        str(src), str(_out(tmp_path) / "s.tif"), out_fmt, 100, 85, keep_exif=True
    )
    with Image.open(dest) as img:
        img.load()
        kept = img.getexif()
        assert kept.get_ifd(0x8825).get(1) == "N"
        assert kept.get_ifd(0x8769).get(0x9003) == "2026:10:24 10:00:00"

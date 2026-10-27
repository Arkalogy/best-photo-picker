"""The pixels an export converts or resizes, and the metadata the written
file may carry. Split out of :mod:`bpp.output.export_modes` for the
500-LOC cap.

One rule for every image an export writes (converted or resized photos,
the full-size copy without metadata, gallery thumbnails): the colour
profile always goes along, since the pixels mean nothing without it; the
EXIF, XMP and comment go along only when the user keeps metadata. An
encoder writes some of these back from the image's ``info`` unasked, so
they are cleared there and passed explicitly instead.
"""

from __future__ import annotations

import os
from typing import Any

from bpp.constants import RAW_EXTENSIONS
from bpp.utils.logging import get_logger

log = get_logger(__name__)

#: Metadata an image carries in ``info`` that an encoder may write back
#: unasked (the HEIF and JPEG encoders take XMP and the comment from it).
_PRIVATE_INFO = ("exif", "xmp", "XML:com.adobe.xmp", "comment")


#: Colour spaces a profile describes: a profile fits the pixels only while
#: they stay in its space (a converted copy drops it).
_GRAY_MODES = frozenset({"1", "L", "LA", "La", "I", "I;16", "F"})

#: Fields of the source's top EXIF block that describe the file's layout
#: (a TIFF keeps its EXIF there) or go separately (orientation, XMP, the
#: ICC profile): written back with resized pixels they would make the file
#: unreadable or contradict it. Everything else, the Exif / GPS /
#: interoperability blocks included, is kept as it is.
_LAYOUT_TAGS = frozenset(
    {0x00FE, 0x0100, 0x0101, 0x0102, 0x0103, 0x0106, 0x0111, 0x0112, 0x0115, 0x0116, 0x0117}
    | {0x011C, 0x013D, 0x0140, 0x0142, 0x0143, 0x0144, 0x0145, 0x0152, 0x0153, 0x0201}
    | {0x0202, 0x02BC, 0x8773, 0x014A}  # SubIFDs: a RAW's sensor-data blocks
)
#: A JPEG holds at most this much EXIF in its one APP1 segment (a WebP has no such cap).
_JPEG_EXIF_MAX = 65_000
_MAKER_NOTE = 0x927C
_XMP_TAG = 0x02BC


def _space(mode: str) -> str:
    return "gray" if mode in _GRAY_MODES else "cmyk" if mode == "CMYK" else "rgb"


def scrubbed(img: Any, *, to_mode: str | None = None) -> dict[str, Any]:
    """Clear every private field from ``img.info`` and return the save
    arguments that keep the colour: the ICC profile, when it has one and
    still fits the pixels once converted to *to_mode*."""
    for key in _PRIVATE_INFO:
        img.info.pop(key, None)
    icc = img.info.get("icc_profile")
    if not icc or (to_mode and _space(to_mode) != _space(img.mode)):
        return {}
    return {"icc_profile": icc}


def writer_mode(mode: str, fmt: str) -> str:
    """The pixel mode an encoder can write: JPEG holds no alpha or palette,
    PNG no CMYK."""
    if fmt == "JPEG":
        return {"RGBA": "RGB", "P": "RGB", "LA": "L"}.get(mode, mode)
    if fmt == "PNG" and mode == "CMYK":
        return "RGB"
    return mode


def encoder_args(save_kw: dict[str, Any], fmt: str, from_mode: str, to_mode: str) -> dict[str, Any]:
    """``save_kw`` as the *fmt* encoder takes it, for pixels converted from
    *from_mode* to *to_mode*: the profile only while it fits, and for PNG
    the XMP and comment as text chunks (its encoder ignores them otherwise)."""
    kw = dict(save_kw)
    if _space(from_mode) != _space(to_mode):
        kw.pop("icc_profile", None)
    if fmt in ("JPEG", "MPO") and kw.get("exif"):
        kw["exif"] = _fitted_exif(kw["exif"])
        if kw["exif"] is None:
            del kw["exif"]
    if fmt == "TIFF" and kw.get("xmp"):
        # The TIFF encoder ignores ``xmp=``: the packet goes in its tag.
        from PIL import Image

        exif = kw.get("exif") or Image.Exif()
        exif[_XMP_TAG] = kw.pop("xmp")
        kw["exif"] = exif
    if fmt == "PNG":
        from PIL.PngImagePlugin import PngInfo

        xmp, comment = kw.pop("xmp", None), kw.pop("comment", None)
        if xmp or comment:
            chunks = PngInfo()
            if xmp:
                chunks.add_itxt("XML:com.adobe.xmp", xmp.decode("utf-8", "replace"))
            if comment:
                text = comment.decode("utf-8", "replace") if isinstance(comment, bytes) else comment
                chunks.add_text("Comment", text)
            kw["pnginfo"] = chunks
    return kw


def _fitted_exif(exif: Any) -> Any:
    """``exif`` within a JPEG's limit: without the camera maker's private
    note when that is what makes it too long (a Nikon RAW's alone is ~145
    KB), else without the EXIF at all (logged), never a failed export."""
    from PIL import Image

    if not isinstance(exif, Image.Exif) or len(exif.tobytes()) <= _JPEG_EXIF_MAX:
        return exif
    exif.get_ifd(0x8769).pop(_MAKER_NOTE, None)
    if len(exif.tobytes()) <= _JPEG_EXIF_MAX:
        return exif
    log.warning("Exporting without the EXIF: too long for a JPEG even without the maker note")
    return None


def _detached_exif(opened: Any) -> Any:
    """The source's EXIF as a copy held in memory, without the layout
    fields. Read before the pixels load: a TIFF's EXIF blocks are read
    lazily from the file, which loading the pixels (or leaving the ``with``)
    closes. Everything else, the Exif / GPS / interoperability blocks
    included, is kept as it is. None when there is nothing to keep."""
    from PIL import Image

    try:
        exif = Image.Exif()
        exif.load(opened.getexif().tobytes())
    except Exception as e:  # a malformed APP1 block raises SyntaxError
        log.warning("Exporting %s without its EXIF (unreadable): %s", opened.filename, e)
        return None
    for tag in _LAYOUT_TAGS:
        exif.pop(tag, None)
    return exif or None


def _kept_metadata(exif: Any, upright: Any) -> dict[str, Any]:
    """Save arguments for kept metadata: the detached EXIF, and the XMP and
    comment of the upright image (the transpose drops the orientation from
    its XMP; the source's would turn the photo a second time)."""
    kept: dict[str, Any] = {"exif": exif} if exif else {}
    xmp = upright.info.get("xmp") or upright.info.get("XML:com.adobe.xmp")
    if xmp:
        kept["xmp"] = xmp.encode() if isinstance(xmp, str) else xmp
    comment = upright.info.get("comment") or upright.info.get("Comment")
    if comment:
        kept["comment"] = comment
    return kept


def _raw_pixels(src: str, max_size: int | None) -> Any:
    """A camera RAW decoded upright, within the app's limit on decodes at
    once, at half size when that is still at least *max_size* (a quarter
    of the work). Raises OSError naming the reason."""
    from PIL import Image

    from bpp.utils import raw

    name = os.path.basename(src)
    if not raw.RAWPY_AVAILABLE:
        raise OSError(f"RAW support isn't installed, so {name} can't be converted")
    try:
        half = bool(max_size) and max(raw.raw_dimensions(src)) >= 2 * max_size
    except Exception:
        half = False  # unreadable header: the full decode reports it
    try:
        with raw.decode_slot():
            return Image.fromarray(raw.decode_raw_rgb(src, half_size=half))
    except Exception as e:
        log.warning("RAW export decode failed for %s: %s", src, e)
        raise OSError(f"{name} couldn't be decoded") from None


def _raw_exif(src: str) -> Any:
    """A camera RAW's own EXIF (date, camera, GPS), read from its TIFF-style
    header; None for a format Pillow can't read (CR3, for one)."""
    from PIL import Image

    try:
        with Image.open(src) as header:
            return _detached_exif(header)
    except Exception:
        log.info("Exporting %s without its metadata: the RAW header can't be read", src)
        return None


def upright_pixels(
    src: str, keep_exif: bool, max_size: int | None = None
) -> tuple[Any, str | None, dict[str, Any]]:
    """The photo's pixels upright, its format, and the save arguments the
    written file carries (see the module docstring). A camera RAW goes
    through the RAW reader: the plain image reader can't decode it (it
    comes out upright and has no format a converted copy could keep)."""
    from PIL import Image, ImageOps

    if os.path.splitext(src)[1].lower() in RAW_EXTENSIONS:
        exif = _raw_exif(src) if keep_exif else None
        return _raw_pixels(src, max_size), None, {"exif": exif} if exif else {}
    with Image.open(src) as opened:
        exif = _detached_exif(opened) if keep_exif else None
        try:
            img = ImageOps.exif_transpose(opened)
        except Exception as e:  # a malformed APP1 block raises SyntaxError
            log.warning("Exporting %s without applying its EXIF (unreadable): %s", src, e)
            img = opened
        img = img.copy() if img is opened else img  # independent of the file handle
        kept = _kept_metadata(exif, img) if keep_exif else {}
        if opened.info.get("icc_profile") and not img.info.get("icc_profile"):
            img.info["icc_profile"] = opened.info["icc_profile"]
        return img, opened.format, {**scrubbed(img), **kept}

"""One crop generator for the face and pet thumbnails.

Both crops are the same operation: open the photo in display
orientation, scale a box stored in extraction space (``max_long_side``)
back onto the full-size image, pad it, crop, shrink to a square JPEG.
Keeping it in one place means a camera RAW photo is handled once, by
the RAW decoder, instead of each generator opening the file with PIL
(which can't read RAW) and failing.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import TYPE_CHECKING

from bpp.constants import JPEG_QUALITY_CROP
from bpp.media_types import MediaKind, media_kind_from_path
from bpp.utils import raw as raw_support
from bpp.utils.logging import get_logger

if TYPE_CHECKING:
    from PIL import Image

log = get_logger(__name__)


class RawUnreadable(RuntimeError):
    """A camera RAW couldn't be read: RAW support is missing (warned
    once by the decoder) or its conversion failed (logged). Raised by
    :func:`open_oriented` so a caller can report the RAW reason and fix
    instead of guessing from the file extension."""


@contextlib.contextmanager
def open_oriented(
    filepath: str, *, label: str, raw_full_jpeg: str | None = None
) -> Iterator[Image.Image]:
    """Yield the photo as a PIL image in display orientation.

    A camera RAW is read from its full-size JPEG conversion at
    *raw_full_jpeg* (made on first use and shared with the viewer, so a
    photo with eight faces is decoded once, not eight times); without a
    cache path it is decoded directly. Either way the camera's rotation
    is applied, as in the decode face extraction scaled its boxes from.
    Anything else is opened by PIL with its EXIF orientation applied.
    The file handle is released when the block ends. Raises
    :class:`RawUnreadable` when a camera RAW can't be read (no RAW
    support, which the decoder warns about once, or a failed
    conversion, which is logged).
    """
    from PIL import Image, ImageOps

    from bpp.utils.retry import retry_io

    if media_kind_from_path(filepath) is MediaKind.RAW:
        if raw_full_jpeg is None:
            try:
                with raw_support.decode_slot():
                    rgb = raw_support.decode_raw_rgb(filepath)
            except raw_support.RawSupportMissing as e:
                raise RawUnreadable(filepath) from e
            yield Image.fromarray(rgb)
            return
        converted = raw_support.ensure_raw_jpeg(filepath, raw_full_jpeg)
        if converted is None:
            raise RawUnreadable(filepath)
        # The conversion carries no EXIF and is already upright: no
        # transpose (which would copy the whole frame).
        with retry_io(Image.open, converted, label=label) as src:
            yield src
        return

    with retry_io(Image.open, filepath, label=label) as src:
        # exif_transpose() returns a new Image, so the open file isn't
        # pinned past the caller's block.
        yield ImageOps.exif_transpose(src)


def generate_box_crop(
    filepath: str,
    bbox: tuple[int, int, int, int],
    crop_path: str,
    *,
    padding: float,
    size: int,
    max_long_side: int,
    label: str,
    raw_full_jpeg: str | None = None,
) -> str | None:
    """Write the padded square crop of *bbox* to *crop_path*.

    *bbox* is ``(x, y, w, h)`` in the extraction space (the image scaled
    to *max_long_side*); it is scaled back onto the full-size image. A
    camera RAW is read through its cached conversion at *raw_full_jpeg*
    when given (see :func:`open_oriented`). Returns *crop_path*, or None
    when the photo can't be read or the box falls outside it; raises
    :class:`RawUnreadable` when a camera RAW couldn't be read at all.
    """
    from PIL import Image

    bx, by, bw, bh = bbox
    try:
        with open_oriented(filepath, label=label, raw_full_jpeg=raw_full_jpeg) as img:
            orig_w, orig_h = img.size
            long_side = max(orig_w, orig_h)
            scale = long_side / max_long_side if long_side > max_long_side else 1.0

            sx = int(bx * scale)
            sy = int(by * scale)
            sw = int(bw * scale)
            sh = int(bh * scale)

            pad_x = int(sw * padding)
            pad_y = int(sh * padding)
            x1 = max(0, sx - pad_x)
            y1 = max(0, sy - pad_y)
            x2 = min(orig_w, sx + sw + pad_x)
            y2 = min(orig_h, sy + sh + pad_y)

            if x1 >= x2 or y1 >= y2:
                log.warning("Invalid %s region for %s: %s", label, filepath, bbox)
                return None

            crop = img.crop((x1, y1, x2, y2))
        crop.thumbnail((size, size), Image.LANCZOS)
        crop.convert("RGB").save(crop_path, "JPEG", quality=JPEG_QUALITY_CROP)
        return crop_path
    except RawUnreadable:
        raise  # already warned / logged
    except Exception:
        log.exception("Failed to generate %s for %s", label, filepath)
        return None

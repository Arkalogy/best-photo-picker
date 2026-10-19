"""Face-thumbnail generation.

Pure path + bbox → JPEG-on-disk operation, no DB or worker state.
Re-exported from face_worker so existing imports keep working.

Cache key includes the bbox so a re-cluster that shifts a face's
bbox produces a fresh crop instead of serving a stale one.
"""

from __future__ import annotations

import os

from bpp.constants import FACE_CROP_PADDING, FACE_CROP_SIZE
from bpp.utils.logging import get_logger
from bpp.web import box_crop

log = get_logger(__name__)


def generate_face_crop(
    filepath: str,
    bbox: tuple[int, int, int, int],
    crop_dir: str,
    path_hash: str,
    face_index: int,
    max_long_side: int = 1024,
    raw_full_jpeg: str | None = None,
) -> str | None:
    """Generate a cropped face thumbnail. Returns the saved path, or None
    on failure. *raw_full_jpeg* is the cache path of a camera RAW's
    full-size conversion (see ``box_crop.open_oriented``)."""
    bx, by, bw, bh = bbox
    bbox_tag = f"{bx}_{by}_{bw}_{bh}"
    crop_path = os.path.join(crop_dir, f"{path_hash}_{face_index}_{bbox_tag}.jpg")
    if os.path.exists(crop_path):
        return crop_path

    # Clean up old crops for this face_index (stale bbox or old naming format)
    import glob as _glob

    for old in _glob.glob(os.path.join(crop_dir, f"{path_hash}_{face_index}*.jpg")):
        if old != crop_path:
            try:
                os.remove(old)
            except OSError:
                log.debug("Could not remove stale crop %s", old, exc_info=True)

    return box_crop.generate_box_crop(
        filepath,
        bbox,
        crop_path,
        padding=FACE_CROP_PADDING,
        size=FACE_CROP_SIZE,
        max_long_side=max_long_side,
        label="face_crop",
        raw_full_jpeg=raw_full_jpeg,
    )

"""Face-thumbnail generation.

Pure path + bbox → JPEG-on-disk operation, no DB or worker state.
Re-exported from face_worker so existing imports keep working.

Cache key includes the bbox so a re-cluster that shifts a face's
bbox produces a fresh crop instead of serving a stale one.
"""

from __future__ import annotations

import os

from bpp.constants import FACE_CROP_PADDING, FACE_CROP_SIZE, JPEG_QUALITY_CROP
from bpp.utils.logging import get_logger

log = get_logger(__name__)


def generate_face_crop(
    filepath: str,
    bbox: tuple[int, int, int, int],
    crop_dir: str,
    path_hash: str,
    face_index: int,
    max_long_side: int = 1024,
) -> str | None:
    """Generate a cropped face thumbnail. Returns the saved path, or None on failure."""
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

    try:
        from PIL import Image, ImageOps

        from bpp.utils.retry import retry_io

        # Context manager closes the underlying file handle — these crops
        # are generated on demand per request, so a leaked FD per crop
        # accumulates under load.
        with retry_io(Image.open, filepath, label="face_crop") as src:
            img = ImageOps.exif_transpose(src)
            orig_w, orig_h = img.size
            long_side = max(orig_w, orig_h)
            scale = long_side / max_long_side if long_side > max_long_side else 1.0

            sx = int(bx * scale)
            sy = int(by * scale)
            sw = int(bw * scale)
            sh = int(bh * scale)

            pad_x = int(sw * FACE_CROP_PADDING)
            pad_y = int(sh * FACE_CROP_PADDING)
            x1 = max(0, sx - pad_x)
            y1 = max(0, sy - pad_y)
            x2 = min(orig_w, sx + sw + pad_x)
            y2 = min(orig_h, sy + sh + pad_y)

            if x1 >= x2 or y1 >= y2:
                log.warning("Invalid crop region for %s face %d", filepath, face_index)
                return None

            crop = img.crop((x1, y1, x2, y2))
        crop.thumbnail((FACE_CROP_SIZE, FACE_CROP_SIZE), Image.LANCZOS)
        crop.convert("RGB").save(crop_path, "JPEG", quality=JPEG_QUALITY_CROP)
        return crop_path
    except Exception:
        log.exception("Failed to generate face crop for %s", filepath)
        return None

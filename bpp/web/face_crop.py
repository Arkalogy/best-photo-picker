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
from bpp.web.photo_cache import own_cache_file

log = get_logger(__name__)


def remove_face_crops(
    crop_dir: str, path_hash: str, face_index: object, *, keep: str | None = None
) -> None:
    """Delete the cached crops of one face: the ``.jpg`` entries of
    ``crop_dir`` named ``{path_hash}_{face_index}…``, except ``keep``.

    The folder is listed and names are compared as text; no pattern is
    built from the values. ``face_index`` can come from the library
    database, and a stored value holding path or glob characters must
    not be able to point the delete at another folder: anything but a
    plain integer removes nothing.
    """
    if isinstance(face_index, bool) or not isinstance(face_index, int):
        log.warning("Not removing face crops: face index %r is not an integer", face_index)
        return
    prefix = f"{path_hash}_{face_index}"
    try:
        # "<prefix>.jpg" (old naming) or "<prefix>_<bbox>.jpg": a bare
        # prefix match would take face 1's crops and faces 10 to 19's too.
        names = [
            n
            for n in os.listdir(crop_dir)
            if n == prefix + ".jpg" or (n.startswith(prefix + "_") and n.endswith(".jpg"))
        ]
    except OSError:
        return
    for name in names:
        path = os.path.join(crop_dir, name)
        if path == keep:
            continue
        try:
            os.remove(path)
        except OSError:
            log.debug("Could not remove stale crop %s", path, exc_info=True)


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
    if own_cache_file(crop_path):
        return crop_path

    # Clean up old crops for this face_index (stale bbox or old naming format)
    remove_face_crops(crop_dir, path_hash, face_index, keep=crop_path)

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

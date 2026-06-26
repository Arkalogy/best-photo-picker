"""RAW image file detection and conversion utilities."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from bpp.utils.logging import get_logger

if TYPE_CHECKING:
    from PIL import Image

log = get_logger(__name__)

RAW_EXTENSIONS = frozenset(
    {".cr2", ".cr3", ".nef", ".arw", ".orf", ".raf", ".rw2", ".dng", ".pef", ".srw", ".x3f"}
)

# Check if rawpy is available (optional dependency)
try:
    import rawpy  # noqa: F401

    RAWPY_AVAILABLE = True
except ImportError:
    RAWPY_AVAILABLE = False


def is_raw_file(filepath: str) -> bool:
    """Check if a filepath has a RAW image extension."""
    ext = os.path.splitext(filepath)[1].lower()
    return ext in RAW_EXTENSIONS


def open_raw_as_pil(raw_path: str) -> Image.Image | None:
    """Open a RAW file and return a PIL RGB Image, or None on failure."""
    if not RAWPY_AVAILABLE:
        log.debug("rawpy not available, cannot open %s", raw_path)
        return None
    try:
        import rawpy
        from PIL import Image

        with rawpy.imread(raw_path) as raw:
            rgb = raw.postprocess(use_camera_wb=True, half_size=False)
        return Image.fromarray(rgb)
    except Exception as e:
        log.warning("RAW open failed for %s: %s", raw_path, e)
        return None


def convert_raw_to_jpeg(raw_path: str, output_path: str, quality: int = 92) -> str | None:
    """Convert a RAW file to JPEG. Returns output path on success, None on failure.

    Requires rawpy to be installed. Returns None if rawpy is not available
    or if the conversion fails.
    """
    if not RAWPY_AVAILABLE:
        log.debug("rawpy not available, cannot convert %s", raw_path)
        return None

    try:
        import rawpy

        with rawpy.imread(raw_path) as raw:
            rgb = raw.postprocess(use_camera_wb=True, half_size=False)

        from PIL import Image

        img = Image.fromarray(rgb)
        img.save(output_path, "JPEG", quality=quality)
        return output_path
    except Exception as e:
        log.warning("RAW conversion failed for %s: %s", raw_path, e)
        return None

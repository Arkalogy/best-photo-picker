"""RAW image file detection and conversion utilities."""

from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING

from bpp.utils.logging import get_logger

if TYPE_CHECKING:
    import numpy as np
    from PIL import Image

log = get_logger(__name__)

RAW_EXTENSIONS = frozenset(
    {".cr2", ".cr3", ".nef", ".arw", ".orf", ".raf", ".rw2", ".dng", ".pef", ".srw", ".x3f"}
)

# Check if rawpy is available (optional dependency)
_RAWPY_IMPORT_ERROR: str | None = None
try:
    import rawpy  # noqa: F401

    RAWPY_AVAILABLE = True
except ImportError as _exc:
    RAWPY_AVAILABLE = False
    # Kept for the warning: "not installed" and "installed but its native
    # library failed to load" need different fixes.
    _RAWPY_IMPORT_ERROR = str(_exc)

#: Held once the "RAW support isn't available" warning was shown: taking
#: it is the atomic "first time?" check, safe across parallel threads.
_missing_rawpy_once = threading.Lock()


def is_raw_file(filepath: str) -> bool:
    """Check if a filepath has a RAW image extension."""
    ext = os.path.splitext(filepath)[1].lower()
    return ext in RAW_EXTENSIONS


class RawSupportMissing(RuntimeError):
    """RAW photos can't be decoded in this install (rawpy unavailable)."""


def _note_missing_rawpy(raw_path: str) -> None:
    """Warn once per process (then debug) that RAW photos are skipped."""
    if not _missing_rawpy_once.acquire(blocking=False):
        log.debug("rawpy not available, cannot open %s", raw_path)
        return
    log.warning(
        "Can't open RAW photos (e.g. %s): RAW support isn't available (%s), so "
        "RAW photos are skipped. Fix: `pip install 'bppicker[raw]'`, or reinstall "
        "the desktop app. Further skips are logged at debug level.",
        raw_path,
        _RAWPY_IMPORT_ERROR or "rawpy isn't installed",
    )


def _decode_path(raw_path: str, half_size: bool) -> np.ndarray:
    import rawpy

    with rawpy.imread(raw_path) as raw:
        return raw.postprocess(use_camera_wb=True, half_size=half_size)


def decode_raw_rgb(raw_path: str, *, half_size: bool = False) -> np.ndarray:
    """Decode a RAW file to an RGB uint8 array (full size unless
    *half_size* — a 4x cheaper decode for small model inputs).

    The decoder reads the file from its path. A transient OS error opening
    it (a network drive dropping out) is retried by ``retry_io``; a decode
    error (a damaged or truncated file) fails at once. Raises
    :class:`RawSupportMissing` when RAW support isn't available (after a
    one-time warning) and the decoder's own error when the file can't be
    decoded — callers decide what a failure means.
    """
    from bpp.utils.retry import retry_io

    if not RAWPY_AVAILABLE:
        _note_missing_rawpy(raw_path)
        raise RawSupportMissing(raw_path)
    return retry_io(_decode_path, raw_path, half_size, label="decode_raw")


def preview_failure() -> tuple[str, str]:
    """Why a RAW photo can't be shown, and what to do: ``(reason,
    recovery)`` for the viewer's error toast. Never the import error or a
    path — those stay in the server log."""
    if not RAWPY_AVAILABLE:
        return (
            "RAW support isn't available in this install",
            "reinstall the desktop app, or run: pip install 'bppicker[raw]'",
        )
    return (
        "the RAW file couldn't be converted",
        "check the file and that its drive is connected; details are in "
        "Settings → Activity on this computer",
    )


def open_raw_as_pil(raw_path: str) -> Image.Image | None:
    """Open a RAW file and return a PIL RGB Image, or None on failure."""
    from PIL import Image

    try:
        return Image.fromarray(decode_raw_rgb(raw_path))
    except RawSupportMissing:
        return None
    except Exception as e:
        log.warning("RAW open failed for %s: %s", raw_path, e)
        return None


def convert_raw_to_jpeg(raw_path: str, output_path: str, quality: int = 92) -> str | None:
    """Convert a RAW file to JPEG. Returns output path on success, None on failure.

    Requires rawpy to be installed. Returns None if rawpy is not available
    or if the conversion fails.
    """
    from PIL import Image

    # Written to a temp file and renamed: a failure part-way (disk full)
    # must not leave a truncated preview that would be served from then on.
    tmp = f"{output_path}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        Image.fromarray(decode_raw_rgb(raw_path)).save(tmp, "JPEG", quality=quality)
        os.replace(tmp, output_path)
        return output_path
    except RawSupportMissing:
        return None
    except Exception as e:
        log.warning("RAW conversion failed for %s: %s", raw_path, e)
        return None
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

"""RAW image file detection and conversion utilities."""

from __future__ import annotations

import os
import threading
import time
from typing import TYPE_CHECKING

from bpp.utils.fresh_file import replace_fresh
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


#: The recovery hint for a photo file that can't be read right now; the
#: viewer, the crop and the editor toasts all say the same thing.
CHECK_FILE_RECOVERY = (
    "check the file and that its drive is connected; details are in "
    "Settings → Activity on this computer"
)


#: Same cap as PIL / OpenCV decodes (``MAX_IMAGE_PIXELS`` in
#: ``bpp/scoring/aggregate.py``, ``OPENCV_IO_MAX_IMAGE_PIXELS`` in
#: ``bpp/__init__.py``): LibRaw's postprocess allocates 16-bit RGB for
#: the whole frame, so a crafted header could ask for gigabytes.
MAX_RAW_PIXELS = 200_000_000


class RawTooLarge(ValueError):
    """The RAW header declares more pixels than :data:`MAX_RAW_PIXELS`."""


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

    if not hasattr(rawpy, "RawPy"):  # a minimal stand-in: no header access
        with rawpy.imread(raw_path) as raw:
            return raw.postprocess(use_camera_wb=True, half_size=half_size)
    # open_file + sizes + unpack + postprocess on one handle: the pixel
    # cap is checked from the header before the frame is unpacked, and
    # the file is opened once (a NAS library pays per open).
    raw = rawpy.RawPy()
    try:
        raw.open_file(raw_path)
        sizes = raw.sizes
        if sizes.width * sizes.height > MAX_RAW_PIXELS:
            raise RawTooLarge(f"{sizes.width}x{sizes.height} exceeds {MAX_RAW_PIXELS} pixels")
        raw.unpack()
        return raw.postprocess(use_camera_wb=True, half_size=half_size)
    finally:
        raw.close()


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


def _read_sizes(raw_path: str) -> tuple[int, int, int]:
    import rawpy

    # open_file() alone: LibRaw fills the size table while identifying
    # the file. imread() + postprocess() would unpack the whole frame.
    r = rawpy.RawPy()
    try:
        r.open_file(raw_path)
        sizes = r.sizes
        return sizes.width, sizes.height, sizes.flip
    finally:
        r.close()


def raw_dimensions(raw_path: str) -> tuple[int, int]:
    """``(width, height)`` of the decoded photo in display orientation,
    read from the RAW header without a pixel decode.

    The camera's flip is applied the way :func:`decode_raw_rgb` applies
    it: a 90 deg flip (LibRaw values 4-7) swaps the two, so these are
    the dimensions of the decoded image that boxes were scaled from.
    Raises :class:`RawSupportMissing` when RAW support isn't available
    (after a one-time warning).
    """
    from bpp.utils.retry import retry_io

    if not RAWPY_AVAILABLE:
        _note_missing_rawpy(raw_path)
        raise RawSupportMissing(raw_path)
    width, height, flip = retry_io(_read_sizes, raw_path, label="raw_dimensions")
    # LibRaw swaps the two whenever the flip has the "rotate 90" bit
    # (values 4-7), the same test its postprocess output uses.
    return (height, width) if flip & 4 else (width, height)


def preview_failure() -> tuple[str, str]:
    """Why a RAW photo can't be shown, and what to do: ``(reason,
    recovery)`` for the viewer's error toast. Never the import error or a
    path — those stay in the server log."""
    if not RAWPY_AVAILABLE:
        return (
            "RAW support isn't available in this install",
            "reinstall the desktop app, or run: pip install 'bppicker[raw]'",
        )
    return ("the RAW file couldn't be converted", CHECK_FILE_RECOVERY)


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


#: One conversion per RAW photo at a time: the viewer and a photo's face
#: and pet crops arrive together, and each would otherwise decode the
#: whole frame. One lock per conversion path for the life of the
#: process (a few dozen bytes per RAW photo ever converted).
_conversion_locks_guard = threading.Lock()
_conversion_locks: dict[str, threading.Lock] = {}
#: At most this many RAW decodes run at once across all requests: a
#: People grid over a RAW library asks for dozens of crops together,
#: and each decode holds a full-frame 16-bit buffer.
_conversion_slots = threading.BoundedSemaphore(2)
#: Conversions that failed, keyed by the conversion path: the RAW file's
#: (mtime, size) and when it failed. A damaged file is not re-decoded on
#: every request, only after it changes or, in case the failure was the
#: environment's (disk full, drive gone), after a few minutes.
_failed_conversions: dict[str, tuple[tuple[float, int], float]] = {}
#: Short: the failure may have been the drive or the disk, which the
#: toast tells the user to fix, and a retry must follow that soon.
FAILED_CONVERSION_RETRY_SECONDS = 60.0
#: The RAW file's (mtime, size) each conversion was made from, so a
#: file dated in the future (where mtimes can't say) is still noticed
#: when it is replaced.
_converted_signatures: dict[str, tuple[float, int]] = {}


def decode_slot() -> threading.BoundedSemaphore:
    """The limit on simultaneous RAW decodes, for the few readers that
    decode directly rather than through :func:`ensure_raw_jpeg`."""
    return _conversion_slots


def _conversion_lock(jpeg_path: str) -> threading.Lock:
    with _conversion_locks_guard:
        lock = _conversion_locks.get(jpeg_path)
        if lock is None:
            lock = _conversion_locks[jpeg_path] = threading.Lock()
        return lock


def _conversion_is_fresh(raw_path: str, jpeg_path: str) -> bool:
    try:
        jpeg_mtime = os.path.getmtime(jpeg_path)
    except OSError:
        return False
    try:
        raw_mtime = os.path.getmtime(raw_path)
    except OSError:
        # The RAW can't be checked right now (a drive hiccup): the
        # conversion on disk is still the best answer.
        return True
    if raw_mtime > time.time():
        # Dated in the future (camera clock ahead, mtime kept on copy):
        # no conversion could ever be newer by mtime, so the one on disk
        # stands unless the file it was made from has changed.
        known = _converted_signatures.get(jpeg_path)
        return known is None or known == _raw_signature(raw_path)
    return jpeg_mtime >= raw_mtime


def _raw_signature(raw_path: str) -> tuple[float, int] | None:
    try:
        st = os.stat(raw_path)
    except OSError:
        return None
    return (st.st_mtime, st.st_size)


def ensure_raw_jpeg(raw_path: str, jpeg_path: str) -> str | None:
    """The full-size JPEG conversion of *raw_path* at *jpeg_path*,
    converting when it is missing or older than the RAW file. One
    decode then serves the viewer and every face / pet crop of the
    photo: concurrent requests for the same photo wait for the first
    conversion instead of each decoding, and a file whose conversion
    failed isn't decoded again until it changes. Returns None when the
    conversion fails (already logged)."""
    if _conversion_is_fresh(raw_path, jpeg_path):
        return jpeg_path
    with _conversion_lock(jpeg_path):
        if _conversion_is_fresh(raw_path, jpeg_path):
            return jpeg_path  # converted by the request we waited for
        signature = _raw_signature(raw_path)
        failed = _failed_conversions.get(jpeg_path)
        if (
            signature is not None
            and failed is not None
            and failed[0] == signature
            and time.monotonic() - failed[1] < FAILED_CONVERSION_RETRY_SECONDS
        ):
            return None  # same damaged file as a moment ago
        with _conversion_slots:
            out = convert_raw_to_jpeg(raw_path, jpeg_path)
        if out is None:
            if signature is not None:
                _failed_conversions[jpeg_path] = (signature, time.monotonic())
            return None
        _failed_conversions.pop(jpeg_path, None)
        if signature is not None:
            _converted_signatures[jpeg_path] = signature
        return out


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
        with replace_fresh(output_path, part=tmp) as fh:
            Image.fromarray(decode_raw_rgb(raw_path)).save(fh, "JPEG", quality=quality)
        return output_path
    except RawSupportMissing:
        return None
    except Exception as e:
        log.warning("RAW conversion failed for %s: %s", raw_path, e)
        return None
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

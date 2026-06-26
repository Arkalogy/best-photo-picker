"""Perceptual hashing for near-duplicate detection."""

from __future__ import annotations

import cv2
import numpy as np

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:
    pass

from bpp.utils.logging import get_logger

log = get_logger(__name__)


def _pack_hash(diff: np.ndarray) -> int:
    """Pack a boolean diff array into a signed 64-bit int (SQLite-safe).

    np.packbits produces uint64 which can exceed SQLite's max INTEGER
    (2^63-1). Reinterpret as signed int64 — hamming distance is unaffected.
    """
    return int(np.packbits(diff.flatten()[:64]).view(np.int64)[0])


def compute_dhash(image: np.ndarray, hash_size: int = 8) -> int:
    """Compute difference hash (dHash) of an image.

    Returns a 64-bit signed integer hash.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image

    # Resize to (hash_size+1) x hash_size
    resized = cv2.resize(gray, (hash_size + 1, hash_size), interpolation=cv2.INTER_AREA)

    # Compute differences between adjacent pixels
    diff = resized[:, 1:] > resized[:, :-1]

    return _pack_hash(diff)


def compute_ahash(image: np.ndarray, hash_size: int = 8) -> int:
    """Compute average hash (aHash) of an image.

    More tolerant of small positional changes than dHash.
    Returns a 64-bit signed integer hash.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
    resized = cv2.resize(gray, (hash_size, hash_size), interpolation=cv2.INTER_AREA)
    avg = resized.mean()
    diff = resized > avg
    return _pack_hash(diff)


_HEIC_EXTS = {".heic", ".heif"}
_VIDEO_EXTS = {
    ".mp4",
    ".mov",
    ".avi",
    ".mkv",
    ".webm",
    ".m4v",
    ".3gp",
    ".wmv",
    ".flv",
}


def _load_image(filepath: str) -> np.ndarray:
    """Load image as grayscale with EXIF rotation.

    Routes HEIC files directly to PIL (cv2 doesn't support them).
    Skips video files entirely.
    Retries on transient I/O errors (NAS flakes).
    """
    import os
    import time

    from bpp.utils.retry import is_transient

    ext = os.path.splitext(filepath)[1].lower()
    if ext in _VIDEO_EXTS:
        raise ValueError(f"Cannot hash video file: {filepath}")

    # HEIC: go straight to PIL (cv2 has no HEIC support)
    use_pil = ext in _HEIC_EXTS

    for attempt in range(3):
        try:
            if use_pil:
                from PIL import Image, ImageOps

                # Context manager releases the FD before the array copy.
                # exif_transpose() returns a new Image so the open file
                # isn't pinned past the `with` block.
                with Image.open(filepath) as raw:
                    pil_img = ImageOps.exif_transpose(raw).convert("L")
                return np.array(pil_img)

            img = cv2.imread(filepath, cv2.IMREAD_GRAYSCALE)
            if img is None:
                # cv2 failed — try PIL as fallback. Same FD-release semantics
                # as the use_pil branch above.
                from PIL import Image, ImageOps

                with Image.open(filepath) as raw:
                    pil_img = ImageOps.exif_transpose(raw).convert("L")
                return np.array(pil_img)
            return img
        except OSError as e:
            if attempt < 2 and is_transient(e):
                time.sleep(0.5 * (2**attempt))
                continue
            raise


def compute_hashes_from_file(filepath: str) -> tuple[int | None, int | None]:
    """Compute both dHash and aHash from a file. Returns (dhash, ahash)."""
    try:
        img = _load_image(filepath)
        return compute_dhash(img), compute_ahash(img)
    except Exception as e:
        log.warning("Failed to compute hashes for %s: %s", filepath, e)
        return None, None


def compute_dhash_from_file(filepath: str) -> int | None:
    """Load image, apply EXIF rotation, compute dHash."""
    try:
        return compute_dhash(_load_image(filepath))
    except Exception as e:
        log.warning("Failed to compute hash for %s: %s", filepath, e)
        return None


def hamming_distance(hash1: int, hash2: int) -> int:
    """Compute Hamming distance between two hashes."""
    xor = hash1 ^ hash2
    return bin(xor).count("1")


def dual_hash_distance(dhash1: int, ahash1: int, dhash2: int, ahash2: int) -> int:
    """Min Hamming distance across dHash and aHash. More robust for burst photos."""
    return min(hamming_distance(dhash1, dhash2), hamming_distance(ahash1, ahash2))

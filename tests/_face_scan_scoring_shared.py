"""Shared fixtures and helpers of the test_face_scan_scoring.py suite (split for the
500-LOC cap)."""

from __future__ import annotations

import numpy as np
from PIL import Image

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _blank_bgr(h: int = 100, w: int = 100, color=(128, 128, 128)):
    """Create a solid-color BGR image (no faces)."""
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = color
    return img


def _random_bgr(seed: int = 0, h: int = 100, w: int = 100):
    """Create a deterministic random BGR image."""
    rng = np.random.RandomState(seed)
    return rng.randint(0, 256, (h, w, 3), dtype=np.uint8)


def _grayscale(h: int = 100, w: int = 100, value: int = 128):
    """Create a single-channel grayscale image."""
    return np.full((h, w), value, dtype=np.uint8)


def _save_jpeg(path, w=100, h=100, color="blue"):
    """Save a minimal JPEG file via PIL."""
    Image.new("RGB", (w, h), color).save(str(path), "JPEG")


def _save_png(path, w=100, h=100, color="red"):
    """Save a minimal PNG file via PIL."""
    Image.new("RGB", (w, h), color).save(str(path), "PNG")

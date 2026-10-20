"""Shared fixtures and helpers of the test_model_quality.py suite (split for the 500-LOC cap)."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from tests._models import skip_unless_scoring_models


@pytest.fixture(autouse=True)
def _real_models_on_disk():
    """This module runs the real detectors and landmarkers; tests never
    download, so it skips where the weights aren't on disk (CI keeps
    them in its model cache)."""
    skip_unless_scoring_models()


def _solid_bgr(h: int = 480, w: int = 640, color: tuple = (128, 128, 128)) -> np.ndarray:
    """Create a solid-color BGR image."""
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = color
    return img


def _draw_circle_face(
    img: np.ndarray,
    center: tuple[int, int],
    radius: int = 40,
    skin_color: tuple = (180, 200, 230),
) -> np.ndarray:
    """Draw a simple circle 'face' with eyes and mouth — crude but sometimes detected."""
    cx, cy = center
    # Head
    cv2.circle(img, (cx, cy), radius, skin_color, -1)
    # Eyes
    eye_y = cy - radius // 4
    eye_dx = radius // 3
    cv2.circle(img, (cx - eye_dx, eye_y), radius // 8, (50, 50, 50), -1)
    cv2.circle(img, (cx + eye_dx, eye_y), radius // 8, (50, 50, 50), -1)
    # Mouth
    mouth_y = cy + radius // 3
    cv2.ellipse(img, (cx, mouth_y), (radius // 3, radius // 6), 0, 0, 180, (50, 50, 50), 2)
    return img


def _noise_image(h: int = 480, w: int = 640, seed: int = 42) -> np.ndarray:
    """Create a random noise BGR image (no face expected)."""
    rng = np.random.RandomState(seed)
    return rng.randint(0, 256, (h, w, 3), dtype=np.uint8)


def _gradient_image(h: int = 480, w: int = 640) -> np.ndarray:
    """Create a smooth gradient image (no face expected)."""
    row = np.linspace(0, 255, w, dtype=np.uint8)
    img = np.tile(row, (h, 1))
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

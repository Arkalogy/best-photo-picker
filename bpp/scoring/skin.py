"""Skin exposure detection using color-space analysis."""

from __future__ import annotations

import cv2
import numpy as np


def score_skin_exposure(img: np.ndarray) -> float:
    """Estimate skin pixel ratio (0-1) using YCrCb color space.

    Higher values indicate more visible skin. Useful for filtering
    photos with excessive skin exposure.
    """
    ycrcb = cv2.cvtColor(img, cv2.COLOR_BGR2YCrCb)
    mask = cv2.inRange(ycrcb, np.array([0, 133, 77]), np.array([255, 173, 127]))
    ratio = np.count_nonzero(mask) / mask.size
    return float(ratio)

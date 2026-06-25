"""Exposure scoring based on luminance histogram analysis."""

from __future__ import annotations

import cv2
import numpy as np


def score_exposure(image: np.ndarray) -> float:
    """Score exposure quality from 0..1.

    Penalizes under-exposed and over-exposed images.
    Uses luminance channel histogram analysis.
    """
    if len(image.shape) == 3:
        # Convert to LAB and use L channel
        lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
        lum = lab[:, :, 0]  # L channel, range 0-255
    else:
        lum = image

    hist = cv2.calcHist([lum], [0], None, [256], [0, 256]).flatten()
    total = hist.sum()
    if total == 0:
        return 0.0
    hist = hist / total

    # Mean luminance (ideal ~128 for well-exposed)
    mean_lum = np.average(np.arange(256), weights=hist)

    # Penalize deviation from ideal mean
    mean_score = 1.0 - abs(mean_lum - 128) / 128.0

    # Penalize clipping: fraction of pixels near 0 or 255
    underexposed = hist[:10].sum()
    overexposed = hist[246:].sum()
    clip_penalty = 1.0 - min(1.0, (underexposed + overexposed) * 3.0)

    # Reward good spread (std dev)
    std_lum = np.sqrt(np.average((np.arange(256) - mean_lum) ** 2, weights=hist))
    spread_score = min(1.0, std_lum / 64.0)

    score = 0.5 * mean_score + 0.3 * clip_penalty + 0.2 * spread_score
    return float(max(0.0, min(1.0, score)))

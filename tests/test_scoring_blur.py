"""Tests for blur/sharpness scoring with synthetic images."""

from __future__ import annotations

import cv2
import numpy as np

from bpp.scoring.blur import (
    _face_coverage,
    _face_region_laplacian,
    compute_laplacian_variance,
    score_blur_raw,
)
from bpp.scoring.exposure import score_exposure


def _make_sharp_image(size: int = 200) -> np.ndarray:
    """Create a sharp synthetic image with high-frequency edges."""
    img = np.zeros((size, size, 3), dtype=np.uint8)
    # Checkerboard pattern = lots of edges
    for y in range(0, size, 10):
        for x in range(0, size, 10):
            if (x // 10 + y // 10) % 2 == 0:
                img[y : y + 10, x : x + 10] = [200, 200, 200]
    return img


def _make_blurry_image(size: int = 200) -> np.ndarray:
    """Create a blurry synthetic image."""
    sharp = _make_sharp_image(size)
    return cv2.GaussianBlur(sharp, (31, 31), 10)


def _make_uniform_image(size: int = 200, value: int = 128) -> np.ndarray:
    """Create a uniform gray image (extremely blurry)."""
    return np.full((size, size, 3), value, dtype=np.uint8)


def test_sharp_has_higher_variance_than_blurry():
    sharp = _make_sharp_image()
    blurry = _make_blurry_image()
    assert compute_laplacian_variance(sharp) > compute_laplacian_variance(blurry)


def test_uniform_image_has_zero_variance():
    uniform = _make_uniform_image()
    var = compute_laplacian_variance(uniform)
    assert var < 1.0


def test_blur_raw_returns_positive():
    img = _make_sharp_image()
    score = score_blur_raw(img)
    assert score > 0


def test_exposure_well_exposed():
    """A mid-gray image should score well for exposure."""
    img = _make_uniform_image(value=128)
    score = score_exposure(img)
    assert 0.4 < score < 1.0


def test_exposure_underexposed():
    """A very dark image should score lower."""
    dark = _make_uniform_image(value=10)
    mid = _make_uniform_image(value=128)
    assert score_exposure(dark) < score_exposure(mid)


def test_exposure_overexposed():
    """A very bright image should score lower."""
    bright = _make_uniform_image(value=250)
    mid = _make_uniform_image(value=128)
    assert score_exposure(bright) < score_exposure(mid)


# ── Face-weighted blur tests ──


def _make_bokeh_portrait(size: int = 400) -> np.ndarray:
    """Simulate a portrait: sharp face region on blurry background."""
    # Start with a very blurry background
    img = np.full((size, size, 3), 128, dtype=np.uint8)
    img = cv2.GaussianBlur(img, (31, 31), 15)
    # Paint a sharp checkerboard in the face region (center 100x100)
    cx, cy = size // 2, size // 2
    for y in range(cy - 50, cy + 50, 8):
        for x in range(cx - 50, cx + 50, 8):
            if ((x - cx + 50) // 8 + (y - cy + 50) // 8) % 2 == 0:
                img[y : y + 8, x : x + 8] = [220, 220, 220]
    return img


def test_face_weighted_blur_higher_than_global_for_portrait():
    """A portrait with sharp face + blurry bg should score higher with face weighting."""
    img = _make_bokeh_portrait()
    face_bbox = (150, 150, 100, 100)  # center 100x100 face

    global_only = score_blur_raw(img, faces=None)
    face_weighted = score_blur_raw(img, faces=[face_bbox])
    assert face_weighted > global_only


def test_no_faces_falls_back_to_global():
    """Without faces, score_blur_raw returns plain global Laplacian."""
    img = _make_sharp_image()
    assert score_blur_raw(img) == score_blur_raw(img, faces=None)
    assert score_blur_raw(img) == score_blur_raw(img, faces=[])


def test_face_region_laplacian_uses_sharpest_face():
    """With multiple faces, _face_region_laplacian returns the sharpest."""
    img = _make_bokeh_portrait()
    sharp_face = (150, 150, 100, 100)  # in the sharp checkerboard zone
    blurry_face = (10, 10, 40, 40)  # in the blurry background

    sharp_only = _face_region_laplacian(img, [sharp_face])
    both = _face_region_laplacian(img, [sharp_face, blurry_face])
    assert both == sharp_only  # max of both = the sharp one


def test_face_region_laplacian_skips_tiny_crops():
    """Face bboxes smaller than 10px are skipped."""
    img = _make_sharp_image(200)
    tiny_face = (50, 50, 5, 5)
    assert _face_region_laplacian(img, [tiny_face]) == 0.0


def test_face_bbox_padding_stays_in_bounds():
    """Face near image edge shouldn't cause array indexing errors."""
    img = _make_sharp_image(200)
    edge_face = (0, 0, 50, 50)  # top-left corner
    score = score_blur_raw(img, faces=[edge_face])
    assert score > 0


# ── Adaptive face coverage tests ──


def test_face_coverage_calculation():
    """Face coverage should be face area / image area."""
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    # 50x50 face = 25% of 100x100 image
    assert abs(_face_coverage(img, [(25, 25, 50, 50)]) - 0.25) < 0.001
    # Two faces
    assert abs(_face_coverage(img, [(0, 0, 10, 10), (50, 50, 10, 10)]) - 0.02) < 0.001
    # No faces
    assert _face_coverage(img, []) == 0.0


def test_large_face_gets_more_face_weight():
    """A close-up portrait (large face) should weight face sharpness more."""
    img = _make_bokeh_portrait(400)
    # Large face (25% of frame) → face_weight = min(1, 0.25*5) = 1.0
    large_face = (100, 100, 200, 200)
    # Small face (1% of frame) → face_weight = min(1, 0.01*5) = 0.05
    small_face = (190, 190, 20, 20)

    score_large = score_blur_raw(img, faces=[large_face])
    score_small = score_blur_raw(img, faces=[small_face])
    score_none = score_blur_raw(img, faces=None)

    # Large face should differ most from global (face region is sharper)
    assert abs(score_large - score_none) > abs(score_small - score_none)


# ── Log-sigmoid normalization tests ──


def test_log_sigmoid_monotonic():
    """Higher blur_raw should always produce higher blur_score."""
    from bpp.scoring.aggregate import _blur_log_sigmoid

    prev = 0.0
    for raw in [10, 50, 100, 200, 500, 1000, 5000]:
        score = _blur_log_sigmoid(raw)
        assert score > prev, f"raw={raw} scored {score} <= prev {prev}"
        prev = score


def test_log_sigmoid_midpoint():
    """blur_raw at midpoint (200) should score ~0.5."""
    from bpp.scoring.aggregate import _blur_log_sigmoid

    assert abs(_blur_log_sigmoid(200.0) - 0.5) < 0.01


def test_log_sigmoid_bounds():
    """Scores should be in (0, 1) and handle edge cases."""
    from bpp.scoring.aggregate import _blur_log_sigmoid

    assert _blur_log_sigmoid(0) == 0.0
    assert 0 < _blur_log_sigmoid(1) < 0.1
    assert _blur_log_sigmoid(100000) > 0.99


def test_normalize_blur_scores_absolute():
    """normalize_blur_scores should produce absolute scores, not relative."""
    from bpp.scoring.aggregate import normalize_blur_scores

    # Same photo in two different "datasets" should get the same score
    results_a = [{"blur_raw": 200}, {"blur_raw": 5000}]
    results_b = [{"blur_raw": 200}, {"blur_raw": 50}]
    normalize_blur_scores(results_a)
    normalize_blur_scores(results_b)
    # blur_raw=200 should score the same regardless of neighbors
    assert results_a[0]["blur_score"] == results_b[0]["blur_score"]

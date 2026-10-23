"""Comprehensive tests for recompute.py and composition.py — part 2 of 2: TestOptimize,
TestCompositionNoFaces, TestCompositionFaceCentered."""

from __future__ import annotations

import pytest

from bpp.scoring.composition import score_composition
from bpp.web.recompute import RecomputeOptions, optimize
from tests._models import skip_unless_on_disk
from tests._recompute_coverage_shared import _default_config, _make_analysis, _make_image

# ===========================================================================
# Optimize tests
# ===========================================================================


class TestOptimize:
    """Tests for the optimize() weight sweep."""

    def test_basic_optimize_returns_expected_keys(self):
        """optimize() returns settings dict with expected weight keys."""
        analysis = _make_analysis(20)
        config = _default_config()

        opts = RecomputeOptions(analysis=analysis, config=config, k=5, skip_dedupe=True)
        result = optimize(opts)

        assert "settings" in result
        assert "breakdown" in result
        settings = result["settings"]
        assert "blur_weight" in settings
        assert "exposure_weight" in settings
        assert "composition_weight" in settings
        assert "face_weight" in settings
        assert "face_selection_boost" in settings

        breakdown = result["breakdown"]
        assert "avg_quality" in breakdown
        assert "composite_score" in breakdown
        assert "total_selected" in breakdown

    def test_optimize_without_faces_no_face_coverage(self):
        """Without faces, breakdown should not have face_coverage."""
        analysis = _make_analysis(15)
        config = _default_config()

        opts = RecomputeOptions(analysis=analysis, config=config, k=5, skip_dedupe=True)
        result = optimize(opts)

        assert "face_coverage" not in result["breakdown"]

    def test_optimize_with_faces(self):
        """With faces, breakdown should include face_coverage."""
        analysis = _make_analysis(15)
        config = _default_config()

        face_cluster_map = {}
        face_filepaths = set()
        for i, item in enumerate(analysis):
            fp = item["filepath"]
            if i % 3 == 0:
                face_cluster_map[fp] = [1]
                face_filepaths.add(fp)

        opts = RecomputeOptions(
            analysis=analysis,
            config=config,
            k=5,
            selected_faces=[1],
            face_cluster_map=face_cluster_map,
            skip_dedupe=True,
        )
        result = optimize(opts, face_filepaths=face_filepaths)

        assert "face_coverage" in result["breakdown"]
        assert "face_photos_selected" in result["breakdown"]
        assert result["breakdown"]["face_coverage"] >= 0.0

    def test_optimize_composite_quality_positive(self):
        """The composite score from optimize should be positive for valid data."""
        analysis = _make_analysis(20)
        config = _default_config()

        opts = RecomputeOptions(analysis=analysis, config=config, k=5, skip_dedupe=True)
        result = optimize(opts)

        assert result["breakdown"]["composite_score"] > 0.0


# ===========================================================================
# Composition scoring tests
# ===========================================================================


class TestCompositionNoFaces:
    """score_composition with no faces."""

    @pytest.fixture(autouse=True)
    def _real_models_on_disk(self):
        skip_unless_on_disk("Selfie segmenter")  # real segmentation

    def test_no_faces_returns_neutral(self):
        """With no face_boxes, score should be 0.5."""
        img = _make_image(300, 400)
        assert score_composition(img, []) == pytest.approx(0.5)


class TestCompositionFaceCentered:
    """Face centered in the image."""

    def test_face_centered_high_score(self):
        """A face centered at (1/2, ~2/5) should score high (near rule-of-thirds)."""
        img = _make_image(300, 400)
        # Face at center: x=150, y=90 (center of face near 2/5 height), w=100, h=120
        # Face center: (200/400=0.5, 150/300=0.5) -- exactly at 1/2 for both
        face = (150, 90, 100, 120)  # center_x=200/400=0.5, center_y=150/300=0.5
        score = score_composition(img, [face])
        # Center aligns with 1/2 in both x and y thirds -> should be high
        assert score > 0.7


class TestCompositionRuleOfThirds:
    """Face placed at rule-of-thirds intersection."""

    def test_face_at_one_third(self):
        """A face at (1/3, 1/3) should score high."""
        h, w = 300, 400
        img = _make_image(h, w)
        # Target face center at (w/3, h/3) = (133.3, 100)
        face_w, face_h = 60, 80
        fx = int(w / 3 - face_w / 2)
        fy = int(h / 3 - face_h / 2)
        face = (fx, fy, face_w, face_h)
        score = score_composition(img, [face])
        assert score > 0.7


class TestCompositionCorner:
    """Face placed in extreme corner."""

    def test_face_at_corner_lower_score(self):
        """A face in the extreme bottom-right corner should score lower."""
        h, w = 300, 400
        img = _make_image(h, w)
        # Face in bottom-right corner
        face = (w - 40, h - 40, 30, 30)  # center near (385/400, 285/300) = (0.96, 0.95)
        score = score_composition(img, [face])
        # Far from any third -> low x_score and y_score
        assert score < 0.5


class TestCompositionHeadroom:
    """Face near top edge with low headroom."""

    def test_low_headroom_penalty(self):
        """A face very near the top edge should get a headroom penalty."""
        h, w = 300, 400
        img = _make_image(h, w)
        # Face touching top edge: fy=0 means face_top = 0/300 = 0.0
        # Put face center at x=1/2 to maximize x_score
        face_low_headroom = (150, 0, 100, 80)
        score_low = score_composition(img, [face_low_headroom])

        # Same face but with headroom: fy=60 -> face_top = 60/300 = 0.2
        face_good_headroom = (150, 60, 100, 80)
        score_good = score_composition(img, [face_good_headroom])

        # The one with no headroom should score lower
        assert score_low < score_good


class TestCompositionMultipleFaces:
    """Multiple faces: largest is used."""

    def test_uses_largest_face(self):
        """When multiple faces are present, the largest face determines score."""
        img = _make_image(300, 400)
        # Small face in corner (would give low score if used)
        small_face = (380, 270, 10, 10)
        # Large face at center (gives high score)
        large_face = (150, 90, 100, 120)

        score = score_composition(img, [small_face, large_face])
        # Should use large_face (area 12000 vs 100) -> high score
        assert score > 0.7


class TestCompositionEmptyImage:
    """Edge case: tiny image dimensions with a face box."""

    def test_tiny_image_with_face(self):
        """A 1x1 image with a face should not crash and return a valid score."""
        img = _make_image(1, 1)
        face = (0, 0, 1, 1)
        score = score_composition(img, [face])
        assert 0.0 <= score <= 1.0

    def test_small_image_with_face(self):
        """A small image with a face at centre should return valid score."""
        img = _make_image(10, 10)
        face = (2, 2, 6, 6)  # center at (5/10, 5/10) = (0.5, 0.5)
        score = score_composition(img, [face])
        assert 0.0 <= score <= 1.0
        # Center placement should yield decent score
        assert score > 0.5

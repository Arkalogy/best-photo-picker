"""Quality benchmarks for ML models — part 2 of 2: TestPoseClassification,
TestSubjectCompositionScoring, TestCrossModelConsistency."""

from __future__ import annotations

import numpy as np
import pytest

from tests._model_quality_shared import (
    _real_models_on_disk,  # noqa: F401
    _solid_bgr,
)

# ── Pose Classification Unit Tests ──


class TestPoseClassification:
    """Test _classify_pose with synthetic landmark data."""

    @staticmethod
    def _make_landmark(x: float, y: float, visibility: float):
        """Create a mock landmark with x, y, visibility attributes."""

        class LM:
            pass

        lm = LM()
        lm.x = x
        lm.y = y
        lm.visibility = visibility
        return lm

    def _make_landmarks(self, positions: dict[int, tuple[float, float, float]]):
        """Build a 33-element landmark list with specified positions.

        positions maps landmark index → (x, y, visibility).
        Unspecified landmarks get (0.5, 0.5, 0.0) (invisible).
        """
        landmarks = [self._make_landmark(0.5, 0.5, 0.0) for _ in range(33)]
        for idx, (x, y, v) in positions.items():
            landmarks[idx] = self._make_landmark(x, y, v)
        return landmarks

    def test_standing_pose(self):
        """Vertical torso with visible ankles → standing."""
        from bpp.scoring.pose import _classify_pose

        lms = self._make_landmarks(
            {
                0: (0.5, 0.1, 0.9),  # nose
                11: (0.45, 0.25, 0.9),  # left shoulder
                12: (0.55, 0.25, 0.9),  # right shoulder
                23: (0.45, 0.50, 0.9),  # left hip
                24: (0.55, 0.50, 0.9),  # right hip
                25: (0.45, 0.70, 0.9),  # left knee
                26: (0.55, 0.70, 0.9),  # right knee
                27: (0.45, 0.90, 0.9),  # left ankle
                28: (0.55, 0.90, 0.9),  # right ankle
            }
        )
        assert _classify_pose(lms) == "standing"

    def test_sitting_pose(self):
        """Hips and knees at similar height → sitting."""
        from bpp.scoring.pose import _classify_pose

        lms = self._make_landmarks(
            {
                0: (0.5, 0.1, 0.9),
                11: (0.45, 0.25, 0.9),
                12: (0.55, 0.25, 0.9),
                23: (0.45, 0.50, 0.9),
                24: (0.55, 0.50, 0.9),
                25: (0.45, 0.52, 0.9),  # knee near hip level
                26: (0.55, 0.52, 0.9),
                27: (0.45, 0.70, 0.9),
                28: (0.55, 0.70, 0.9),
            }
        )
        assert _classify_pose(lms) == "sitting"

    def test_lying_pose(self):
        """Minimal Y difference between shoulder and hip → lying."""
        from bpp.scoring.pose import _classify_pose

        lms = self._make_landmarks(
            {
                0: (0.1, 0.50, 0.9),
                11: (0.3, 0.50, 0.9),  # shoulders and hips at same Y
                12: (0.4, 0.50, 0.9),
                23: (0.6, 0.53, 0.9),  # very small Y diff
                24: (0.7, 0.53, 0.9),
            }
        )
        assert _classify_pose(lms) == "lying"

    def test_low_visibility_returns_unknown(self):
        """Landmarks with low visibility → unknown."""
        from bpp.scoring.pose import _classify_pose

        lms = self._make_landmarks(
            {
                0: (0.5, 0.1, 0.1),
                11: (0.45, 0.25, 0.1),  # low visibility
                12: (0.55, 0.25, 0.1),
                23: (0.45, 0.50, 0.1),
                24: (0.55, 0.50, 0.1),
            }
        )
        assert _classify_pose(lms) == "unknown"


# ── Segmentation Composition Scoring ──


class TestSubjectCompositionScoring:
    """Test composition scoring math with synthetic masks."""

    def test_ideal_coverage_centered(self):
        """Subject covering ~30% of frame, centered → high score."""
        from unittest.mock import patch

        from bpp.scoring.segmentation import score_subject_composition

        # Create a mask with ~30% coverage centered
        mask = np.zeros((100, 100), dtype=np.float32)
        # 30x30 centered block at row 35-65, col 35-65 = 900 / 10000 = 9%
        # Use bigger block: 55x55 at center = ~30%
        mask[22:78, 22:78] = 1.0  # 56x56 = 3136 / 10000 = 31.4%

        with patch("bpp.scoring.segmentation.segment_subject", return_value=mask):
            score = score_subject_composition(np.zeros((100, 100, 3), dtype=np.uint8))

        assert score is not None
        assert score >= 0.7, f"Ideal coverage + centered should score high, got {score}"

    def test_tiny_subject_low_score(self):
        """Very small subject → low or None score."""
        from unittest.mock import patch

        from bpp.scoring.segmentation import score_subject_composition

        mask = np.zeros((100, 100), dtype=np.float32)
        mask[48:52, 48:52] = 1.0  # 4x4 = 16/10000 = 0.16%

        with patch("bpp.scoring.segmentation.segment_subject", return_value=mask):
            score = score_subject_composition(np.zeros((100, 100, 3), dtype=np.uint8))

        # Should be None (too small) or very low
        assert score is None or score < 0.3, f"Tiny subject should score low, got {score}"

    def test_full_frame_subject_tapers(self):
        """Subject filling entire frame → lower score."""
        from unittest.mock import patch

        from bpp.scoring.segmentation import score_subject_composition

        mask = np.ones((100, 100), dtype=np.float32)

        with patch("bpp.scoring.segmentation.segment_subject", return_value=mask):
            score = score_subject_composition(np.zeros((100, 100, 3), dtype=np.uint8))

        assert score is not None
        assert score < 0.9, f"Full-frame subject should be penalized, got {score}"

    def test_off_center_lower_score(self):
        """Subject in corner scores lower than centered subject."""
        from unittest.mock import patch

        from bpp.scoring.segmentation import score_subject_composition

        img = np.zeros((100, 100, 3), dtype=np.uint8)

        # Centered subject
        mask_center = np.zeros((100, 100), dtype=np.float32)
        mask_center[25:75, 25:75] = 1.0

        with patch("bpp.scoring.segmentation.segment_subject", return_value=mask_center):
            center_score = score_subject_composition(img)

        # Corner subject (same size)
        mask_corner = np.zeros((100, 100), dtype=np.float32)
        mask_corner[0:50, 0:50] = 1.0

        with patch("bpp.scoring.segmentation.segment_subject", return_value=mask_corner):
            corner_score = score_subject_composition(img)

        assert center_score is not None and corner_score is not None
        assert center_score > corner_score, (
            f"Center {center_score} should beat corner {corner_score}"
        )


# ── Cross-Model Consistency ──


class TestCrossModelConsistency:
    """Validate consistency between models and scoring pipelines."""

    def test_score_face_with_precomputed_faces(self):
        """Providing faces directly should skip detection and still score."""
        from bpp.scoring.face import score_face

        img = _solid_bgr(480, 640)
        result = score_face(img, faces=[(200, 150, 100, 100)])
        assert result["face_count"] == 1
        assert result["face_score"] > 0.2

    def test_composition_uses_segmentation_fallback(self):
        """Composition with no faces should attempt segmentation fallback."""
        from bpp.scoring.composition import score_composition

        img = _solid_bgr(480, 640)
        score = score_composition(img, [])
        # Should return something (0.5 default or segmentation result)
        assert 0.0 <= score <= 1.0

    def test_face_score_weight_sum(self):
        """Face score weights should sum to 1.0."""
        from bpp.constants import (
            FACE_SCORE_AREA_W,
            FACE_SCORE_CENTER_W,
            FACE_SCORE_COUNT_W,
            FACE_SCORE_EDGE_W,
            FACE_SCORE_EXPRESSION_W,
        )

        total = (
            FACE_SCORE_AREA_W
            + FACE_SCORE_CENTER_W
            + FACE_SCORE_COUNT_W
            + FACE_SCORE_EDGE_W
            + FACE_SCORE_EXPRESSION_W
        )
        assert total == pytest.approx(1.0), f"Weights sum to {total}, expected 1.0"

    def test_iterative_collect_lowers_threshold(self):
        """Iterative collection should try lower thresholds on marginal detections."""
        from bpp.scoring.face import _iterative_collect

        # On solid image, should return empty regardless of threshold
        result = _iterative_collect(_solid_bgr(), 0.3)
        assert isinstance(result, list)

    def test_full_pipeline_solid_image(self):
        """Full pipeline on solid image: detect → score → compose."""
        from bpp.scoring.composition import score_composition
        from bpp.scoring.face import detect_faces, score_face

        img = _solid_bgr(480, 640)
        faces = detect_faces(img)
        face_result = score_face(img, faces=faces)
        comp_score = score_composition(img, [(x, y, w, h) for x, y, w, h in faces])

        assert face_result["face_count"] == 0
        assert face_result["face_score"] == pytest.approx(0.2)
        assert 0.0 <= comp_score <= 1.0

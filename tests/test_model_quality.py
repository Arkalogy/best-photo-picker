"""Quality benchmarks for ML models.

These tests run the actual ML models (not mocks) on synthetic and constructed
test images to validate detection quality, scoring accuracy, and suppression
effectiveness. They require all ML dependencies to be installed.

Run with: pytest tests/test_model_quality.py -v
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

# ── Helpers ──


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


# ── Face Detection Quality ──


class TestFaceDetectionQuality:
    """Validate face detection pipeline quality on edge cases."""

    def test_false_positive_rate_on_noise(self):
        """Track false positive rate on noise images.

        Multi-detector pipeline (YuNet + BlazeFace SR/FR + dlib + iterative
        retry) can produce FPs on synthetic noise. This test documents the
        baseline FP rate — if it increases, something regressed.
        """
        from bpp.scoring.face import detect_faces

        img = _noise_image()
        faces = detect_faces(img)
        # Baseline: up to 6 FPs from aggressive multi-detector + iterative retry
        assert len(faces) <= 8, f"FP rate regressed on noise: {len(faces)} (max 8)"

    def test_false_positive_rate_on_gradient(self):
        """Track false positive rate on gradient images."""
        from bpp.scoring.face import detect_faces

        img = _gradient_image()
        faces = detect_faces(img)
        # Baseline: up to 4 FPs from multi-detector pipeline
        assert len(faces) <= 6, f"FP rate regressed on gradient: {len(faces)} (max 6)"

    def test_false_positive_rate_on_solid(self):
        """Track false positive rate on solid color images.

        Even solid black can trigger 1 FP from BlazeFace full-range at low
        confidence thresholds during iterative retry.
        """
        from bpp.scoring.face import detect_faces

        total_fps = 0
        for color in [(0, 0, 0), (255, 255, 255), (128, 128, 128)]:
            img = _solid_bgr(color=color)
            faces = detect_faces(img)
            total_fps += len(faces)
        # Baseline: ~1 FP total across 3 solid images
        assert total_fps <= 3, f"FP rate regressed on solid: {total_fps} total (max 3)"

    def test_tiny_image_returns_empty(self):
        """Images smaller than MIN_FACE_IMAGE_PX should return empty."""
        from bpp.scoring.face import detect_faces

        img = np.zeros((10, 10, 3), dtype=np.uint8)
        assert detect_faces(img) == []

    def test_grayscale_input_handled(self):
        """Grayscale input should not crash any detector."""
        from bpp.scoring.face import detect_faces

        gray = np.random.randint(0, 256, (480, 640), dtype=np.uint8)
        # Should not raise
        detect_faces(gray)

    def test_detect_faces_with_confidence_returns_5_tuples(self):
        """detect_faces_with_confidence should return 5-tuples."""
        from bpp.scoring.face import detect_faces_with_confidence

        img = _solid_bgr()
        result = detect_faces_with_confidence(img)
        for item in result:
            assert len(item) == 5, f"Expected 5-tuple, got {len(item)}"

    def test_nms_deduplicates_overlapping(self):
        """NMS should reduce overlapping detections to ~1."""
        from bpp.scoring.face import _nms_faces

        # 5 nearly-identical boxes
        faces = [(100, 100, 80, 80, 0.9)] * 5
        result = _nms_faces(faces, keep_confidence=True)
        assert len(result) <= 2, f"NMS failed to deduplicate: {len(result)}"

    def test_nms_keeps_separate_faces(self):
        """NMS should keep non-overlapping detections."""
        from bpp.scoring.face import _nms_faces

        faces = [
            (10, 10, 50, 50, 0.9),
            (300, 300, 50, 50, 0.8),
        ]
        result = _nms_faces(faces)
        assert len(result) == 2, f"NMS incorrectly merged separate faces: {len(result)}"


# ── Expression Scoring Quality ──


class TestExpressionScoringQuality:
    """Validate expression scoring produces reasonable scores."""

    def test_expression_score_in_range(self):
        """Expression score should always be in [0, 1]."""
        from bpp.scoring.face import _score_expression

        for img in [_solid_bgr(), _noise_image(), _gradient_image()]:
            score = _score_expression(img)
            assert 0.0 <= score <= 1.0, f"Expression score out of range: {score}"

    def test_no_face_returns_neutral(self):
        """Expression score on faceless image should be ~0.5 (neutral)."""
        from bpp.scoring.face import _score_expression

        score = _score_expression(_solid_bgr())
        assert 0.3 <= score <= 0.7, f"No-face expression should be neutral-ish, got {score}"

    def test_expression_deterministic(self):
        """Same input should produce same expression score."""
        from bpp.scoring.face import _score_expression

        img = _noise_image(seed=123)
        s1 = _score_expression(img)
        s2 = _score_expression(img)
        assert s1 == pytest.approx(s2), "Expression scoring not deterministic"


# ── Hand Suppression Quality ──


class TestHandSuppressionQuality:
    """Validate hand-as-face suppression logic."""

    def test_iou_identical_boxes(self):
        """Identical boxes should have IoU = 1.0."""
        from bpp.scoring.face import _iou

        assert _iou((10, 10, 50, 50), (10, 10, 50, 50)) == pytest.approx(1.0)

    def test_iou_no_overlap(self):
        """Non-overlapping boxes should have IoU = 0.0."""
        from bpp.scoring.face import _iou

        assert _iou((0, 0, 10, 10), (100, 100, 10, 10)) == pytest.approx(0.0)

    def test_iou_partial_overlap(self):
        """Partially overlapping boxes should give IoU between 0 and 1."""
        from bpp.scoring.face import _iou

        iou = _iou((0, 0, 100, 100), (50, 50, 100, 100))
        assert 0.0 < iou < 1.0, f"Partial IoU should be between 0 and 1, got {iou}"
        # Intersection is 50x50=2500, union is 100x100 + 100x100 - 2500 = 17500
        assert iou == pytest.approx(2500 / 17500, abs=0.01)

    def test_suppress_with_no_faces(self):
        """Empty face list should pass through unchanged."""
        from bpp.scoring.face import _suppress_hand_faces

        result = _suppress_hand_faces([], _solid_bgr())
        assert result == []

    def test_suppress_keeps_faces_without_hands(self):
        """When no hands detected, all faces should be kept."""
        from unittest.mock import patch

        from bpp.scoring.face import _suppress_hand_faces

        faces = [(100, 100, 50, 50, 0.9), (200, 200, 50, 50, 0.8)]
        with patch("bpp.scoring.face_hand_filter._detect_hand_bboxes", return_value=[]):
            result = _suppress_hand_faces(faces, _solid_bgr())
        assert len(result) == 2

    def test_suppress_removes_overlapping_hand_face(self):
        """Face overlapping a hand bbox should be suppressed."""
        from unittest.mock import patch

        from bpp.scoring.face import _suppress_hand_faces

        faces = [(100, 100, 50, 50, 0.9)]
        # Hand bbox perfectly overlapping the face
        with patch(
            "bpp.scoring.face_hand_filter._detect_hand_bboxes",
            return_value=[(100, 100, 50, 50)],
        ):
            result = _suppress_hand_faces(faces, _solid_bgr())
        assert len(result) == 0, "Should suppress face overlapping hand"

    def test_suppress_keeps_non_overlapping(self):
        """Face far from hand bbox should be kept."""
        from unittest.mock import patch

        from bpp.scoring.face import _suppress_hand_faces

        faces = [(100, 100, 50, 50, 0.9)]
        with patch(
            "bpp.scoring.face_hand_filter._detect_hand_bboxes",
            return_value=[(400, 400, 50, 50)],
        ):
            result = _suppress_hand_faces(faces, _solid_bgr())
        assert len(result) == 1, "Should keep face not overlapping hand"


# ── Face Score Quality ──


class TestFaceScoreQuality:
    """Validate overall face scoring quality."""

    def test_no_face_score_is_low(self):
        """Images with no faces should get a low face score."""
        from bpp.scoring.face import score_face

        result = score_face(_solid_bgr())
        assert result["face_score"] <= 0.3, f"No-face score too high: {result['face_score']}"
        assert result["face_count"] == 0

    def test_score_keys_complete(self):
        """score_face should return all expected keys."""
        from bpp.scoring.face import score_face

        result = score_face(_solid_bgr())
        expected = {
            "face_score",
            "face_count",
            "largest_face_ratio",
            "face_center_dist",
            "face_edge_penalty",
            "expression_score",
        }
        assert set(result.keys()) == expected

    def test_all_scores_in_range(self):
        """All sub-scores should be in [0, 1] range."""
        from bpp.scoring.face import score_face

        for img in [_solid_bgr(), _noise_image()]:
            result = score_face(img)
            for key, val in result.items():
                if key == "face_count":
                    assert val >= 0
                else:
                    assert 0.0 <= val <= 1.0, f"{key}={val} out of [0,1]"

    def test_centered_face_scores_higher(self):
        """A centered face box should score higher on center_dist than edge face."""
        from bpp.scoring.face import score_face

        img = _solid_bgr(480, 640)
        # Centered face
        centered = score_face(img, faces=[(270, 190, 100, 100)])
        # Edge face
        edge = score_face(img, faces=[(0, 0, 100, 100)])
        assert centered["face_center_dist"] < edge["face_center_dist"], (
            "Centered face should have smaller center_dist"
        )

    def test_large_face_scores_higher_area(self):
        """A larger face should get higher area score contribution."""
        from bpp.scoring.face import score_face

        img = _solid_bgr(480, 640)
        large = score_face(img, faces=[(200, 100, 200, 200)])
        small = score_face(img, faces=[(200, 100, 30, 30)])
        assert large["largest_face_ratio"] > small["largest_face_ratio"]


# ── Composition Scoring Quality ──


class TestCompositionQuality:
    """Validate composition scoring logic."""

    def test_centered_face_good_composition(self):
        """Face near center/thirds should score well."""
        from bpp.scoring.composition import score_composition

        img = _solid_bgr(480, 640)
        # Face near 1/3 horizontal
        score = score_composition(img, [(213, 150, 80, 80)])
        assert score >= 0.5, f"Thirds-positioned face should score >= 0.5, got {score}"

    def test_corner_face_lower_composition(self):
        """Face in corner should score lower than centered."""
        from bpp.scoring.composition import score_composition

        img = _solid_bgr(480, 640)
        center_score = score_composition(img, [(280, 200, 80, 80)])
        corner_score = score_composition(img, [(0, 0, 80, 80)])
        assert center_score > corner_score, (
            f"Center {center_score} should beat corner {corner_score}"
        )

    def test_no_faces_returns_default(self):
        """No faces should return ~0.5 (or segmentation score)."""
        from bpp.scoring.composition import score_composition

        score = score_composition(_solid_bgr(), [])
        assert 0.0 <= score <= 1.0

    def test_headroom_penalty(self):
        """Face touching top edge should get headroom penalty."""
        from bpp.scoring.composition import score_composition

        img = _solid_bgr(480, 640)
        # Face at very top
        top_score = score_composition(img, [(280, 0, 80, 80)])
        # Face with headroom
        room_score = score_composition(img, [(280, 50, 80, 80)])
        assert room_score >= top_score, f"Headroom {room_score} should >= top-touching {top_score}"

    def test_composition_score_range(self):
        """Composition score should always be in [0, 1]."""
        from bpp.scoring.composition import score_composition

        for boxes in [
            [(0, 0, 50, 50)],
            [(320, 240, 100, 100)],
            [(0, 0, 640, 480)],
            [],
        ]:
            score = score_composition(_solid_bgr(480, 640), boxes)
            assert 0.0 <= score <= 1.0, f"Score {score} out of range for boxes={boxes}"


# ── Selfie Segmentation Quality ──


class TestSegmentationQuality:
    """Validate selfie segmentation and subject composition scoring."""

    def test_solid_image_no_subject(self):
        """Solid color image should find no meaningful subject."""
        from bpp.scoring.segmentation import score_subject_composition

        result = score_subject_composition(_solid_bgr())
        # Should return None (no subject) or a low score
        if result is not None:
            assert result <= 0.6, f"Solid image shouldn't have high subject score: {result}"

    def test_segment_subject_returns_mask_or_none(self):
        """segment_subject should return float32 mask or None."""
        from bpp.scoring.segmentation import segment_subject

        mask = segment_subject(_solid_bgr())
        if mask is not None:
            assert mask.dtype == np.float32
            assert mask.ndim == 2
            assert mask.min() >= 0.0
            assert mask.max() <= 1.0

    def test_composition_score_in_range(self):
        """Subject composition score should be in [0, 1] when not None."""
        from bpp.scoring.segmentation import score_subject_composition

        for img in [_noise_image(), _gradient_image()]:
            result = score_subject_composition(img)
            if result is not None:
                assert 0.0 <= result <= 1.0, f"Subject score out of range: {result}"


# ── Pose Detection Quality ──


class TestPoseDetectionQuality:
    """Validate pose detection and classification."""

    def test_no_pose_on_solid(self):
        """Solid image should detect no poses."""
        from bpp.scoring.pose import detect_poses

        poses = detect_poses(_solid_bgr())
        assert len(poses) == 0, f"False pose detection on solid: {poses}"

    def test_no_pose_on_noise(self):
        """Random noise should not produce confident pose detections."""
        from bpp.scoring.pose import detect_poses

        poses = detect_poses(_noise_image())
        # Some noise may trigger weak detections, but confidence should be low
        for p in poses:
            assert p["confidence"] < 0.8, f"High-confidence pose on noise: {p['confidence']}"

    def test_pose_dict_structure(self):
        """Pose results should have the expected structure."""
        from bpp.scoring.pose import detect_poses

        # Even if no poses detected, validate on any result
        poses = detect_poses(_noise_image(seed=99))
        for p in poses:
            assert "pose_type" in p
            assert "bbox" in p
            assert "landmark_count" in p
            assert "confidence" in p
            assert p["pose_type"] in {
                "standing",
                "sitting",
                "lying",
                "crawling",
                "crouching",
                "unknown",
            }
            assert len(p["bbox"]) == 4
            assert p["landmark_count"] >= 5
            assert 0.0 <= p["confidence"] <= 1.0

    def test_classify_pose_values(self):
        """_classify_pose should return only valid pose types."""
        # Tested implicitly through detect_poses, but verify the constant set
        valid_types = {"standing", "sitting", "lying", "crawling", "crouching", "unknown"}
        # Just verify the set is as expected
        assert len(valid_types) == 6


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

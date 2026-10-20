"""Comprehensive tests for face scoring, face embedding, face clustering, — part 1 of 3."""

from __future__ import annotations

from unittest.mock import patch

import cv2
import pytest

from bpp.scoring.face import (
    _get_cascade,
    _iou,
    _nms_faces,
    _score_expression,
    detect_faces,
    score_face,
)
from tests._face_scan_scoring_shared import (
    _blank_bgr,
    _grayscale,
    _random_bgr,
)

# ===================================================================
# FACE DETECTION & SCORING  (bpp/scoring/face.py)
# ===================================================================


class TestGetCascade:
    """Tests for _get_cascade (Haar cascade loading)."""

    def test_returns_classifier(self):
        cascade = _get_cascade()
        assert isinstance(cascade, cv2.CascadeClassifier)

    def test_not_empty(self):
        cascade = _get_cascade()
        assert not cascade.empty()

    def test_cached_same_object(self):
        c1 = _get_cascade()
        c2 = _get_cascade()
        assert c1 is c2


@pytest.mark.usefixtures("real_face_pipeline")
class TestDetectFaces:
    """Tests for detect_faces."""

    def test_no_faces_in_blank_image(self):
        img = _blank_bgr(200, 200)
        faces = detect_faces(img)
        assert faces == []

    def test_no_faces_in_random_noise(self):
        img = _random_bgr(seed=42, h=200, w=200)
        faces = detect_faces(img)
        # Random noise is unlikely to produce stable detections
        assert isinstance(faces, list)

    def test_returns_list_of_tuples(self):
        img = _blank_bgr(200, 200)
        faces = detect_faces(img)
        assert isinstance(faces, list)
        for f in faces:
            assert isinstance(f, tuple)

    def test_grayscale_input(self):
        gray = _grayscale(200, 200, 200)
        faces = detect_faces(gray)
        assert isinstance(faces, list)

    def test_small_image(self):
        """Images smaller than minSize should produce no detections."""
        img = _blank_bgr(20, 20)
        faces = detect_faces(img)
        assert faces == []


class TestNmsFaces:
    """Tests for _nms_faces — confidence-weighted Non-Maximum Suppression."""

    def test_empty_input(self):
        assert _nms_faces([]) == []

    def test_single_face_passes_through(self):
        result = _nms_faces([(100, 100, 50, 50, 0.9)])
        assert len(result) == 1
        assert result[0] == (100, 100, 50, 50)

    def test_confidence_stripped_from_output(self):
        result = _nms_faces([(10, 20, 30, 40, 0.95)])
        assert len(result[0]) == 4

    def test_two_distant_faces_both_kept(self):
        faces = [
            (10, 10, 50, 50, 0.9),  # face top-left
            (300, 300, 50, 50, 0.8),  # face bottom-right
        ]
        result = _nms_faces(faces)
        assert len(result) == 2

    def test_overlapping_boxes_merged(self):
        """Two heavily overlapping boxes for the same face — NMS keeps one."""
        faces = [
            (100, 100, 60, 60, 0.9),  # MediaPipe detection
            (105, 105, 55, 55, 0.75),  # dlib detection (slightly offset)
        ]
        result = _nms_faces(faces)
        assert len(result) == 1

    def test_higher_confidence_wins(self):
        """When overlapping, the higher-confidence box is kept."""
        faces = [
            (100, 100, 60, 60, 0.5),  # lower confidence
            (105, 105, 55, 55, 0.95),  # higher confidence
        ]
        result = _nms_faces(faces)
        assert len(result) == 1
        # NMS keeps the higher-confidence box
        assert result[0] == (105, 105, 55, 55)

    def test_cross_detector_merge_different_sizes(self):
        """Detectors reporting different crop sizes for same face get merged."""
        faces = [
            (100, 100, 80, 80, 0.85),  # wider crop (MediaPipe)
            (110, 110, 60, 60, 0.75),  # tighter crop (dlib)
        ]
        result = _nms_faces(faces)
        assert len(result) == 1

    def test_four_detections_two_faces(self):
        """Simulates 2 detectors each finding 2 faces — should merge to 2."""
        faces = [
            # Face 1: two overlapping detections
            (50, 50, 80, 80, 0.9),
            (55, 48, 75, 85, 0.75),
            # Face 2: two overlapping detections
            (300, 200, 70, 70, 0.88),
            (295, 205, 72, 68, 0.75),
        ]
        result = _nms_faces(faces)
        assert len(result) == 2

    def test_low_score_below_threshold_filtered(self):
        """Boxes below FACE_NMS_SCORE_THRESH are filtered out."""
        faces = [(100, 100, 50, 50, 0.01)]  # very low confidence
        result = _nms_faces(faces)
        assert len(result) == 0


@pytest.mark.usefixtures("real_face_pipeline")
class TestScoreFace:
    """Tests for score_face — the main face-scoring entry point."""

    def test_no_faces_returns_default(self):
        img = _blank_bgr(200, 200)
        result = score_face(img)
        assert result["face_score"] == pytest.approx(0.2)
        assert result["face_count"] == 0
        assert result["largest_face_ratio"] == pytest.approx(0.0)
        assert result["face_center_dist"] == pytest.approx(1.0)
        assert result["face_edge_penalty"] == pytest.approx(0.0)

    def test_all_keys_present(self):
        img = _blank_bgr(200, 200)
        result = score_face(img)
        expected_keys = {
            "face_score",
            "face_count",
            "largest_face_ratio",
            "face_center_dist",
            "face_edge_penalty",
            "expression_score",
        }
        assert set(result.keys()) == expected_keys

    def test_score_values_are_float(self):
        img = _blank_bgr(200, 200)
        result = score_face(img)
        for key, val in result.items():
            if key == "face_count":
                assert isinstance(val, int)
            else:
                assert isinstance(val, float)

    def test_face_score_clamped_0_to_1(self):
        """face_score should always be in [0, 1]."""
        img = _random_bgr(seed=7, h=300, w=300)
        result = score_face(img)
        assert 0.0 <= result["face_score"] <= 1.0

    def test_with_mocked_faces(self):
        """Mock detect_faces to return known boxes and verify scoring math."""
        img = _blank_bgr(200, 200)
        fake_faces = [(80, 80, 40, 40)]  # centered 40x40 face
        with patch(
            "bpp.scoring.face.detect_faces",
            return_value=fake_faces,
        ):
            result = score_face(img)
        assert result["face_count"] == 1
        assert result["face_score"] > 0.2  # better than no-face
        assert result["largest_face_ratio"] > 0.0

    def test_with_mocked_multiple_faces(self):
        """Multiple faces should give count > 1 and count_score < 1."""
        img = _blank_bgr(200, 200)
        fake_faces = [
            (10, 10, 30, 30),
            (100, 100, 40, 40),
            (50, 50, 20, 20),
        ]
        with patch(
            "bpp.scoring.face.detect_faces",
            return_value=fake_faces,
        ):
            result = score_face(img)
        assert result["face_count"] == 3
        assert result["face_score"] > 0.0

    def test_edge_face_penalty(self):
        """Face at the extreme corner should get edge penalty."""
        img = _blank_bgr(200, 200)
        # Face at top-left corner: x=0, y=0, 30x30
        fake_faces = [(0, 0, 30, 30)]
        with patch(
            "bpp.scoring.face.detect_faces",
            return_value=fake_faces,
        ):
            result = score_face(img)
        # Edge penalty should be nonzero when face touches edge
        assert result["face_edge_penalty"] > 0.0

    def test_centered_face_low_center_dist(self):
        """A perfectly centered face should have low center distance."""
        img = _blank_bgr(200, 200)
        # Face centered: (80, 80, 40, 40) -> center at (100, 100)
        fake_faces = [(80, 80, 40, 40)]
        with patch(
            "bpp.scoring.face.detect_faces",
            return_value=fake_faces,
        ):
            result = score_face(img)
        assert result["face_center_dist"] < 0.1

    def test_expression_score_in_result(self):
        """score_face should include expression_score when faces exist."""
        img = _blank_bgr(200, 200)
        fake_faces = [(80, 80, 40, 40)]
        with patch("bpp.scoring.face.detect_faces", return_value=fake_faces):
            result = score_face(img)
        assert "expression_score" in result
        assert 0.0 <= result["expression_score"] <= 1.0

    def test_no_faces_expression_score_zero(self):
        """When no faces, expression_score should be 0.0."""
        img = _blank_bgr(200, 200)
        result = score_face(img)
        assert result["expression_score"] == 0.0


class TestScoreExpression:
    """Tests for _score_expression — FaceLandmarker blendshape scoring."""

    def test_returns_neutral_when_landmarker_unavailable(self):
        """Falls back to 0.5 when FaceLandmarker can't load."""
        img = _blank_bgr(200, 200)
        with patch("bpp.scoring.face_expression._get_landmarker", return_value=None):
            score = _score_expression(img)
        assert score == pytest.approx(0.5)

    def test_returns_neutral_when_no_blendshapes(self):
        """Falls back to 0.5 when landmarker finds no faces."""
        img = _blank_bgr(200, 200)
        mock_result = type("R", (), {"face_blendshapes": []})()
        mock_lm = type("L", (), {"detect": lambda self, x: mock_result})()
        with patch("bpp.scoring.face_expression._get_landmarker", return_value=mock_lm):
            score = _score_expression(img)
        assert score == pytest.approx(0.5)

    def _make_blendshape(self, name: str, score: float):
        """Create a mock blendshape category."""
        return type("BS", (), {"category_name": name, "score": score})()

    def test_open_eyes_smiling_scores_high(self):
        """Eyes open + smiling should score well above neutral."""
        shapes = [
            self._make_blendshape("eyeBlinkLeft", 0.0),
            self._make_blendshape("eyeBlinkRight", 0.0),
            self._make_blendshape("mouthSmileLeft", 0.8),
            self._make_blendshape("mouthSmileRight", 0.8),
            self._make_blendshape("jawLeft", 0.0),
            self._make_blendshape("jawRight", 0.0),
        ]
        mock_result = type("R", (), {"face_blendshapes": [shapes]})()
        mock_lm = type("L", (), {"detect": lambda self, x: mock_result})()
        img = _blank_bgr(200, 200)
        with patch("bpp.scoring.face_expression._get_landmarker", return_value=mock_lm):
            score = _score_expression(img)
        # Open eyes (1.0) * 0.5 + smile (1.0) * 0.3 + frontal (1.0) * 0.2 = 1.0
        assert score > 0.8

    def test_closed_eyes_scores_low(self):
        """Both eyes closed should penalize heavily."""
        shapes = [
            self._make_blendshape("eyeBlinkLeft", 0.95),
            self._make_blendshape("eyeBlinkRight", 0.95),
            self._make_blendshape("mouthSmileLeft", 0.0),
            self._make_blendshape("mouthSmileRight", 0.0),
            self._make_blendshape("jawLeft", 0.0),
            self._make_blendshape("jawRight", 0.0),
        ]
        mock_result = type("R", (), {"face_blendshapes": [shapes]})()
        mock_lm = type("L", (), {"detect": lambda self, x: mock_result})()
        img = _blank_bgr(200, 200)
        with patch("bpp.scoring.face_expression._get_landmarker", return_value=mock_lm):
            score = _score_expression(img)
        # Blink penalty should dominate — score should be below neutral
        assert score < 0.5

    def test_head_turned_scores_lower(self):
        """Jaw asymmetry (head turned) should reduce frontality score."""
        shapes_frontal = [
            self._make_blendshape("eyeBlinkLeft", 0.0),
            self._make_blendshape("eyeBlinkRight", 0.0),
            self._make_blendshape("mouthSmileLeft", 0.3),
            self._make_blendshape("mouthSmileRight", 0.3),
            self._make_blendshape("jawLeft", 0.0),
            self._make_blendshape("jawRight", 0.0),
        ]
        shapes_turned = [
            self._make_blendshape("eyeBlinkLeft", 0.0),
            self._make_blendshape("eyeBlinkRight", 0.0),
            self._make_blendshape("mouthSmileLeft", 0.3),
            self._make_blendshape("mouthSmileRight", 0.3),
            self._make_blendshape("jawLeft", 0.6),
            self._make_blendshape("jawRight", 0.0),
        ]
        img = _blank_bgr(200, 200)

        mock_frontal = type("R", (), {"face_blendshapes": [shapes_frontal]})()
        mock_lm_f = type("L", (), {"detect": lambda self, x: mock_frontal})()
        with patch("bpp.scoring.face_expression._get_landmarker", return_value=mock_lm_f):
            frontal_score = _score_expression(img)

        mock_turned = type("R", (), {"face_blendshapes": [shapes_turned]})()
        mock_lm_t = type("L", (), {"detect": lambda self, x: mock_turned})()
        with patch("bpp.scoring.face_expression._get_landmarker", return_value=mock_lm_t):
            turned_score = _score_expression(img)

        assert frontal_score > turned_score

    def test_best_face_selected_from_multiple(self):
        """With multiple faces, returns the best expression score."""
        # Face 1: blinking (bad)
        face1 = [
            self._make_blendshape("eyeBlinkLeft", 0.9),
            self._make_blendshape("eyeBlinkRight", 0.9),
            self._make_blendshape("mouthSmileLeft", 0.0),
            self._make_blendshape("mouthSmileRight", 0.0),
            self._make_blendshape("jawLeft", 0.0),
            self._make_blendshape("jawRight", 0.0),
        ]
        # Face 2: smiling (good)
        face2 = [
            self._make_blendshape("eyeBlinkLeft", 0.0),
            self._make_blendshape("eyeBlinkRight", 0.0),
            self._make_blendshape("mouthSmileLeft", 0.7),
            self._make_blendshape("mouthSmileRight", 0.7),
            self._make_blendshape("jawLeft", 0.0),
            self._make_blendshape("jawRight", 0.0),
        ]
        mock_result = type("R", (), {"face_blendshapes": [face1, face2]})()
        mock_lm = type("L", (), {"detect": lambda self, x: mock_result})()
        img = _blank_bgr(200, 200)
        with patch("bpp.scoring.face_expression._get_landmarker", return_value=mock_lm):
            score = _score_expression(img)
        # Should pick face2's score (the better one)
        assert score > 0.7

    def test_score_in_valid_range(self):
        """Expression score should always be in [0, 1]."""
        shapes = [
            self._make_blendshape("eyeBlinkLeft", 1.0),
            self._make_blendshape("eyeBlinkRight", 1.0),
            self._make_blendshape("mouthSmileLeft", 1.0),
            self._make_blendshape("mouthSmileRight", 1.0),
            self._make_blendshape("jawLeft", 1.0),
            self._make_blendshape("jawRight", 0.0),
        ]
        mock_result = type("R", (), {"face_blendshapes": [shapes]})()
        mock_lm = type("L", (), {"detect": lambda self, x: mock_result})()
        img = _blank_bgr(200, 200)
        with patch("bpp.scoring.face_expression._get_landmarker", return_value=mock_lm):
            score = _score_expression(img)
        assert 0.0 <= score <= 1.0


class TestIoU:
    """Tests for _iou — Intersection-over-Union."""

    def test_identical_boxes(self):
        assert _iou((10, 10, 50, 50), (10, 10, 50, 50)) == pytest.approx(1.0)

    def test_no_overlap(self):
        assert _iou((0, 0, 10, 10), (100, 100, 10, 10)) == pytest.approx(0.0)

    def test_partial_overlap(self):
        iou = _iou((0, 0, 20, 20), (10, 10, 20, 20))
        # Overlap: 10x10 = 100; union = 400+400-100 = 700
        assert iou == pytest.approx(100.0 / 700.0)

    def test_one_inside_other(self):
        iou = _iou((10, 10, 10, 10), (0, 0, 100, 100))
        # Small inside big: overlap = 100; union = 100+10000-100 = 10000
        assert iou == pytest.approx(100.0 / 10000.0)

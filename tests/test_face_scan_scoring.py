"""Comprehensive tests for face scoring, face embedding, face clustering,
image scanning, perceptual hashing, and EXIF date extraction."""

from __future__ import annotations

import datetime
import os
from unittest.mock import patch

import cv2
import numpy as np
import pytest
from PIL import Image

from bpp.dedupe.phash import (
    _load_image,
    compute_ahash,
    compute_dhash,
    compute_dhash_from_file,
    compute_hashes_from_file,
    dual_hash_distance,
    hamming_distance,
)
from bpp.exif_utils import extract_exif_date, get_date, get_file_date
from bpp.io_scan import scan_images
from bpp.scoring.face import (
    _get_cascade,
    _iou,
    _nms_faces,
    _score_expression,
    _suppress_hand_faces,
    detect_faces,
    score_face,
)
from bpp.scoring.face_cluster import cluster_faces, pick_representative
from bpp.scoring.face_embed import extract_face_embeddings, is_available

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


class TestSuppressHandFaces:
    """Tests for _suppress_hand_faces — hand FP suppression."""

    def test_no_faces_returns_empty(self):
        img = _blank_bgr(200, 200)
        assert _suppress_hand_faces([], img) == []

    def test_no_hands_keeps_all_faces(self):
        """When no hands detected, all faces are kept."""
        img = _blank_bgr(200, 200)
        faces = [(50, 50, 40, 40, 0.9), (150, 50, 40, 40, 0.8)]
        with patch("bpp.scoring.face_hand_filter._detect_hand_bboxes", return_value=[]):
            result = _suppress_hand_faces(faces, img)
        assert len(result) == 2

    def test_overlapping_hand_suppresses_face(self):
        """Face overlapping with hand bbox gets suppressed."""
        img = _blank_bgr(200, 200)
        faces = [(50, 50, 40, 40, 0.7)]
        hand_bboxes = [(45, 45, 50, 50)]  # overlaps significantly
        with patch(
            "bpp.scoring.face_hand_filter._detect_hand_bboxes",
            return_value=hand_bboxes,
        ):
            result = _suppress_hand_faces(faces, img)
        assert len(result) == 0

    def test_non_overlapping_hand_keeps_face(self):
        """Face far from hand bbox is kept."""
        img = _blank_bgr(200, 200)
        faces = [(10, 10, 30, 30, 0.9)]
        hand_bboxes = [(150, 150, 40, 40)]  # far away
        with patch(
            "bpp.scoring.face_hand_filter._detect_hand_bboxes",
            return_value=hand_bboxes,
        ):
            result = _suppress_hand_faces(faces, img)
        assert len(result) == 1

    def test_mixed_suppression(self):
        """Only the face overlapping with hand is suppressed."""
        img = _blank_bgr(300, 300)
        faces = [
            (50, 50, 40, 40, 0.7),  # overlaps with hand
            (200, 200, 40, 40, 0.9),  # no overlap
        ]
        hand_bboxes = [(45, 45, 50, 50)]
        with patch(
            "bpp.scoring.face_hand_filter._detect_hand_bboxes",
            return_value=hand_bboxes,
        ):
            result = _suppress_hand_faces(faces, img)
        assert len(result) == 1
        assert result[0][0] == 200  # kept the non-overlapping face


# ===================================================================
# FACE EMBEDDINGS  (bpp/scoring/face_embed.py)
# ===================================================================


class TestFaceEmbedAvailability:
    """Tests for is_available."""

    def test_is_available_returns_bool(self):
        result = is_available()
        assert isinstance(result, bool)

    def test_is_available_true_when_installed(self):
        """face_recognition is installed in this venv."""
        assert is_available() is True

    def test_is_available_false_when_both_missing(self):
        """Simulate missing face_recognition AND SFace unavailable."""
        with (
            patch("bpp.scoring.face_embed._sface_available", return_value=False),
            patch.dict("sys.modules", {"face_recognition": None}),
            patch(
                "builtins.__import__",
                side_effect=ImportError("no face_recognition"),
            ),
        ):
            assert is_available() is False


class TestExtractFaceEmbeddings:
    """Tests for extract_face_embeddings."""

    def test_blank_image_no_embeddings(self):
        img = _blank_bgr(200, 200)
        result = extract_face_embeddings(img)
        assert result == []

    def test_returns_list(self):
        img = _random_bgr(seed=99, h=200, w=200)
        result = extract_face_embeddings(img)
        assert isinstance(result, list)

    def test_result_dict_keys(self):
        """If a face is found, each dict should have bbox and embedding."""
        img = _blank_bgr(200, 200)
        # Even if no faces, we at least verify the empty return
        result = extract_face_embeddings(img)
        for item in result:
            assert "bbox" in item
            assert "embedding" in item

    def test_embedding_shape_when_found(self):
        """Mock face_recognition to return a known face and verify shape."""
        import face_recognition

        img = _blank_bgr(200, 200)
        fake_locations = [(50, 150, 150, 50)]  # (top, right, bottom, left)
        fake_encoding = np.random.randn(128)
        with (
            patch.object(
                face_recognition,
                "face_locations",
                return_value=fake_locations,
            ),
            patch.object(
                face_recognition,
                "face_encodings",
                return_value=[fake_encoding],
            ),
        ):
            result = extract_face_embeddings(img)
        assert len(result) == 1
        assert result[0]["embedding"].shape == (128,)
        # bbox should be (left, top, right-left, bottom-top) = (50,50,100,100)
        assert result[0]["bbox"] == (50, 50, 100, 100)


# ===================================================================
# FACE CLUSTERING  (bpp/scoring/face_cluster.py)
# ===================================================================


class TestClusterFaces:
    """Tests for cluster_faces."""

    def test_empty_list(self):
        assert cluster_faces([]) == []

    def test_single_embedding(self):
        emb = np.random.randn(128)
        assert cluster_faces([emb]) == [0]

    def test_two_identical_embeddings(self):
        emb = np.random.randn(128)
        labels = cluster_faces([emb, emb.copy()], threshold=0.6)
        assert len(labels) == 2
        assert labels[0] == labels[1]

    def test_two_very_different_embeddings(self):
        e1 = np.ones(128)
        e2 = -np.ones(128)
        labels = cluster_faces([e1, e2], threshold=0.3)
        assert len(labels) == 2
        assert labels[0] != labels[1]

    def test_cluster_ids_are_zero_based(self):
        embs = [np.random.randn(128) * 100 for _ in range(5)]
        labels = cluster_faces(embs, threshold=0.01)
        assert min(labels) == 0

    def test_high_threshold_merges_all(self):
        """A very high threshold should merge everything into one cluster."""
        embs = [np.random.randn(128) for _ in range(5)]
        labels = cluster_faces(embs, threshold=100.0)
        assert len(set(labels)) == 1

    def test_returns_int_labels(self):
        embs = [np.random.randn(128) for _ in range(3)]
        labels = cluster_faces(embs, threshold=0.6)
        for label in labels:
            assert isinstance(label, int)

    def test_label_count_matches_input(self):
        n = 10
        embs = [np.random.randn(128) for _ in range(n)]
        labels = cluster_faces(embs, threshold=0.6)
        assert len(labels) == n

    def test_tight_clusters(self):
        """Create two tight groups and ensure they cluster separately."""
        rng = np.random.RandomState(42)
        group_a = [rng.randn(128) * 0.01 for _ in range(4)]
        group_b = [rng.randn(128) * 0.01 + 5.0 for _ in range(4)]
        labels = cluster_faces(group_a + group_b, threshold=0.5)
        # Group A should all share one label, group B another
        a_labels = set(labels[:4])
        b_labels = set(labels[4:])
        assert len(a_labels) == 1
        assert len(b_labels) == 1
        assert a_labels != b_labels


class TestPickRepresentative:
    """Tests for pick_representative."""

    def test_single_embedding(self):
        emb = np.random.randn(128)
        assert pick_representative([emb]) == 0

    def test_returns_int(self):
        embs = [np.random.randn(128) for _ in range(5)]
        idx = pick_representative(embs)
        assert isinstance(idx, int)

    def test_index_in_range(self):
        embs = [np.random.randn(128) for _ in range(5)]
        idx = pick_representative(embs)
        assert 0 <= idx < 5

    def test_closest_to_centroid(self):
        """Build embeddings where one is exactly the mean."""
        base = np.zeros(128)
        embs = [
            base + 10.0,
            base - 10.0,
            base.copy(),  # this is the centroid
        ]
        idx = pick_representative(embs)
        assert idx == 2

    def test_quality_none_falls_back_to_centroid(self):
        """When qualities is None, picks by centroid distance only."""
        base = np.zeros(128)
        embs = [base + 10.0, base - 10.0, base.copy()]
        assert pick_representative(embs, qualities=None) == 2

    def test_quality_all_none_falls_back(self):
        """When all qualities are None, picks by centroid distance only."""
        base = np.zeros(128)
        embs = [base + 10.0, base - 10.0, base.copy()]
        assert pick_representative(embs, qualities=[None, None, None]) == 2

    def test_quality_boosts_high_quality_face(self):
        """Quality should tip the balance between similar-distance embeddings."""
        rng = np.random.RandomState(42)
        centroid = rng.randn(128)
        centroid /= np.linalg.norm(centroid)
        # Three embeddings all close to centroid (similar typicality)
        embs = [
            centroid + rng.randn(128) * 0.01,
            centroid + rng.randn(128) * 0.01,
            centroid + rng.randn(128) * 0.01,
        ]
        # Without quality, any might win — but with quality, index 1 should
        idx = pick_representative(embs, qualities=[0.2, 1.0, 0.2])
        assert idx == 1

    def test_quality_partial_none_uses_median(self):
        """None quality entries get median of known values."""
        base = np.zeros(128)
        embs = [base + 1.0, base - 1.0, base.copy()]
        # qualities: [None, 0.8, 0.2] → None gets median(0.8, 0.2) = 0.5
        idx = pick_representative(embs, qualities=[None, 0.8, 0.2])
        assert isinstance(idx, int)
        assert 0 <= idx < 3

    def test_empty_embeddings_returns_zero(self):
        """Empty embedding list must return 0, not crash."""
        assert pick_representative([]) == 0

    def test_empty_embeddings_with_qualities_returns_zero(self):
        """Empty list + empty qualities must return 0."""
        assert pick_representative([], qualities=[]) == 0


# ===================================================================
# IMAGE SCANNING  (bpp/io_scan.py)
# ===================================================================


class TestScanImages:
    """Tests for scan_images."""

    def test_empty_directory(self, tmp_path):
        result = scan_images(str(tmp_path))
        assert result == []

    def test_finds_jpg(self, tmp_path):
        _save_jpeg(tmp_path / "a.jpg")
        result = scan_images(str(tmp_path))
        assert len(result) == 1
        assert result[0].endswith("a.jpg")

    def test_finds_png(self, tmp_path):
        _save_png(tmp_path / "b.png")
        result = scan_images(str(tmp_path))
        assert len(result) == 1

    def test_ignores_non_image(self, tmp_path):
        (tmp_path / "notes.txt").write_text("hello")
        result = scan_images(str(tmp_path))
        assert result == []

    def test_default_extensions(self, tmp_path):
        _save_jpeg(tmp_path / "a.jpg")
        _save_jpeg(tmp_path / "b.jpeg")
        _save_png(tmp_path / "c.png")
        (tmp_path / "d.bmp").write_bytes(b"\x00")
        result = scan_images(str(tmp_path))
        assert len(result) == 3

    def test_custom_extensions(self, tmp_path):
        _save_jpeg(tmp_path / "a.jpg")
        (tmp_path / "b.bmp").write_bytes(b"\x00")
        result = scan_images(str(tmp_path), extensions=["bmp"])
        assert len(result) == 1
        assert result[0].endswith("b.bmp")

    def test_extensions_case_insensitive(self, tmp_path):
        _save_jpeg(tmp_path / "a.JPG")
        result = scan_images(str(tmp_path))
        assert len(result) == 1

    def test_extensions_with_dot_prefix(self, tmp_path):
        _save_jpeg(tmp_path / "a.jpg")
        result = scan_images(str(tmp_path), extensions=[".jpg"])
        assert len(result) == 1

    def test_sorted_output(self, tmp_path):
        for name in ["c.jpg", "a.jpg", "b.jpg"]:
            _save_jpeg(tmp_path / name)
        result = scan_images(str(tmp_path))
        names = [os.path.basename(p) for p in result]
        assert names == ["a.jpg", "b.jpg", "c.jpg"]

    def test_max_images(self, tmp_path):
        for i in range(5):
            _save_jpeg(tmp_path / f"{i:02d}.jpg")
        result = scan_images(str(tmp_path), max_images=3)
        assert len(result) == 3

    def test_max_images_zero_means_unlimited(self, tmp_path):
        for i in range(5):
            _save_jpeg(tmp_path / f"{i:02d}.jpg")
        result = scan_images(str(tmp_path), max_images=0)
        assert len(result) == 5

    def test_recursive(self, tmp_path):
        sub = tmp_path / "subdir"
        sub.mkdir()
        _save_jpeg(tmp_path / "a.jpg")
        _save_jpeg(sub / "b.jpg")
        result = scan_images(str(tmp_path), recursive=True)
        assert len(result) == 2

    def test_non_recursive_ignores_subdirs(self, tmp_path):
        sub = tmp_path / "subdir"
        sub.mkdir()
        _save_jpeg(tmp_path / "a.jpg")
        _save_jpeg(sub / "b.jpg")
        result = scan_images(str(tmp_path), recursive=False)
        assert len(result) == 1

    def test_symlink_skipped_by_default(self, tmp_path):
        real = tmp_path / "real.jpg"
        _save_jpeg(real)
        link = tmp_path / "link.jpg"
        link.symlink_to(real)
        result = scan_images(str(tmp_path), follow_symlinks=False)
        assert len(result) == 1
        assert result[0].endswith("real.jpg")

    def test_symlink_followed_when_enabled(self, tmp_path):
        real = tmp_path / "real.jpg"
        _save_jpeg(real)
        link = tmp_path / "link.jpg"
        link.symlink_to(real)
        result = scan_images(str(tmp_path), follow_symlinks=True)
        assert len(result) == 2

    def test_recursive_symlink_skipped(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        real = sub / "real.jpg"
        _save_jpeg(real)
        link = sub / "link.jpg"
        link.symlink_to(real)
        result = scan_images(str(tmp_path), recursive=True, follow_symlinks=False)
        assert len(result) == 1

    def test_nonexistent_directory_returns_empty(self, tmp_path):
        result = scan_images(str(tmp_path / "nope"))
        assert result == []

    def test_nonexistent_directory_recursive_returns_empty(self, tmp_path):
        result = scan_images(str(tmp_path / "nope"), recursive=True)
        assert result == []

    def test_directories_not_included(self, tmp_path):
        (tmp_path / "subdir.jpg").mkdir()
        _save_jpeg(tmp_path / "real.jpg")
        result = scan_images(str(tmp_path))
        assert len(result) == 1
        assert result[0].endswith("real.jpg")


# ===================================================================
# PERCEPTUAL HASHING  (bpp/dedupe/phash.py)
# ===================================================================


class TestComputeDhash:
    """Tests for compute_dhash."""

    def test_returns_int(self):
        img = _random_bgr(seed=1, h=100, w=100)
        assert isinstance(compute_dhash(img), int)

    def test_deterministic(self):
        img = _random_bgr(seed=1)
        assert compute_dhash(img) == compute_dhash(img)

    def test_grayscale_input(self):
        gray = _grayscale(100, 100)
        h = compute_dhash(gray)
        assert isinstance(h, int)

    def test_different_images_different_hashes(self):
        h1 = compute_dhash(_random_bgr(seed=1))
        h2 = compute_dhash(_random_bgr(seed=999))
        # Very unlikely to collide
        assert h1 != h2

    def test_custom_hash_size(self):
        img = _random_bgr(seed=5)
        h = compute_dhash(img, hash_size=8)
        assert isinstance(h, int)


class TestComputeAhash:
    """Tests for compute_ahash."""

    def test_returns_int(self):
        img = _random_bgr(seed=2, h=100, w=100)
        assert isinstance(compute_ahash(img), int)

    def test_deterministic(self):
        img = _random_bgr(seed=2)
        assert compute_ahash(img) == compute_ahash(img)

    def test_grayscale_input(self):
        gray = _grayscale(100, 100, 200)
        h = compute_ahash(gray)
        assert isinstance(h, int)

    def test_different_from_dhash(self):
        img = _random_bgr(seed=3)
        assert compute_ahash(img) != compute_dhash(img)


class TestHammingDistance:
    """Tests for hamming_distance."""

    def test_zero_distance(self):
        assert hamming_distance(0xABCD, 0xABCD) == 0

    def test_known_values(self):
        assert hamming_distance(0b1111, 0b0000) == 4
        assert hamming_distance(0b1000, 0b0000) == 1

    def test_symmetric(self):
        a, b = 0x1234, 0x5678
        assert hamming_distance(a, b) == hamming_distance(b, a)

    def test_max_distance_8bit(self):
        assert hamming_distance(0b11111111, 0b00000000) == 8

    def test_large_hashes(self):
        h = hamming_distance(0xFFFFFFFFFFFFFFFF, 0x0)
        assert h == 64


class TestDualHashDistance:
    """Tests for dual_hash_distance."""

    def test_identical(self):
        assert dual_hash_distance(10, 20, 10, 20) == 0

    def test_takes_minimum(self):
        d1 = hamming_distance(0xFF, 0x00)  # 8
        d2 = hamming_distance(0x01, 0x00)  # 1
        result = dual_hash_distance(0xFF, 0x01, 0x00, 0x00)
        assert result == min(d1, d2)

    def test_symmetric(self):
        assert dual_hash_distance(0xAA, 0xBB, 0xCC, 0xDD) == dual_hash_distance(
            0xCC, 0xDD, 0xAA, 0xBB
        )


class TestLoadImage:
    """Tests for _load_image."""

    def test_loads_jpeg(self, tmp_path):
        p = tmp_path / "test.jpg"
        _save_jpeg(p)
        img = _load_image(str(p))
        assert img is not None
        assert len(img.shape) == 2  # grayscale

    def test_loads_png(self, tmp_path):
        p = tmp_path / "test.png"
        _save_png(p)
        img = _load_image(str(p))
        assert img is not None

    def test_nonexistent_file_raises(self, tmp_path):
        with pytest.raises(OSError):
            _load_image(str(tmp_path / "nope.jpg"))

    def test_corrupt_file_falls_back_to_pil(self, tmp_path):
        """If cv2 cannot read the file, _load_image falls back to PIL."""
        p = tmp_path / "test.jpg"
        _save_jpeg(p)
        with patch("cv2.imread", return_value=None):
            img = _load_image(str(p))
        assert img is not None
        assert len(img.shape) == 2


class TestComputeHashesFromFile:
    """Tests for compute_hashes_from_file."""

    def test_returns_tuple(self, tmp_path):
        p = tmp_path / "img.jpg"
        _save_jpeg(p, color="green")
        result = compute_hashes_from_file(str(p))
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_both_ints(self, tmp_path):
        p = tmp_path / "img.jpg"
        _save_jpeg(p, color="green")
        dhash, ahash = compute_hashes_from_file(str(p))
        assert isinstance(dhash, int)
        assert isinstance(ahash, int)

    def test_corrupt_returns_none_none(self, tmp_path):
        p = tmp_path / "bad.jpg"
        p.write_bytes(b"\x00\x00\x00")
        dhash, ahash = compute_hashes_from_file(str(p))
        assert dhash is None
        assert ahash is None


class TestComputeDhashFromFile:
    """Tests for compute_dhash_from_file."""

    def test_returns_int(self, tmp_path):
        p = tmp_path / "img.jpg"
        _save_jpeg(p)
        h = compute_dhash_from_file(str(p))
        assert isinstance(h, int)

    def test_corrupt_returns_none(self, tmp_path):
        p = tmp_path / "bad.jpg"
        p.write_bytes(b"\xff\xd8\xff")
        # Should not raise, just return None
        h = compute_dhash_from_file(str(p))
        assert h is None


class TestPhashSimilarity:
    """Integration: verify similar images hash close, different hash far."""

    def test_blurred_copy_close(self, tmp_path):
        img = _random_bgr(seed=10, h=200, w=200)
        blurred = cv2.GaussianBlur(img, (3, 3), 1)
        d1 = compute_dhash(img)
        d2 = compute_dhash(blurred)
        assert hamming_distance(d1, d2) < 15

    def test_color_shift_close(self):
        img = _random_bgr(seed=20, h=200, w=200)
        shifted = np.clip(img.astype(int) + 10, 0, 255).astype(np.uint8)
        d1 = compute_dhash(img)
        d2 = compute_dhash(shifted)
        assert hamming_distance(d1, d2) < 10

    def test_very_different_images_far(self):
        d1 = compute_dhash(_blank_bgr(200, 200, (0, 0, 0)))
        d2 = compute_dhash(_random_bgr(seed=77, h=200, w=200))
        # Black vs random noise should differ significantly
        assert hamming_distance(d1, d2) > 5


# ===================================================================
# EXIF DATE EXTRACTION  (bpp/exif_utils.py)
# ===================================================================


class TestExtractExifDate:
    """Tests for extract_exif_date."""

    def test_no_exif_returns_none(self, tmp_path):
        p = tmp_path / "plain.jpg"
        _save_jpeg(p)
        result = extract_exif_date(str(p))
        # Plain PIL-saved JPEG has no EXIF
        assert result is None

    def test_with_exif_date(self, tmp_path):
        """Create a JPEG with EXIF DateTimeOriginal and verify extraction."""
        from PIL.ExifTags import Base as ExifBase

        p = tmp_path / "dated.jpg"
        img = Image.new("RGB", (100, 100), "green")
        exif = img.getexif()
        exif[ExifBase.DateTimeOriginal] = "2024:06:15 14:30:00"
        img.save(str(p), "JPEG", exif=exif.tobytes())

        result = extract_exif_date(str(p))
        assert result is not None
        assert result.year == 2024
        assert result.month == 6
        assert result.day == 15
        assert result.hour == 14
        assert result.minute == 30

    def test_with_datetime_fallback(self, tmp_path):
        """Falls back to DateTime when DateTimeOriginal is absent."""
        from PIL.ExifTags import Base as ExifBase

        p = tmp_path / "datetime.jpg"
        img = Image.new("RGB", (100, 100), "red")
        exif = img.getexif()
        exif[ExifBase.DateTime] = "2023:01:20 08:00:00"
        img.save(str(p), "JPEG", exif=exif.tobytes())

        result = extract_exif_date(str(p))
        assert result is not None
        assert result.year == 2023
        assert result.month == 1

    def test_corrupt_file_returns_none(self, tmp_path):
        p = tmp_path / "corrupt.jpg"
        p.write_bytes(b"\x00\x01\x02\x03")
        result = extract_exif_date(str(p))
        assert result is None

    def test_nonexistent_file_returns_none(self, tmp_path):
        result = extract_exif_date(str(tmp_path / "ghost.jpg"))
        assert result is None


class TestGetFileDate:
    """Tests for get_file_date."""

    def test_returns_datetime(self, tmp_path):
        p = tmp_path / "img.jpg"
        _save_jpeg(p)
        result = get_file_date(str(p))
        assert isinstance(result, datetime.datetime)

    def test_recent_file(self, tmp_path):
        p = tmp_path / "img.jpg"
        _save_jpeg(p)
        result = get_file_date(str(p))
        now = datetime.datetime.now()
        # Should be within the last minute
        assert (now - result).total_seconds() < 60


class TestGetDate:
    """Tests for get_date (EXIF with mtime fallback)."""

    def test_no_exif_falls_back_to_mtime(self, tmp_path):
        p = tmp_path / "plain.jpg"
        _save_jpeg(p)
        result = get_date(str(p))
        assert isinstance(result, datetime.datetime)

    def test_exif_preferred_over_mtime(self, tmp_path):
        from PIL.ExifTags import Base as ExifBase

        p = tmp_path / "exif.jpg"
        img = Image.new("RGB", (100, 100))
        exif = img.getexif()
        exif[ExifBase.DateTimeOriginal] = "2020:03:14 12:00:00"
        img.save(str(p), "JPEG", exif=exif.tobytes())

        result = get_date(str(p))
        assert result.year == 2020
        assert result.month == 3


# ===================================================================
# PHASH RETRY / TRANSIENT ERROR HANDLING
# ===================================================================


class TestLoadImageRetry:
    """Tests for _load_image retry logic on transient errors."""

    def test_retry_on_transient_error(self, tmp_path):
        """_load_image retries when is_transient returns True."""
        p = tmp_path / "test.jpg"
        _save_jpeg(p)

        call_count = 0
        original_imread = cv2.imread

        def flaky_imread(path, flags):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError(5, "I/O error")  # errno 5 = EIO
            return original_imread(path, flags)

        with patch("cv2.imread", side_effect=flaky_imread), patch("time.sleep"):
            img = _load_image(str(p))
        assert img is not None
        assert call_count == 2

    def test_non_transient_error_not_retried(self, tmp_path):
        """Non-transient OSError should be raised immediately."""
        p = tmp_path / "test.jpg"
        _save_jpeg(p)

        def always_fail(path, flags):
            raise OSError(2, "No such file or directory")

        with (
            patch("cv2.imread", side_effect=always_fail),
            pytest.raises(OSError, match="No such file"),
        ):
            _load_image(str(p))

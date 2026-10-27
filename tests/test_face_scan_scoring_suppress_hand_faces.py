"""Comprehensive tests for face scoring, face embedding, face clustering, — part 2 of 3:
TestSuppressHandFaces, TestFaceEmbedAvailability, TestExtractFaceEmbeddings."""

from __future__ import annotations

import os
from unittest.mock import patch

import numpy as np
import pytest

from bpp.dedupe.phash import (
    compute_ahash,
    compute_dhash,
)
from bpp.io_scan import scan_images
from bpp.scoring.face import (
    _suppress_hand_faces,
)
from bpp.scoring.face_cluster import cluster_faces, pick_representative
from bpp.scoring.face_embed import extract_face_embeddings, is_available
from tests._face_scan_scoring_shared import (
    _blank_bgr,
    _grayscale,
    _random_bgr,
    _save_jpeg,
    _save_png,
)


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
            patch(
                "bpp.scoring.face_recognition_loader.load_face_recognition",
                return_value=None,
            ),
        ):
            assert is_available() is False


@pytest.mark.usefixtures("real_face_pipeline")
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
            result = extract_face_embeddings(img, method="dlib")
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

"""Tests for YuNet face detection and SFace face recognition pipeline.

Validates that the OpenCV-based face detection (YuNet) and recognition
(SFace) pipeline produces correct results, and that the embedding version
migration handles method transitions correctly.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import numpy as np
import pytest

from bpp.scoring.face import _yunet_detect, _yunet_detect_raw
from bpp.scoring.face_embed import (
    EMBEDDING_METHOD_DLIB,
    EMBEDDING_METHOD_SFACE,
    SFACE_DISTANCE_SCALE,
    _extract_sface,
    _validate_face_landmarks,
    embedding_method,
    extract_face_embeddings,
)


def _blank_bgr(h: int = 200, w: int = 200):
    """Create a blank BGR image (no faces)."""
    return np.zeros((h, w, 3), dtype=np.uint8)


class TestYuNetDetector:
    """Tests for the YuNet face detector."""

    def test_no_faces_in_blank_image(self):
        """YuNet should find no faces in a solid-color image."""
        img = _blank_bgr()
        faces = _yunet_detect(img, min_confidence=0.5)
        assert faces == []

    def test_returns_tuples_with_confidence(self):
        """Each YuNet detection should be a 5-tuple (x, y, w, h, conf)."""
        # Mock the detector to return a known face
        fake_faces = np.array([[10, 20, 50, 60, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.9]])
        with patch("bpp.scoring.face_yunet._get_yunet_detector") as mock:
            mock_det = mock.return_value
            mock_det.detect.return_value = (1, fake_faces)
            faces = _yunet_detect(_blank_bgr(), min_confidence=0.5)
        assert len(faces) == 1
        x, y, w, h, conf = faces[0]
        assert (x, y, w, h) == (10, 20, 50, 60)
        assert conf == pytest.approx(0.9)

    def test_confidence_filter(self):
        """Detections below min_confidence should be filtered out."""
        fake_faces = np.array(
            [
                [10, 20, 50, 60, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.9],
                [100, 100, 40, 40, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.3],
            ]
        )
        with patch("bpp.scoring.face_yunet._get_yunet_detector") as mock:
            mock_det = mock.return_value
            mock_det.detect.return_value = (2, fake_faces)
            faces = _yunet_detect(_blank_bgr(), min_confidence=0.5)
        assert len(faces) == 1
        assert faces[0][-1] == pytest.approx(0.9)

    def test_detector_unavailable(self):
        """Returns empty list when YuNet detector is unavailable."""
        with patch("bpp.scoring.face_yunet._get_yunet_detector", return_value=None):
            faces = _yunet_detect(_blank_bgr())
        assert faces == []


class TestYuNetDetectRaw:
    """Tests for _yunet_detect_raw (used by SFace alignment)."""

    def test_returns_none_when_no_faces(self):
        """Should return None for blank images."""
        result = _yunet_detect_raw(_blank_bgr(), min_confidence=0.5)
        assert result is None

    def test_returns_filtered_array(self):
        """Should return numpy array filtered by confidence."""
        fake_faces = np.array(
            [
                [10, 20, 50, 60, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 0.9],
                [100, 100, 40, 40, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 0.3],
            ]
        )
        with patch("bpp.scoring.face_yunet._get_yunet_detector") as mock:
            mock_det = mock.return_value
            mock_det.detect.return_value = (2, fake_faces)
            result = _yunet_detect_raw(_blank_bgr(), min_confidence=0.5)
        assert result is not None
        assert len(result) == 1
        assert result[0, -1] == pytest.approx(0.9)


class TestSFaceEmbedding:
    """Tests for SFace embedding extraction."""

    def test_embedding_shape_and_scale(self):
        """SFace embeddings should be 128-d with norm = SFACE_DISTANCE_SCALE."""
        # Create a mock SFace pipeline
        fake_faces = np.array(
            [
                [10, 20, 50, 60, 30, 35, 45, 35, 37, 50, 30, 55, 45, 55, 0.9],
            ]
        )
        fake_emb = np.random.randn(1, 128).astype(np.float32)

        # Detection now goes through detect_face_rows (full pipeline + per-face
        # landmark recovery). Mock it to isolate the SFace embedding step.
        with (
            patch("bpp.scoring.face_embed._get_sface_recognizer") as mock_rec,
            patch(
                "bpp.scoring.face_embed_detect.detect_face_rows",
                return_value=list(fake_faces),
            ),
        ):
            rec = mock_rec.return_value
            rec.alignCrop.return_value = np.zeros((112, 112, 3), dtype=np.uint8)
            rec.feature.return_value = fake_emb

            results = _extract_sface(_blank_bgr(), min_confidence=0.3)

        assert results is not None
        assert len(results) == 1
        emb = results[0]["embedding"]
        assert emb.shape == (128,)
        assert emb.dtype == np.float32
        assert np.linalg.norm(emb) == pytest.approx(SFACE_DISTANCE_SCALE, abs=1e-6)

    def test_sface_returns_none_when_unavailable(self):
        """_extract_sface returns None when SFace model is missing."""
        with patch("bpp.scoring.face_embed._get_sface_recognizer", return_value=None):
            result = _extract_sface(_blank_bgr(), 0.3)
        assert result is None

    def test_sface_filters_low_confidence(self):
        """Direct-YuNet faces below the embedding-confidence gate are dropped.

        The gate now lives in detect_face_rows (the shared full-coverage
        detector). With the wider pipeline mocked empty, only the strong
        direct detection should survive.
        """
        from bpp.scoring.face_embed_detect import detect_face_rows

        fake_faces = np.array(
            [
                [10, 20, 50, 60, 30, 35, 45, 35, 37, 50, 30, 55, 45, 55, 0.9],
                [100, 100, 40, 40, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.3],
            ]
        )

        with (
            patch("bpp.scoring.face_yunet._yunet_detect_raw", return_value=fake_faces),
            patch(
                "bpp.scoring.face.detect_faces_with_confidence",
                return_value=[],
            ),
        ):
            rows = detect_face_rows(_blank_bgr(), min_confidence=0.3, embedding_confidence=0.65)

        # Only the 0.9-confidence face clears the 0.65 gate.
        assert len(rows) == 1
        assert float(rows[0][-1]) == pytest.approx(0.9)

    def test_blank_image_no_embeddings(self):
        """Blank images should produce no embeddings."""
        results = extract_face_embeddings(_blank_bgr(), min_confidence=0.3)
        assert results == []


class TestSFaceDistanceCalibration:
    """Verify SFace distance scaling matches dlib's threshold range."""

    def test_same_person_below_threshold(self):
        """Scaled SFace same-person distance should be in dlib-compatible range.

        Real SFace same-person L2 (normalized) ~0.8, scaled by 0.65 → ~0.52.
        """
        # Directly verify the math: L2 distance 0.8 * scale = 0.52
        raw_same_person_dist = 0.80
        scaled = raw_same_person_dist * SFACE_DISTANCE_SCALE
        assert scaled < 0.55, f"Scaled same-person distance {scaled:.3f} should be < 0.55"

    def test_different_person_above_threshold(self):
        """Two SFace embeddings of different people should have distance > 0.55."""
        rng = np.random.RandomState(42)
        emb1 = rng.randn(128)
        emb1 = emb1 / np.linalg.norm(emb1) * SFACE_DISTANCE_SCALE

        emb2 = rng.randn(128)
        emb2 = emb2 / np.linalg.norm(emb2) * SFACE_DISTANCE_SCALE

        dist = np.linalg.norm(emb1 - emb2)
        assert dist > 0.55, f"Different person distance {dist:.3f} should be > 0.55"


class TestEmbeddingMethod:
    """Tests for embedding method detection."""

    def test_sface_method_when_available(self):
        """Should return 'sface' when SFace recognizer is available."""
        with patch("bpp.scoring.face_embed._get_sface_recognizer") as mock:
            mock.return_value = object()  # non-None = available
            assert embedding_method() == EMBEDDING_METHOD_SFACE

    def test_dlib_method_when_sface_unavailable(self):
        """Should return 'dlib' when SFace is not available."""
        with patch("bpp.scoring.face_embed._get_sface_recognizer", return_value=None):
            assert embedding_method() == EMBEDDING_METHOD_DLIB


class TestEmbeddingVersionMigration:
    """Tests for embedding version tracking and migration in face_worker."""

    @pytest.fixture()
    def db(self, tmp_path):
        """Create a test database with tables needed by face_worker."""
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute(
            "CREATE TABLE photos ("
            "  id INTEGER PRIMARY KEY, filepath TEXT UNIQUE, missing INTEGER DEFAULT 0,"
            "  deleted_at TEXT, hidden_at TEXT)"
        )
        conn.execute(
            "CREATE TABLE face_embeddings ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  photo_id INTEGER, face_index INTEGER,"
            "  bbox_x INTEGER, bbox_y INTEGER, bbox_w INTEGER, bbox_h INTEGER,"
            "  embedding BLOB, cluster_id INTEGER DEFAULT -1, quality REAL,"
            "  FOREIGN KEY (photo_id) REFERENCES photos(id))"
        )
        # Tables needed by _remap_names_and_tags
        conn.execute(
            "CREATE TABLE albums ("
            "  id INTEGER PRIMARY KEY, name TEXT, album_type TEXT, rule_json TEXT)"
        )
        conn.execute(
            "CREATE TABLE album_photos ("
            "  album_id INTEGER, photo_id INTEGER,"
            "  PRIMARY KEY (album_id, photo_id))"
        )
        conn.execute(
            "CREATE TABLE photo_person_tags ("
            "  photo_id INTEGER, cluster_id INTEGER, created_at TEXT,"
            "  PRIMARY KEY (photo_id, cluster_id))"
        )
        conn.commit()
        return conn

    def test_first_run_stores_method(self, db):
        """First run should store the embedding method without wiping."""
        from bpp.web.face_worker import extract_and_cluster_faces

        db.execute("INSERT INTO photos VALUES (1, '/test.jpg', 0, NULL, NULL)")
        db.commit()

        with patch("bpp.web.face_phase_classes.embedding_method", return_value="sface"):
            extract_and_cluster_faces(db, [], {}, 1024, 0.3, {"face_cluster_threshold": 0.55})

        row = db.execute("SELECT value FROM settings WHERE key='face_embedding_method'").fetchone()
        assert row is not None
        assert row[0] == "sface"

    def test_method_change_wipes_embeddings(self, db):
        """Changing embedding method should wipe existing embeddings."""
        # Insert some fake embeddings from "dlib"
        db.execute("INSERT INTO settings VALUES ('face_embedding_method', 'dlib')")
        db.execute("INSERT INTO photos VALUES (1, '/test.jpg', 0, NULL, NULL)")
        fake_emb = np.random.randn(128).astype(np.float32).tobytes()
        db.execute(
            "INSERT INTO face_embeddings (photo_id, face_index, "
            "bbox_x, bbox_y, bbox_w, bbox_h, embedding, cluster_id) "
            "VALUES (1, 0, 10, 20, 50, 60, ?, 5)",
            (fake_emb,),
        )
        db.commit()
        assert db.execute("SELECT COUNT(*) FROM face_embeddings").fetchone()[0] == 1

        from bpp.web.face_worker import extract_and_cluster_faces

        with patch("bpp.web.face_phase_classes.embedding_method", return_value="sface"):
            extract_and_cluster_faces(db, [], {}, 1024, 0.3, {"face_cluster_threshold": 0.55})

        # Embeddings should be wiped
        assert db.execute("SELECT COUNT(*) FROM face_embeddings").fetchone()[0] == 0
        # Method should be updated
        row = db.execute("SELECT value FROM settings WHERE key='face_embedding_method'").fetchone()
        assert row[0] == "sface"

    def test_same_method_preserves_embeddings(self, db):
        """Same embedding method should NOT wipe embeddings."""
        db.execute("INSERT INTO settings VALUES ('face_embedding_method', 'sface')")
        db.execute("INSERT INTO photos VALUES (1, '/test.jpg', 0, NULL, NULL)")
        fake_emb = np.random.randn(128).astype(np.float32).tobytes()
        db.execute(
            "INSERT INTO face_embeddings (photo_id, face_index, "
            "bbox_x, bbox_y, bbox_w, bbox_h, embedding, cluster_id) "
            "VALUES (1, 0, 10, 20, 50, 60, ?, 5)",
            (fake_emb,),
        )
        db.commit()

        from bpp.web.face_worker import extract_and_cluster_faces

        with patch("bpp.web.face_phase_classes.embedding_method", return_value="sface"):
            extract_and_cluster_faces(db, [], {}, 1024, 0.3, {"face_cluster_threshold": 0.55})

        # Embeddings should be preserved
        assert db.execute("SELECT COUNT(*) FROM face_embeddings").fetchone()[0] == 1


class TestLandmarkValidationStillWorks:
    """Ensure _validate_face_landmarks still works (used by dlib fallback)."""

    def test_valid_frontal_face(self):
        bbox = (100, 100, 120, 140)
        lm = {
            "left_eye": [(130, 140), (145, 140)],
            "right_eye": [(170, 140), (185, 140)],
            "nose_tip": [(155, 180)],
        }
        assert _validate_face_landmarks(lm, bbox) is True

    def test_reject_empty(self):
        assert _validate_face_landmarks({}, (100, 100, 120, 140)) is False

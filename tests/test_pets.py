"""Tests for pet detection module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest


class TestIsAvailable:
    def test_available_when_onnxruntime_installed(self):
        from bpp.scoring.pets import is_available

        # onnxruntime is installed in the dev env
        assert is_available() is True

    def test_unavailable_when_import_fails(self):
        import importlib

        import bpp.scoring.pets as pets_mod

        with patch.dict("sys.modules", {"onnxruntime": None}):
            importlib.reload(pets_mod)
            assert pets_mod.is_available() is False
        importlib.reload(pets_mod)


class TestModelManagement:
    def test_get_model_dir_creates_directory(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        from bpp.scoring.pets import _get_model_dir

        model_dir = _get_model_dir()
        assert model_dir.startswith(str(tmp_path))
        assert "bpp" in model_dir

    def test_ensure_model_skips_download_if_exists(self, tmp_path, monkeypatch):
        """Post-R5-H3, the cached file is SHA-verified before reuse —
        a tampered cache now raises ModelIntegrityError. Pin
        _MODEL_SHA256 to the test bytes so verification passes; the
        path under test is still "existing cache reused without
        re-downloading."""
        import hashlib
        import os

        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        from bpp.scoring import pets as pets_mod
        from bpp.scoring.pets import _get_model_path, ensure_model

        model_path = _get_model_path()
        os.makedirs(os.path.dirname(model_path), exist_ok=True)
        content = b"fake model"
        with open(model_path, "wb") as f:
            f.write(content)
        monkeypatch.setattr(pets_mod, "_MODEL_SHA256", hashlib.sha256(content).hexdigest())

        result = ensure_model()
        assert result == model_path


class TestPreprocess:
    def test_preprocess_default_output_shape(self):
        from bpp.scoring.pets import _INPUT_SIZE, _preprocess

        img = np.zeros((480, 640, 3), dtype=np.uint8)
        blob, _scale, _pad_x, _pad_y = _preprocess(img)
        assert blob.shape == (1, 3, _INPUT_SIZE, _INPUT_SIZE)
        assert blob.dtype == np.float32
        assert 0.0 <= blob.max() <= 1.0

    def test_preprocess_custom_input_size(self):
        from bpp.scoring.pets import _preprocess

        img = np.zeros((480, 640, 3), dtype=np.uint8)
        blob, _scale, _pad_x, _pad_y = _preprocess(img, input_size=640)
        assert blob.shape == (1, 3, 640, 640)

    def test_preprocess_square_image(self):
        from bpp.scoring.pets import _preprocess

        img = np.zeros((640, 640, 3), dtype=np.uint8)
        _blob, scale, pad_x, pad_y = _preprocess(img, input_size=640)
        assert scale == 1.0
        assert pad_x == 0
        assert pad_y == 0

    def test_preprocess_tall_image(self):
        from bpp.scoring.pets import _preprocess

        img = np.zeros((1280, 640, 3), dtype=np.uint8)
        _blob, scale, pad_x, pad_y = _preprocess(img, input_size=640)
        assert scale == pytest.approx(0.5)
        assert pad_y == 0
        assert pad_x > 0  # horizontal padding


class TestPostprocess:
    def test_empty_output_no_detections(self):
        from bpp.scoring.pets import _postprocess

        # Simulate YOLO output with no confident detections
        output = np.zeros((1, 84, 8400), dtype=np.float32)
        result = _postprocess(output, 1.0, 0, 0, 640, 480)
        assert result == []

    def test_single_cat_detection(self):
        from bpp.scoring.pets import _CAT_CLASS, _postprocess

        # Create output with one strong cat detection
        output = np.zeros((1, 84, 8400), dtype=np.float32)
        # Detection at index 0: centered at (320, 240) with w=100, h=80
        output[0, 0, 0] = 320.0  # cx
        output[0, 1, 0] = 240.0  # cy
        output[0, 2, 0] = 100.0  # w
        output[0, 3, 0] = 80.0  # h
        # Class 15 (cat) confidence = 0.9
        output[0, 4 + _CAT_CLASS, 0] = 0.9

        result = _postprocess(output, 1.0, 0, 0, 640, 480)
        assert len(result) == 1
        assert result[0]["class"] == "cat"
        assert result[0]["confidence"] == pytest.approx(0.9)
        assert result[0]["bbox_x"] == 270  # 320 - 100/2
        assert result[0]["bbox_y"] == 200  # 240 - 80/2
        assert result[0]["bbox_w"] == 100
        assert result[0]["bbox_h"] == 80

    def test_dog_detection(self):
        from bpp.scoring.pets import _DOG_CLASS, _postprocess

        output = np.zeros((1, 84, 8400), dtype=np.float32)
        output[0, 0, 0] = 320.0
        output[0, 1, 0] = 240.0
        output[0, 2, 0] = 150.0
        output[0, 3, 0] = 120.0
        output[0, 4 + _DOG_CLASS, 0] = 0.85

        result = _postprocess(output, 1.0, 0, 0, 640, 480)
        assert len(result) == 1
        assert result[0]["class"] == "dog"

    def test_low_confidence_filtered(self):
        from bpp.scoring.pets import _CAT_CLASS, _postprocess

        output = np.zeros((1, 84, 8400), dtype=np.float32)
        output[0, 0, 0] = 320.0
        output[0, 1, 0] = 240.0
        output[0, 2, 0] = 100.0
        output[0, 3, 0] = 80.0
        output[0, 4 + _CAT_CLASS, 0] = 0.1  # Below threshold

        result = _postprocess(output, 1.0, 0, 0, 640, 480)
        assert result == []


class TestDetectPets:
    def test_detect_pets_returns_correct_structure(self):
        from bpp.scoring.pets import detect_pets

        mock_session = MagicMock()
        mock_session.get_inputs.return_value = [MagicMock(name="images")]
        # Empty detections output
        mock_session.run.return_value = [np.zeros((1, 84, 8400), dtype=np.float32)]

        with patch("bpp.scoring.pets._get_session", return_value=mock_session):
            img = np.zeros((480, 640, 3), dtype=np.uint8)
            result = detect_pets(img)

        assert "pet_count" in result
        assert "has_cat" in result
        assert "has_dog" in result
        assert "pet_detections" in result
        assert result["pet_count"] == 0
        assert result["has_cat"] == 0
        assert result["has_dog"] == 0

    def test_tiled_fallback_disabled_by_default(self):
        """Regression: tiled fallback at conf=0.2 on 640px tiles produces
        ~80% false-positive rate on pet-free photos. Empirical sweep on
        2026-04-25 found 12/15 random no-pet photos hallucinated pets
        when enable_tiling=True. Default must stay False — flip back
        only with rationale + a precision benchmark.
        """
        import inspect

        from bpp.scoring.pets import detect_pets

        sig = inspect.signature(detect_pets)
        assert sig.parameters["enable_tiling"].default is False, (
            "detect_pets(enable_tiling=...) default must be False — see "
            "comment in pets.py for empirical justification. Flipping "
            "back to True without measuring precision will reintroduce "
            "the false-positive flood."
        )

    def test_tiled_fallback_only_runs_when_enabled(self):
        """When enable_tiling=False (the default), no tiled detect call
        happens even if single-pass returns nothing."""
        from bpp.scoring.pets import detect_pets

        mock_session = MagicMock()
        mock_session.get_inputs.return_value = [MagicMock(name="images")]
        mock_session.run.return_value = [np.zeros((1, 84, 8400), dtype=np.float32)]

        with (
            patch("bpp.scoring.pets._get_session", return_value=mock_session),
            patch("bpp.scoring.pets._tiled_detect") as tiled,
        ):
            img = np.zeros((1024, 1024, 3), dtype=np.uint8)
            detect_pets(img)  # default enable_tiling=False
            tiled.assert_not_called()

            detect_pets(img, enable_tiling=True)
            tiled.assert_called_once()

    def test_detect_pets_with_cat(self):
        from bpp.scoring.pets import _CAT_CLASS, detect_pets

        mock_session = MagicMock()
        mock_session.get_inputs.return_value = [MagicMock(name="images")]
        output = np.zeros((1, 84, 8400), dtype=np.float32)
        output[0, 0, 0] = 320.0
        output[0, 1, 0] = 240.0
        output[0, 2, 0] = 100.0
        output[0, 3, 0] = 80.0
        output[0, 4 + _CAT_CLASS, 0] = 0.9
        mock_session.run.return_value = [output]

        with patch("bpp.scoring.pets._get_session", return_value=mock_session):
            img = np.zeros((480, 640, 3), dtype=np.uint8)
            result = detect_pets(img)

        assert result["pet_count"] == 1
        assert result["has_cat"] == 1
        assert result["has_dog"] == 0


class TestDetectPetsFromFile:
    def test_nonexistent_file_returns_empty(self):
        from bpp.scoring.pets import detect_pets_from_file

        result = detect_pets_from_file("/nonexistent/path.jpg")
        assert result["pet_count"] == 0
        assert result["has_cat"] == 0
        assert result["has_dog"] == 0


class TestSchemaAndStorage:
    """Tests for pet detection columns in the DB schema."""

    def test_pet_columns_exist(self, tmp_path):
        from bpp.db.connection import init_db

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(photos)").fetchall()}
        assert "pet_count" in cols
        assert "has_cat" in cols
        assert "has_dog" in cols
        conn.close()

    def test_pet_detections_table_exists(self, tmp_path):
        from bpp.db.connection import init_db

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "pet_detections" in tables
        conn.close()

    def test_pet_columns_in_score_columns(self):
        from bpp.db.photos import _SCORE_COLUMNS

        assert "pet_count" in _SCORE_COLUMNS
        assert "has_cat" in _SCORE_COLUMNS
        assert "has_dog" in _SCORE_COLUMNS

    def test_bulk_upsert_with_pet_data(self, tmp_path):
        from bpp.db.connection import init_db
        from bpp.db.photos import bulk_upsert_photos, get_photo_by_path

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)

        # Create a test file
        test_file = tmp_path / "cat_photo.jpg"
        test_file.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)

        photos = [
            {
                "filepath": str(test_file),
                "pet_count": 2,
                "has_cat": 1,
                "has_dog": 1,
                "blur_score": 0.5,
                "aggregate_score": 0.6,
            }
        ]
        bulk_upsert_photos(conn, photos)

        photo = get_photo_by_path(conn, str(test_file))
        assert photo is not None
        assert photo["pet_count"] == 2
        assert photo["has_cat"] == 1
        assert photo["has_dog"] == 1
        conn.close()


class TestSmartAlbums:
    """Tests for pet-based smart albums."""

    def test_refresh_creates_pet_albums(self, tmp_path):
        from bpp.db.connection import init_db
        from bpp.db.pets import assign_pet_clusters, upsert_pet_detections
        from bpp.db.photos import bulk_upsert_photos, get_photo_id_by_path
        from bpp.db.smart_albums import refresh_smart_albums

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)

        # Create test files and insert photos with pet data
        for i in range(3):
            f = tmp_path / f"cat_{i}.jpg"
            f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)

        photos = [
            {
                "filepath": str(tmp_path / f"cat_{i}.jpg"),
                "date": f"2024-01-{i + 1:02d}T12:00:00",
                "date_day": f"2024-01-{i + 1:02d}",
                "date_month": "2024-01",
                "pet_count": 1,
                "has_cat": 1,
                "has_dog": 0,
                "blur_score": 0.5,
                "aggregate_score": 0.5,
            }
            for i in range(3)
        ]
        bulk_upsert_photos(conn, photos)

        # Insert pet detections and assign clusters (required for cluster-based albums)
        for i in range(3):
            fp = str(tmp_path / f"cat_{i}.jpg")
            pid = get_photo_id_by_path(conn, fp)
            upsert_pet_detections(conn, pid, [{"class": "cat", "confidence": 0.9}])
        assign_pet_clusters(conn)

        refresh_smart_albums(conn)

        # Check that a cat album was created
        row = conn.execute(
            "SELECT id FROM albums WHERE album_type='smart_pet' AND rule_json LIKE '%cat%'"
        ).fetchone()
        assert row is not None

        # Check photos are in the album
        album_id = row[0]
        count = conn.execute(
            "SELECT COUNT(*) FROM album_photos WHERE album_id=?", (album_id,)
        ).fetchone()[0]
        assert count == 3
        conn.close()

    def test_pet_album_rule_uses_singular_class(self, tmp_path):
        """Smart pet albums must store singular pet_class ('cat', 'dog') to match cluster data."""
        import json

        from bpp.db.connection import init_db
        from bpp.db.pets import assign_pet_clusters, upsert_pet_detections
        from bpp.db.photos import bulk_upsert_photos, get_photo_id_by_path
        from bpp.db.smart_albums import refresh_smart_albums

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)

        for i in range(2):
            f = tmp_path / f"cat_{i}.jpg"
            f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)

        photos = [
            {
                "filepath": str(tmp_path / f"cat_{i}.jpg"),
                "date": f"2024-01-{i + 1:02d}T12:00:00",
                "date_day": f"2024-01-{i + 1:02d}",
                "date_month": "2024-01",
                "pet_count": 1,
                "has_cat": 1,
                "has_dog": 0,
                "blur_score": 0.5,
                "aggregate_score": 0.5,
            }
            for i in range(2)
        ]
        bulk_upsert_photos(conn, photos)

        for i in range(2):
            fp = str(tmp_path / f"cat_{i}.jpg")
            pid = get_photo_id_by_path(conn, fp)
            upsert_pet_detections(conn, pid, [{"class": "cat", "confidence": 0.9}])
        assign_pet_clusters(conn)

        refresh_smart_albums(conn)

        row = conn.execute("SELECT rule_json FROM albums WHERE album_type='smart_pet'").fetchone()
        assert row is not None
        rule = json.loads(row[0])
        assert rule["pet_class"] == "cat"  # singular, not "cats"
        conn.close()

    def test_legacy_plural_rule_migrated_to_singular(self, tmp_path):
        """Existing albums with plural pet_class ('cats') get migrated to singular ('cat')."""
        import json

        from bpp.db.connection import init_db
        from bpp.db.photos import bulk_upsert_photos
        from bpp.db.smart_albums import refresh_smart_albums

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)

        for i in range(2):
            f = tmp_path / f"cat_{i}.jpg"
            f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)

        photos = [
            {
                "filepath": str(tmp_path / f"cat_{i}.jpg"),
                "date": f"2024-01-{i + 1:02d}T12:00:00",
                "date_day": f"2024-01-{i + 1:02d}",
                "date_month": "2024-01",
                "pet_count": 1,
                "has_cat": 1,
                "has_dog": 0,
                "blur_score": 0.5,
                "aggregate_score": 0.5,
            }
            for i in range(2)
        ]
        bulk_upsert_photos(conn, photos)

        # Manually insert a legacy album with plural "cats"
        legacy_rule = json.dumps({"pet_class": "cats"}, sort_keys=True)
        conn.execute(
            "INSERT INTO albums (name, album_type, rule_json) VALUES (?, ?, ?)",
            ("Cats", "smart_pet", legacy_rule),
        )
        conn.commit()

        # Refresh should migrate the rule to singular
        refresh_smart_albums(conn)

        rows = conn.execute("SELECT rule_json FROM albums WHERE album_type='smart_pet'").fetchall()
        for row in rows:
            rule = json.loads(row[0])
            assert rule["pet_class"] == "cat", f"Expected singular 'cat', got '{rule['pet_class']}'"
        conn.close()

    def test_pet_class_matches_cluster_data(self, tmp_path):
        """Album pet_class must match what get_pet_clusters returns for navigation to work."""
        import json

        from bpp.db.connection import init_db
        from bpp.db.pets import assign_pet_clusters, get_pet_clusters, upsert_pet_detections
        from bpp.db.photos import bulk_upsert_photos
        from bpp.db.smart_albums import refresh_smart_albums

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)

        for i in range(3):
            f = tmp_path / f"mixed_{i}.jpg"
            f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)

        photos = [
            {
                "filepath": str(tmp_path / f"mixed_{i}.jpg"),
                "date": f"2024-01-{i + 1:02d}T12:00:00",
                "date_day": f"2024-01-{i + 1:02d}",
                "date_month": "2024-01",
                "pet_count": 1,
                "has_cat": 1 if i < 2 else 0,
                "has_dog": 0 if i < 2 else 1,
                "blur_score": 0.5,
                "aggregate_score": 0.5,
            }
            for i in range(3)
        ]
        bulk_upsert_photos(conn, photos)

        # Insert detections and cluster them
        from bpp.db.photos import get_photo_id_by_path

        for i in range(3):
            fp = str(tmp_path / f"mixed_{i}.jpg")
            pid = get_photo_id_by_path(conn, fp)
            cls = "cat" if i < 2 else "dog"
            upsert_pet_detections(conn, pid, [{"class": cls, "confidence": 0.9}])
        assign_pet_clusters(conn)

        refresh_smart_albums(conn)

        # Get cluster pet_class values
        clusters = get_pet_clusters(conn)
        cluster_classes = {c["pet_class"] for c in clusters}

        # Get album rule pet_class values
        album_rows = conn.execute(
            "SELECT rule_json FROM albums WHERE album_type='smart_pet'"
        ).fetchall()
        album_classes = {json.loads(r[0])["pet_class"] for r in album_rows}

        # They must match — this is what broke navigation
        assert cluster_classes == album_classes
        conn.close()

    def test_no_pet_albums_when_no_pets(self, tmp_path):
        from bpp.db.connection import init_db
        from bpp.db.photos import bulk_upsert_photos
        from bpp.db.smart_albums import refresh_smart_albums

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)

        f = tmp_path / "no_pet.jpg"
        f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)

        photos = [
            {
                "filepath": str(f),
                "date": "2024-01-01T12:00:00",
                "date_day": "2024-01-01",
                "date_month": "2024-01",
                "pet_count": 0,
                "has_cat": 0,
                "has_dog": 0,
                "blur_score": 0.5,
                "aggregate_score": 0.5,
            }
        ]
        bulk_upsert_photos(conn, photos)
        refresh_smart_albums(conn)

        row = conn.execute("SELECT COUNT(*) FROM albums WHERE album_type='smart_pet'").fetchone()
        assert row[0] == 0
        conn.close()


class TestPetDetectionsCRUD:
    """Tests for pet detections CRUD in db/pets.py."""

    def test_upsert_and_get_detections(self, tmp_path):
        from bpp.db.connection import init_db
        from bpp.db.pets import get_pet_detections, upsert_pet_detections
        from bpp.db.photos import upsert_photo

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)

        f = tmp_path / "pet_photo.jpg"
        f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
        photo_id = upsert_photo(conn, {"filepath": str(f)})

        detections = [
            {
                "class": "cat",
                "confidence": 0.92,
                "bbox_x": 10,
                "bbox_y": 20,
                "bbox_w": 100,
                "bbox_h": 80,
            },
            {
                "class": "dog",
                "confidence": 0.85,
                "bbox_x": 200,
                "bbox_y": 30,
                "bbox_w": 120,
                "bbox_h": 90,
            },
        ]
        upsert_pet_detections(conn, photo_id, detections)

        result = get_pet_detections(conn, photo_id)
        assert len(result) == 2
        assert result[0]["class"] == "cat"
        assert result[0]["confidence"] == pytest.approx(0.92)
        assert result[1]["class"] == "dog"
        conn.close()

    def test_upsert_replaces_old_detections(self, tmp_path):
        from bpp.db.connection import init_db
        from bpp.db.pets import get_pet_detections, upsert_pet_detections
        from bpp.db.photos import upsert_photo

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)

        f = tmp_path / "pet2.jpg"
        f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
        photo_id = upsert_photo(conn, {"filepath": str(f)})

        upsert_pet_detections(conn, photo_id, [{"class": "cat", "confidence": 0.9}])
        assert len(get_pet_detections(conn, photo_id)) == 1

        # Replace with new detections
        upsert_pet_detections(conn, photo_id, [{"class": "dog", "confidence": 0.8}])
        result = get_pet_detections(conn, photo_id)
        assert len(result) == 1
        assert result[0]["class"] == "dog"
        conn.close()

    def test_assign_pet_clusters(self, tmp_path):
        from bpp.db.connection import init_db
        from bpp.db.pets import (
            assign_pet_clusters,
            get_pet_detections,
            upsert_pet_detections,
        )
        from bpp.db.photos import upsert_photo

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)

        f = tmp_path / "pet3.jpg"
        f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
        photo_id = upsert_photo(conn, {"filepath": str(f)})

        detections = [
            {"class": "cat", "confidence": 0.9},
            {"class": "dog", "confidence": 0.8},
        ]
        upsert_pet_detections(conn, photo_id, detections)
        assign_pet_clusters(conn)

        result = get_pet_detections(conn, photo_id)
        assert result[0]["cluster_id"] == 0  # cat
        assert result[1]["cluster_id"] == 1  # dog
        conn.close()

    def test_get_pet_clusters(self, tmp_path):
        from bpp.db.connection import init_db
        from bpp.db.pets import (
            assign_pet_clusters,
            get_pet_clusters,
            upsert_pet_detections,
        )
        from bpp.db.photos import upsert_photo

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)

        for i in range(3):
            f = tmp_path / f"cat_{i}.jpg"
            f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
            photo_id = upsert_photo(conn, {"filepath": str(f)})
            upsert_pet_detections(conn, photo_id, [{"class": "cat", "confidence": 0.8 + i * 0.05}])

        assign_pet_clusters(conn)
        clusters = get_pet_clusters(conn)

        assert len(clusters) == 1
        assert clusters[0]["pet_class"] == "cat"
        assert clusters[0]["photo_count"] == 3
        assert clusters[0]["representative"] is not None
        assert len(clusters[0]["filepaths"]) == 3
        conn.close()

    def test_has_pet_data(self, tmp_path):
        from bpp.db.connection import init_db
        from bpp.db.pets import has_pet_data, upsert_pet_detections
        from bpp.db.photos import upsert_photo

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)
        assert has_pet_data(conn) is False

        f = tmp_path / "pet4.jpg"
        f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
        photo_id = upsert_photo(conn, {"filepath": str(f)})
        upsert_pet_detections(conn, photo_id, [{"class": "cat", "confidence": 0.9}])
        assert has_pet_data(conn) is True
        conn.close()

    def test_split_pet_cluster(self, tmp_path):
        from bpp.db.connection import init_db
        from bpp.db.pets import (
            assign_pet_clusters,
            get_pet_clusters,
            get_pet_detections,
            split_pet_cluster,
            upsert_pet_detections,
        )
        from bpp.db.photos import upsert_photo

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)

        # Create 3 cat photos, all in cluster 0
        det_ids = []
        for i in range(3):
            f = tmp_path / f"split_cat_{i}.jpg"
            f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
            pid = upsert_photo(conn, {"filepath": str(f)})
            upsert_pet_detections(conn, pid, [{"class": "cat", "confidence": 0.9}])
            dets = get_pet_detections(conn, pid)
            det_ids.append(dets[0]["id"])

        assign_pet_clusters(conn)

        # Split first detection into a new cluster
        new_cid = split_pet_cluster(conn, [det_ids[0]])
        assert new_cid is not None
        assert new_cid >= 1  # next after 0 (cat cluster)

        clusters = get_pet_clusters(conn)
        cluster_ids = {c["cluster_id"] for c in clusters}
        assert 0 in cluster_ids  # original cat cluster
        assert new_cid in cluster_ids  # new split cluster
        # Original cluster should have 2 photos, new cluster should have 1
        for c in clusters:
            if c["cluster_id"] == 0:
                assert c["photo_count"] == 2
            elif c["cluster_id"] == new_cid:
                assert c["photo_count"] == 1
        conn.close()

    def test_split_pet_cluster_no_match(self, tmp_path):
        from bpp.db.connection import init_db
        from bpp.db.pets import split_pet_cluster

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)

        result = split_pet_cluster(conn, [99999])
        assert result is None
        conn.close()

    def test_merge_pet_clusters(self, tmp_path):
        from bpp.db.connection import init_db
        from bpp.db.pets import (
            assign_pet_clusters,
            get_pet_clusters,
            get_pet_detections,
            merge_pet_clusters,
            split_pet_cluster,
            upsert_pet_detections,
        )
        from bpp.db.photos import upsert_photo

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)

        # Create 3 cats and 1 dog
        cat_det_ids = []
        for i in range(3):
            f = tmp_path / f"merge_cat_{i}.jpg"
            f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
            pid = upsert_photo(conn, {"filepath": str(f)})
            upsert_pet_detections(conn, pid, [{"class": "cat", "confidence": 0.9}])
            dets = get_pet_detections(conn, pid)
            cat_det_ids.append(dets[0]["id"])

        assign_pet_clusters(conn)

        # Split one cat out
        new_cid = split_pet_cluster(conn, [cat_det_ids[0]])
        assert new_cid is not None

        # Merge the new cluster back into cluster 0
        count = merge_pet_clusters(conn, 0, [new_cid])
        assert count == 1

        clusters = get_pet_clusters(conn)
        cat_clusters = [c for c in clusters if c["pet_class"] == "cat"]
        assert len(cat_clusters) == 1
        assert cat_clusters[0]["photo_count"] == 3
        conn.close()

    def test_merge_empty_list(self, tmp_path):
        from bpp.db.connection import init_db
        from bpp.db.pets import merge_pet_clusters

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)

        count = merge_pet_clusters(conn, 0, [])
        assert count == 0
        conn.close()

    def test_dismiss_pet_cluster(self, tmp_path):
        """'Not a pet' moves every detection to CLUSTER_DISMISSED — gone from clusters + chips."""
        from bpp.constants import CLUSTER_DISMISSED
        from bpp.db.connection import init_db
        from bpp.db.pets import (
            assign_pet_clusters,
            dismiss_pet_cluster,
            get_pet_clusters,
            get_pet_detections,
            upsert_pet_detections,
        )
        from bpp.db.photos import upsert_photo

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)

        pids = []
        for i in range(2):
            f = tmp_path / f"dismiss_cat_{i}.jpg"
            f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
            pid = upsert_photo(conn, {"filepath": str(f)})
            upsert_pet_detections(conn, pid, [{"class": "cat", "confidence": 0.9}])
            pids.append(pid)
        assign_pet_clusters(conn)
        assert len(get_pet_clusters(conn)) == 1

        count = dismiss_pet_cluster(conn, 0)
        assert count == 2

        # Cluster gone from the Pets view…
        assert get_pet_clusters(conn) == []
        # …and from photo chips.
        for pid in pids:
            assert get_pet_detections(conn, pid) == [], f"photo {pid} still shows pet chips"
        # Sentinel actually stored (recoverable, not deleted).
        rows = conn.execute(
            "SELECT cluster_id FROM pet_detections WHERE photo_id IN (?, ?)", pids
        ).fetchall()
        assert len(rows) == 2
        assert all(r[0] == CLUSTER_DISMISSED for r in rows)
        conn.close()

    def test_dismiss_rejects_sentinels_and_empty(self, tmp_path):
        from bpp.constants import CLUSTER_DISMISSED, CLUSTER_UNASSIGNED
        from bpp.db.connection import init_db
        from bpp.db.pets import dismiss_pet_cluster

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)

        assert dismiss_pet_cluster(conn, CLUSTER_UNASSIGNED) == 0
        assert dismiss_pet_cluster(conn, CLUSTER_DISMISSED) == 0
        assert dismiss_pet_cluster(conn, 42) == 0  # no such cluster
        conn.close()

    def test_split_creates_per_cluster_album(self, tmp_path):
        """Splitting a pet cluster and refreshing albums creates a per-cluster album."""
        from bpp.db.connection import init_db
        from bpp.db.pets import (
            assign_pet_clusters,
            get_pet_detections,
            split_pet_cluster,
            upsert_pet_detections,
        )
        from bpp.db.photos import upsert_photo
        from bpp.db.smart_albums import refresh_smart_albums

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)

        det_ids = []
        for i in range(3):
            f = tmp_path / f"album_cat_{i}.jpg"
            f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
            pid = upsert_photo(
                conn,
                {
                    "filepath": str(f),
                    "date": f"2024-01-{i + 1:02d}T12:00:00",
                    "date_day": f"2024-01-{i + 1:02d}",
                    "date_month": "2024-01",
                    "pet_count": 1,
                    "has_cat": 1,
                    "has_dog": 0,
                    "blur_score": 0.5,
                    "aggregate_score": 0.5,
                },
            )
            upsert_pet_detections(conn, pid, [{"class": "cat", "confidence": 0.9}])
            dets = get_pet_detections(conn, pid)
            det_ids.append(dets[0]["id"])

        assign_pet_clusters(conn)
        refresh_smart_albums(conn)

        # Split one detection
        split_pet_cluster(conn, [det_ids[0]])
        refresh_smart_albums(conn)

        # Should have 2 pet albums now — one for original cluster, one for new
        albums = conn.execute(
            "SELECT rule_json FROM albums WHERE album_type='smart_pet'"
        ).fetchall()
        assert len(albums) == 2
        conn.close()

    def test_cluster_survives_reanalysis(self, tmp_path):
        """Manual cluster assignments must survive re-analysis upserts."""
        from bpp.db.connection import init_db
        from bpp.db.pets import (
            assign_pet_clusters,
            bulk_upsert_pet_detections,
            get_pet_detections,
            split_pet_cluster,
            upsert_pet_detections,
        )
        from bpp.db.photos import upsert_photo

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)
        # Insert 3 photos with cat detections
        pids = []
        for i in range(3):
            f = tmp_path / f"reanalysis_{i}.jpg"
            f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
            pid = upsert_photo(conn, {"filepath": str(f)})
            pids.append(pid)
            upsert_pet_detections(conn, pid, [{"class": "cat", "confidence": 0.9}])
        assign_pet_clusters(conn)

        # Split first photo's detection into a new cluster
        det0 = get_pet_detections(conn, pids[0])
        new_cid = split_pet_cluster(conn, [det0[0]["id"]])
        assert new_cid is not None
        assert new_cid != 0  # not the default cat cluster

        # Re-analyze all 3 photos (simulates re-analysis)
        items = [(pid, [{"class": "cat", "confidence": 0.95}]) for pid in pids]
        bulk_upsert_pet_detections(conn, items)
        assign_pet_clusters(conn)

        # The split cluster assignment must survive
        det_after = get_pet_detections(conn, pids[0])
        assert det_after[0]["cluster_id"] == new_cid, (
            f"Expected cluster_id={new_cid}, got {det_after[0]['cluster_id']}. "
            "Re-analysis destroyed manual cluster assignment."
        )
        # Other photos should remain in default cluster
        for pid in pids[1:]:
            det = get_pet_detections(conn, pid)
            assert det[0]["cluster_id"] == 0
        conn.close()

    def test_single_upsert_preserves_cluster(self, tmp_path):
        """Single-photo upsert must also preserve cluster assignments."""
        from bpp.db.connection import init_db
        from bpp.db.pets import (
            assign_pet_clusters,
            get_pet_detections,
            split_pet_cluster,
            upsert_pet_detections,
        )
        from bpp.db.photos import upsert_photo

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)
        f = tmp_path / "pet.jpg"
        f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
        pid = upsert_photo(conn, {"filepath": str(f)})
        upsert_pet_detections(conn, pid, [{"class": "cat", "confidence": 0.9}])
        assign_pet_clusters(conn)

        det = get_pet_detections(conn, pid)
        new_cid = split_pet_cluster(conn, [det[0]["id"]])

        # Re-upsert (simulates single-photo re-analysis)
        upsert_pet_detections(conn, pid, [{"class": "cat", "confidence": 0.95}])
        assign_pet_clusters(conn)

        det_after = get_pet_detections(conn, pid)
        assert det_after[0]["cluster_id"] == new_cid
        conn.close()

    def test_next_cluster_id_avoids_sentinel_collision(self, tmp_path):
        """_next_cluster_id must not return 0 or 1, which collide with cat/dog defaults."""
        from bpp.db.connection import init_db
        from bpp.db.pets import _next_cluster_id, upsert_pet_detections

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)
        # Insert a detection that stays unassigned (cluster_id=-1)
        from bpp.db.photos import upsert_photo

        f = tmp_path / "sentinel.jpg"
        f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
        pid = upsert_photo(conn, {"filepath": str(f)})
        upsert_pet_detections(conn, pid, [{"class": "cat", "confidence": 0.9}])
        # Before assign_pet_clusters, all detections are at -1
        nid = _next_cluster_id(conn)
        assert nid >= 2, f"_next_cluster_id returned {nid}, would collide with cat=0 or dog=1"
        conn.close()

    def test_schema_v5_cluster_id_column(self, tmp_path):
        from bpp.db.connection import init_db

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(pet_detections)").fetchall()}
        assert "cluster_id" in cols
        conn.close()

"""Tests for pet detection module — part 1 of 3."""

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
        # This pins the XDG fallback; an exported BPP_CACHE_DIR outranks it.
        monkeypatch.delenv("BPP_CACHE_DIR", raising=False)
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        from bpp.scoring.pets import _get_model_dir

        model_dir = _get_model_dir()
        assert model_dir.startswith(str(tmp_path))
        assert "bpp" in model_dir

    def test_ensure_model_skips_download_if_exists(self, tmp_path, monkeypatch):
        """The cached file is SHA-verified before reuse —
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

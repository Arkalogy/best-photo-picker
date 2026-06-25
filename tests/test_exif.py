"""TDD tests for EXIF metadata extraction, DB storage, and API."""

from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import MagicMock

import pytest
from PIL import Image

from bpp.exif_utils import extract_exif_metadata

# ── Helpers ──


def _make_jpeg_with_exif(exif_data: dict, tmp_dir: str) -> str:
    """Create a minimal JPEG with EXIF tags and return path."""
    img = Image.new("RGB", (100, 100), (128, 128, 128))
    exif = img.getexif()
    for tag, val in exif_data.items():
        exif[tag] = val
    path = os.path.join(tmp_dir, "test.jpg")
    img.save(path, exif=exif.tobytes())
    return path


def _make_jpeg_with_gps(lat: float, lon: float, tmp_dir: str) -> str:
    """Create a JPEG with GPS IFD data."""
    img = Image.new("RGB", (100, 100), (128, 128, 128))
    exif = img.getexif()

    # GPS IFD (tag 0x8825)
    from PIL.ExifTags import IFD

    gps_ifd = {
        1: "N" if lat >= 0 else "S",  # GPSLatitudeRef
        2: (abs(lat), 1.0, 0.0),  # GPSLatitude (degrees, minutes, seconds)
        3: "E" if lon >= 0 else "W",  # GPSLongitudeRef
        4: (abs(lon), 1.0, 0.0),  # GPSLongitude
    }
    exif.get_ifd(IFD.GPSInfo).update(gps_ifd)

    path = os.path.join(tmp_dir, "gps_test.jpg")
    img.save(path, exif=exif.tobytes())
    return path


# ── extract_exif_metadata tests ──


class TestExtractExifMetadata:
    """Tests for the new extract_exif_metadata() function."""

    def test_returns_dict(self, tmp_path):
        """extract_exif_metadata returns a dict even for a basic JPEG."""
        img = Image.new("RGB", (200, 150))
        path = str(tmp_path / "basic.jpg")
        img.save(path)
        result = extract_exif_metadata(path)
        assert isinstance(result, dict)

    def test_dimensions_extracted(self, tmp_path):
        """Width and height are always extracted from the image."""
        img = Image.new("RGB", (1920, 1080))
        path = str(tmp_path / "dims.jpg")
        img.save(path)
        result = extract_exif_metadata(path)
        assert result["width"] == 1920
        assert result["height"] == 1080

    def test_camera_make_model(self, tmp_path):
        """Camera make and model are extracted from EXIF."""
        from PIL.ExifTags import Base as ExifBase

        exif_data = {
            ExifBase.Make: "Canon",
            ExifBase.Model: "Canon EOS R5",
        }
        path = _make_jpeg_with_exif(exif_data, str(tmp_path))
        result = extract_exif_metadata(path)
        assert result["camera_make"] == "Canon"
        assert result["camera_model"] == "Canon EOS R5"

    def test_iso(self, tmp_path):
        """ISO speed is extracted."""
        from PIL.ExifTags import Base as ExifBase

        path = _make_jpeg_with_exif({ExifBase.ISOSpeedRatings: 800}, str(tmp_path))
        result = extract_exif_metadata(path)
        assert result["iso"] == 800

    def test_focal_length(self, tmp_path):
        """Focal length is extracted as a float."""
        from PIL.ExifTags import Base as ExifBase

        path = _make_jpeg_with_exif({ExifBase.FocalLength: 50.0}, str(tmp_path))
        result = extract_exif_metadata(path)
        assert result["focal_length"] == 50.0

    def test_aperture_from_fnumber(self, tmp_path):
        """F-number is extracted as aperture."""
        from PIL.ExifTags import Base as ExifBase

        path = _make_jpeg_with_exif({ExifBase.FNumber: 2.8}, str(tmp_path))
        result = extract_exif_metadata(path)
        assert result["aperture"] == 2.8

    def test_shutter_speed(self, tmp_path):
        """Exposure time is extracted as shutter_speed string."""
        from PIL.ExifTags import Base as ExifBase

        path = _make_jpeg_with_exif({ExifBase.ExposureTime: 0.004}, str(tmp_path))
        result = extract_exif_metadata(path)
        assert result["shutter_speed"] is not None

    def test_missing_exif_returns_dimensions_only(self, tmp_path):
        """A JPEG with no EXIF still returns dimensions."""
        img = Image.new("RGB", (640, 480))
        path = str(tmp_path / "no_exif.jpg")
        img.save(path)
        result = extract_exif_metadata(path)
        assert result["width"] == 640
        assert result["height"] == 480
        assert result.get("camera_make") is None
        assert result.get("iso") is None

    def test_corrupt_file_returns_empty(self, tmp_path):
        """A corrupt file returns an empty dict, not an exception."""
        path = str(tmp_path / "corrupt.jpg")
        with open(path, "wb") as f:
            f.write(b"not a jpeg")
        result = extract_exif_metadata(path)
        assert isinstance(result, dict)
        # Corrupt files may return empty or partial — should not raise
        assert "width" not in result or result["width"] is None

    def test_nonexistent_file_returns_empty(self):
        """A nonexistent file returns an empty dict."""
        result = extract_exif_metadata("/nonexistent/photo.jpg")
        assert isinstance(result, dict)
        assert not result  # empty

    def test_lens_model(self, tmp_path):
        """Lens model is extracted from EXIF tag 42036."""
        from PIL.ExifTags import Base as ExifBase

        path = _make_jpeg_with_exif({ExifBase.LensModel: "RF 50mm F1.2L USM"}, str(tmp_path))
        result = extract_exif_metadata(path)
        assert result["lens"] == "RF 50mm F1.2L USM"

    def test_shutter_speed_fraction_format(self, tmp_path):
        """Short exposure times are formatted as fractions (e.g. '1/250')."""
        from PIL.ExifTags import Base as ExifBase

        path = _make_jpeg_with_exif({ExifBase.ExposureTime: 0.004}, str(tmp_path))
        result = extract_exif_metadata(path)
        # 0.004 = 1/250
        assert "1/" in result["shutter_speed"]

    def test_shutter_speed_long_exposure(self, tmp_path):
        """Long exposures are formatted as seconds (e.g. '2s')."""
        from PIL.ExifTags import Base as ExifBase

        path = _make_jpeg_with_exif({ExifBase.ExposureTime: 2.0}, str(tmp_path))
        result = extract_exif_metadata(path)
        assert "s" in result["shutter_speed"]


# ── GPS extraction tests ──


class TestGPSExtraction:
    """Tests for GPS coordinate extraction."""

    def test_gps_from_exif(self, tmp_path):
        """GPS coordinates are extracted when present."""
        path = _make_jpeg_with_gps(37.7749, -122.4194, str(tmp_path))
        result = extract_exif_metadata(path)
        # GPS extraction should produce lat/lon
        if result.get("gps_lat") is not None:
            assert isinstance(result["gps_lat"], float)
            assert isinstance(result["gps_lon"], float)

    def test_no_gps_returns_none(self, tmp_path):
        """Photos without GPS return None for lat/lon."""
        img = Image.new("RGB", (100, 100))
        path = str(tmp_path / "no_gps.jpg")
        img.save(path)
        result = extract_exif_metadata(path)
        assert result.get("gps_lat") is None
        assert result.get("gps_lon") is None


# ── DB storage tests ──


class TestExifDBStorage:
    """Tests for storing/retrieving EXIF metadata in the DB."""

    @pytest.fixture()
    def db_conn(self, tmp_path):
        from bpp.db.connection import init_db

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)
        return conn

    def test_exif_json_column_exists(self, db_conn):
        """The photos table has an exif_json column after migration."""
        cols = {row[1] for row in db_conn.execute("PRAGMA table_info(photos)").fetchall()}
        assert "exif_json" in cols

    def test_upsert_photo_stores_exif(self, db_conn):
        """upsert_photo stores exif_json when provided."""
        from bpp.db.photos import get_photo_by_path, upsert_photo

        exif = {"camera_make": "Sony", "iso": 400, "width": 4000, "height": 3000}
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            Image.new("RGB", (10, 10)).save(f, "JPEG")
            path = f.name
        try:
            upsert_photo(
                db_conn,
                {
                    "filepath": path,
                    "exif_json": json.dumps(exif),
                },
            )
            photo = get_photo_by_path(db_conn, path)
            assert photo is not None
            stored = json.loads(photo["exif_json"])
            assert stored["camera_make"] == "Sony"
            assert stored["iso"] == 400
        finally:
            os.unlink(path)

    def test_bulk_upsert_stores_exif(self, db_conn):
        """bulk_upsert_photos stores exif_json for all photos."""
        from bpp.db.photos import bulk_upsert_photos, get_photo_by_path

        photos = []
        paths = []
        for i in range(3):
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
                Image.new("RGB", (10, 10)).save(f, "JPEG")
                paths.append(f.name)
            exif = {"camera_make": f"Camera{i}", "width": 100, "height": 100}
            photos.append(
                {
                    "filepath": paths[-1],
                    "exif_json": json.dumps(exif),
                }
            )
        try:
            count = bulk_upsert_photos(db_conn, photos)
            assert count == 3
            for i, p in enumerate(paths):
                photo = get_photo_by_path(db_conn, p)
                stored = json.loads(photo["exif_json"])
                assert stored["camera_make"] == f"Camera{i}"
        finally:
            for p in paths:
                os.unlink(p)

    def test_schema_migration_adds_column(self, db_conn):
        """A fresh DB (schema v6) has the exif_json column."""
        cols = {row[1] for row in db_conn.execute("PRAGMA table_info(photos)").fetchall()}
        assert "exif_json" in cols
        version = db_conn.execute("PRAGMA user_version").fetchone()[0]
        assert version >= 6


# ── Analysis pipeline integration ──


class TestAnalysisPipelineExif:
    """Tests that the analysis pipeline extracts and stores EXIF."""

    def test_analyze_single_includes_exif(self, tmp_path):
        """analyze_single_image includes exif_json in its result."""
        from PIL.ExifTags import Base as ExifBase

        img = Image.new("RGB", (200, 150))
        exif = img.getexif()
        exif[ExifBase.Make] = "Nikon"
        exif[ExifBase.Model] = "Z6"
        exif[ExifBase.ISOSpeedRatings] = 1600
        path = str(tmp_path / "nikon.jpg")
        img.save(path, exif=exif.tobytes())

        from bpp.scoring.aggregate import analyze_single_image

        result = analyze_single_image(path)
        assert result is not None
        assert "exif_json" in result
        exif_data = json.loads(result["exif_json"])
        assert exif_data["camera_make"] == "Nikon"
        assert exif_data["camera_model"] == "Z6"
        assert exif_data["iso"] == 1600
        assert exif_data["width"] == 200
        assert exif_data["height"] == 150


# ── API / build_photo_dict integration ──


class TestBuildPhotoDictExif:
    """Tests that build_photo_dict includes EXIF data."""

    def test_exif_included_in_photo_dict(self):
        """build_photo_dict includes parsed exif when exif_json is present."""
        from bpp.web.model_cache import ModelCache
        from bpp.web.state import WebAppState

        exif = {"camera_make": "Canon", "iso": 100, "width": 6000, "height": 4000}
        item = {
            "filepath": "/tmp/test.jpg",
            "exif_json": json.dumps(exif),
            "aggregate_score": 0.8,
        }

        # Mock a minimal WebAppState. P4: enhanced-ids cache lives on
        # `state.caches.enhanced_ids` so pre-populate it instead of the
        # legacy `_edited_ids` / `_auto_enhanced_ids` attributes.
        import threading

        state = MagicMock(spec=WebAppState)
        state.thumbs = None
        state.caches = ModelCache()
        state.caches.enhanced_ids.edited = set()
        state.caches.enhanced_ids.auto_enhanced = set()
        state.lock = threading.Lock()
        state.config = {}  # .get(key, default) → default sensitive threshold
        # Call the real method
        result = WebAppState.build_photo_dict(state, item)
        assert "exif" in result
        assert result["exif"]["camera_make"] == "Canon"
        assert result["exif"]["width"] == 6000

    def test_exif_none_when_missing(self):
        """build_photo_dict returns None exif when no exif_json."""
        from bpp.web.model_cache import ModelCache
        from bpp.web.state import WebAppState

        item = {
            "filepath": "/tmp/test.jpg",
            "aggregate_score": 0.5,
        }
        import threading

        state = MagicMock(spec=WebAppState)
        state.thumbs = None
        state.caches = ModelCache()
        state.caches.enhanced_ids.edited = set()
        state.caches.enhanced_ids.auto_enhanced = set()
        state.lock = threading.Lock()
        state.config = {}  # .get(key, default) → default sensitive threshold
        result = WebAppState.build_photo_dict(state, item)
        assert result.get("exif") is None

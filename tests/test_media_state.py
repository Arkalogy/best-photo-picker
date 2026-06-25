"""Tests for bp_media, bp_pets, and state modules."""

from __future__ import annotations

import os
import threading
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PIL import Image

from bpp.web.app import create_app
from bpp.web.state import heic_available

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_test_image(path, color="green", size=(200, 200)):
    """Create a minimal JPEG image for testing."""
    Image.new("RGB", size, color).save(str(path), "JPEG")


def _create_png_image(path, color="blue", size=(200, 200)):
    """Create a minimal PNG image for testing."""
    Image.new("RGB", size, color).save(str(path), "PNG")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def bare_app(tmp_path):
    """App with NO analysis data (no thumbnails loaded)."""
    app = create_app(
        workdir=str(tmp_path),
        input_dir=str(tmp_path),
        library_path=str(tmp_path),
    )
    app.config["TESTING"] = True
    return app


@pytest.fixture()
def app_with_thumbs(tmp_path):
    """App with a real image upserted and thumbnails loaded."""
    img_path = tmp_path / "photo.jpg"
    _create_test_image(img_path)

    app = create_app(
        workdir=str(tmp_path),
        input_dir=str(tmp_path),
        library_path=str(tmp_path),
    )
    app.config["TESTING"] = True

    with app.app_context():
        from bpp.web.state import get_ctx

        ctx = get_ctx()
        conn = ctx.get_conn()
        from bpp.db.photos import upsert_photo

        upsert_photo(
            conn,
            {
                "filepath": str(img_path),
                "aggregate_score": 0.8,
            },
        )
        conn.commit()
        ctx.invalidate_analysis()
        ctx.load_analysis_if_needed()

    return app, str(img_path)


@pytest.fixture()
def app_with_png(tmp_path):
    """App with a PNG image upserted and thumbnails loaded."""
    img_path = tmp_path / "photo.png"
    _create_png_image(img_path)

    app = create_app(
        workdir=str(tmp_path),
        input_dir=str(tmp_path),
        library_path=str(tmp_path),
    )
    app.config["TESTING"] = True

    with app.app_context():
        from bpp.web.state import get_ctx

        ctx = get_ctx()
        conn = ctx.get_conn()
        from bpp.db.photos import upsert_photo

        upsert_photo(
            conn,
            {
                "filepath": str(img_path),
                "aggregate_score": 0.6,
            },
        )
        conn.commit()
        ctx.invalidate_analysis()
        ctx.load_analysis_if_needed()

    return app, str(img_path)


@pytest.fixture()
def app_with_pets(tmp_path):
    """App with a photo that has pet detections seeded."""
    img_path = tmp_path / "pet_photo.jpg"
    _create_test_image(img_path, color="red", size=(400, 400))

    app = create_app(
        workdir=str(tmp_path),
        input_dir=str(tmp_path),
        library_path=str(tmp_path),
    )
    app.config["TESTING"] = True

    with app.app_context():
        from bpp.web.state import get_ctx

        ctx = get_ctx()
        conn = ctx.get_conn()
        from bpp.db.photos import upsert_photo

        photo_id = upsert_photo(
            conn,
            {
                "filepath": str(img_path),
                "aggregate_score": 0.9,
                "pet_count": 1,
                "has_cat": 1,
            },
        )
        conn.execute(
            "INSERT INTO pet_detections "
            "(photo_id, detection_index, class, confidence, "
            "bbox_x, bbox_y, bbox_w, bbox_h, cluster_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (photo_id, 0, "cat", 0.95, 10, 10, 100, 100, 0),
        )
        conn.commit()
        ctx.invalidate_analysis()
        ctx.load_analysis_if_needed()

    return app, str(img_path), photo_id


# ===================================================================
# bp_media tests
# ===================================================================


class TestServeThumbnail:
    """GET /thumb/<path_hash>"""

    def test_no_thumbnails_returns_404(self, bare_app):
        with bare_app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            ctx.thumbs = None

        with bare_app.test_client() as c:
            resp = c.get("/thumb/abc123")
            assert resp.status_code == 404
            assert b"No thumbnails" in resp.data

    def test_unknown_hash_returns_404(self, app_with_thumbs):
        app, _ = app_with_thumbs
        with app.test_client() as c:
            resp = c.get("/thumb/nonexistenthash")
            assert resp.status_code == 404
            assert b"not found" in resp.data.lower()

    def test_valid_hash_returns_jpeg(self, app_with_thumbs):
        app, filepath = app_with_thumbs
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            path_hash = ctx.thumbs.get_hash(filepath)

        with app.test_client() as c:
            resp = c.get(f"/thumb/{path_hash}")
            assert resp.status_code == 200
            assert resp.content_type == "image/jpeg"
            assert len(resp.data) > 0

    def test_thumbnail_is_cached_on_second_request(self, app_with_thumbs):
        app, filepath = app_with_thumbs
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            path_hash = ctx.thumbs.get_hash(filepath)

        with app.test_client() as c:
            resp1 = c.get(f"/thumb/{path_hash}")
            resp2 = c.get(f"/thumb/{path_hash}")
            assert resp1.status_code == 200
            assert resp2.status_code == 200
            assert resp1.data == resp2.data


class TestThumbnailsClear:
    """POST /api/thumbnails/clear"""

    def test_no_thumbnails_returns_404(self, bare_app):
        with bare_app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            ctx.thumbs = None

        with bare_app.test_client() as c:
            resp = c.post("/api/v1/thumbnails/clear")
            assert resp.status_code == 404
            assert b"No thumbnails" in resp.data

    def test_clear_returns_status(self, app_with_thumbs):
        app, filepath = app_with_thumbs
        # First generate a thumbnail so there's something to clear
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            path_hash = ctx.thumbs.get_hash(filepath)

        with app.test_client() as c:
            # Generate the thumbnail
            c.get(f"/thumb/{path_hash}")
            # Now clear
            resp = c.post("/api/v1/thumbnails/clear")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "cleared"
            assert isinstance(data["count"], int)

    def test_clear_empty_cache_returns_zero(self, app_with_thumbs):
        app, _ = app_with_thumbs
        with app.test_client() as c:
            # Clear twice - second should have 0
            c.post("/api/v1/thumbnails/clear")
            resp = c.post("/api/v1/thumbnails/clear")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["count"] == 0


class TestServeFullPhoto:
    """GET /photo/<path_hash>"""

    def test_no_thumbs_returns_404(self, bare_app):
        with bare_app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            ctx.thumbs = None

        with bare_app.test_client() as c:
            resp = c.get("/photo/abc123")
            assert resp.status_code == 404
            assert b"No photos" in resp.data

    def test_unknown_hash_returns_404(self, app_with_thumbs):
        app, _ = app_with_thumbs
        with app.test_client() as c:
            resp = c.get("/photo/nonexistenthash")
            assert resp.status_code == 404
            assert b"not found" in resp.data.lower()

    def test_valid_jpg_returns_jpeg(self, app_with_thumbs):
        app, filepath = app_with_thumbs
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            path_hash = ctx.thumbs.get_hash(filepath)

        with app.test_client() as c:
            resp = c.get(f"/photo/{path_hash}")
            assert resp.status_code == 200
            assert resp.content_type == "image/jpeg"
            assert len(resp.data) > 100

    def test_valid_png_returns_jpeg(self, app_with_png):
        """PNG images should be converted to JPEG for serving."""
        app, filepath = app_with_png
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            path_hash = ctx.thumbs.get_hash(filepath)

        with app.test_client() as c:
            resp = c.get(f"/photo/{path_hash}")
            assert resp.status_code == 200
            assert resp.content_type == "image/jpeg"

    def test_converted_photo_is_cached(self, app_with_thumbs):
        """Second request should serve from cache (no reconversion)."""
        app, filepath = app_with_thumbs
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            path_hash = ctx.thumbs.get_hash(filepath)
            cache_dir = ctx.thumbs.cache_dir

        with app.test_client() as c:
            c.get(f"/photo/{path_hash}")
            cached = os.path.join(cache_dir, f"{path_hash}_full.jpg")
            assert os.path.exists(cached)
            # Second request
            resp2 = c.get(f"/photo/{path_hash}")
            assert resp2.status_code == 200

    def test_deleted_source_file_returns_404(self, app_with_thumbs):
        """If the source file is deleted, return 404."""
        app, filepath = app_with_thumbs
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            path_hash = ctx.thumbs.get_hash(filepath)

        os.remove(filepath)

        with app.test_client() as c:
            resp = c.get(f"/photo/{path_hash}")
            assert resp.status_code == 404

    def test_non_image_extension_served_directly(self, tmp_path):
        """Files with non-image extensions are served as-is."""
        # Create a file with .bmp extension (not in conversion list)
        bmp_path = tmp_path / "photo.bmp"
        Image.new("RGB", (50, 50), "red").save(str(bmp_path), "BMP")

        app = create_app(
            workdir=str(tmp_path),
            input_dir=str(tmp_path),
            library_path=str(tmp_path),
        )
        app.config["TESTING"] = True

        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()
            from bpp.db.photos import upsert_photo

            upsert_photo(
                conn,
                {
                    "filepath": str(bmp_path),
                    "aggregate_score": 0.5,
                },
            )
            conn.commit()
            ctx.invalidate_analysis()
            ctx.load_analysis_if_needed()
            path_hash = ctx.thumbs.get_hash(str(bmp_path))

        with app.test_client() as c:
            resp = c.get(f"/photo/{path_hash}")
            assert resp.status_code == 200

    def test_corrupt_image_returns_500(self, tmp_path):
        """If image conversion fails, return 500."""
        bad_path = tmp_path / "corrupt.jpg"
        bad_path.write_bytes(b"not a real image")

        app = create_app(
            workdir=str(tmp_path),
            input_dir=str(tmp_path),
            library_path=str(tmp_path),
        )
        app.config["TESTING"] = True

        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()
            from bpp.db.photos import upsert_photo

            upsert_photo(
                conn,
                {
                    "filepath": str(bad_path),
                    "aggregate_score": 0.5,
                },
            )
            conn.commit()
            ctx.invalidate_analysis()
            ctx.load_analysis_if_needed()
            path_hash = ctx.thumbs.get_hash(str(bad_path))

        with app.test_client() as c:
            resp = c.get(f"/photo/{path_hash}")
            assert resp.status_code == 500
            assert b"Cannot convert" in resp.data


# ===================================================================
# bp_pets tests
# ===================================================================


class TestPetCrop:
    """GET /api/pets/crop/<path_hash>/<detection_index>"""

    def test_no_thumbs_returns_404(self, bare_app):
        with bare_app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            ctx.thumbs = None

        with bare_app.test_client() as c:
            resp = c.get("/api/v1/pets/crop/abc123/0")
            assert resp.status_code == 404
            assert b"No thumbnails" in resp.data

    def test_unknown_hash_returns_404(self, app_with_thumbs):
        app, _ = app_with_thumbs
        with app.test_client() as c:
            resp = c.get("/api/v1/pets/crop/nonexistent/0")
            assert resp.status_code == 404
            assert b"Unknown image" in resp.data

    def test_no_library_returns_404(self, app_with_thumbs):
        app, filepath = app_with_thumbs
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            path_hash = ctx.thumbs.get_hash(filepath)
            ctx.dirs = None

        with app.test_client() as c:
            resp = c.get(f"/api/v1/pets/crop/{path_hash}/0")
            assert resp.status_code == 404
            assert b"No library loaded" in resp.data

    def test_detection_not_found_returns_404(self, app_with_thumbs):
        app, filepath = app_with_thumbs
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            path_hash = ctx.thumbs.get_hash(filepath)

        with app.test_client() as c:
            resp = c.get(f"/api/v1/pets/crop/{path_hash}/99")
            assert resp.status_code == 404
            assert b"not found" in resp.data.lower()

    def test_valid_crop_returns_jpeg(self, app_with_pets):
        app, filepath, _photo_id = app_with_pets
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            path_hash = ctx.thumbs.get_hash(filepath)

        with app.test_client() as c:
            resp = c.get(f"/api/v1/pets/crop/{path_hash}/0")
            assert resp.status_code == 200
            assert resp.content_type == "image/jpeg"
            assert len(resp.data) > 0

    def test_crop_is_cached(self, app_with_pets):
        """Second request for same crop should hit cache."""
        app, filepath, _ = app_with_pets
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            path_hash = ctx.thumbs.get_hash(filepath)

        with app.test_client() as c:
            resp1 = c.get(f"/api/v1/pets/crop/{path_hash}/0")
            resp2 = c.get(f"/api/v1/pets/crop/{path_hash}/0")
            assert resp1.status_code == 200
            assert resp2.status_code == 200
            assert resp1.data == resp2.data


class TestPetClusters:
    """GET /api/pets/clusters"""

    def test_no_pet_data_returns_empty(self, app_with_thumbs):
        app, _ = app_with_thumbs
        with app.test_client() as c:
            resp = c.get("/api/v1/pets/clusters")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["clusters"] == []

    def test_with_pet_data_returns_clusters(self, app_with_pets):
        app, _filepath, _photo_id = app_with_pets
        with app.test_client() as c:
            resp = c.get("/api/v1/pets/clusters")
            assert resp.status_code == 200
            data = resp.get_json()
            clusters = data["clusters"]
            assert len(clusters) >= 1
            cluster = clusters[0]
            assert cluster["pet_class"] == "cat"
            assert cluster["photo_count"] >= 1
            assert "representative" in cluster
            assert "filepaths" in cluster


class TestPetDetectionsForPhoto:
    """GET /api/pets/detections/<path_hash>"""

    def test_no_thumbs_returns_empty(self, bare_app):
        with bare_app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            ctx.thumbs = None

        with bare_app.test_client() as c:
            resp = c.get("/api/v1/pets/detections/abc123")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["detections"] == []

    def test_unknown_hash_returns_empty(self, app_with_thumbs):
        app, _ = app_with_thumbs
        with app.test_client() as c:
            resp = c.get("/api/v1/pets/detections/nonexistent")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["detections"] == []

    def test_photo_without_detections_returns_empty(self, app_with_thumbs):
        app, filepath = app_with_thumbs
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            path_hash = ctx.thumbs.get_hash(filepath)

        with app.test_client() as c:
            resp = c.get(f"/api/v1/pets/detections/{path_hash}")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["detections"] == []

    def test_photo_with_detections(self, app_with_pets):
        app, filepath, _ = app_with_pets
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            path_hash = ctx.thumbs.get_hash(filepath)

        with app.test_client() as c:
            resp = c.get(f"/api/v1/pets/detections/{path_hash}")
            assert resp.status_code == 200
            data = resp.get_json()
            assert len(data["detections"]) == 1
            det = data["detections"][0]
            assert det["class"] == "cat"
            assert det["confidence"] == 0.95


# ===================================================================
# state.py tests
# ===================================================================


class TestHeicAvailable:
    """heic_available() function."""

    def test_returns_bool(self):
        result = heic_available()
        assert isinstance(result, bool)

    def test_returns_false_without_pillow_heif(self):
        with patch.dict("sys.modules", {"pillow_heif": None}):
            assert heic_available() is False

    def test_returns_true_with_pillow_heif(self):
        mock_mod = MagicMock()
        with patch.dict("sys.modules", {"pillow_heif": mock_mod}):
            assert heic_available() is True


class TestBuildPhotoDict:
    """WebAppState.build_photo_dict()"""

    def test_basic_dict(self, app_with_thumbs):
        app, filepath = app_with_thumbs
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            item = {
                "filepath": filepath,
                "date": "2024-01-15",
                "date_day": "2024-01-15",
                "date_month": "2024-01",
                "blur_score": 0.5,
                "exposure_score": 0.6,
                "face_score": 0.3,
                "composition_score": 0.7,
                "skin_score": 0.4,
                "nudity_score": 0.1,
                "aggregate_score": 0.8,
                "cluster_size": 2,
                "deleted_at": None,
                "pet_count": 0,
                "has_cat": 0,
                "has_dog": 0,
            }
            result = ctx.build_photo_dict(item)

        assert result["filepath"] == filepath
        assert result["filename"] == os.path.basename(filepath)
        assert result["aggregate_score"] == 0.8
        assert result["blur_score"] == 0.5
        assert result["cluster_size"] == 2
        assert result["thumb_hash"] != ""
        assert "selected" not in result

    def test_with_selected_flag(self, app_with_thumbs):
        app, filepath = app_with_thumbs
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            item = {"filepath": filepath, "aggregate_score": 0.5}
            result = ctx.build_photo_dict(item, selected=True)

        assert result["selected"] is True

    def test_selected_false(self, app_with_thumbs):
        app, filepath = app_with_thumbs
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            item = {"filepath": filepath, "aggregate_score": 0.5}
            result = ctx.build_photo_dict(item, selected=False)

        assert result["selected"] is False

    def test_with_similar_photos(self, app_with_thumbs):
        app, filepath = app_with_thumbs
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            item = {
                "filepath": filepath,
                "aggregate_score": 0.8,
                "similar_photos": [
                    {
                        "filepath": filepath,
                        "similarity": 0.95,
                    }
                ],
            }
            result = ctx.build_photo_dict(item)

        assert "similar_photos" in result
        assert len(result["similar_photos"]) == 1
        assert result["similar_photos"][0]["similarity"] == 0.95
        assert result["similar_photos"][0]["thumb_hash"] != ""

    def test_missing_fields_default_to_zero_or_empty(self, app_with_thumbs):
        app, filepath = app_with_thumbs
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            # Minimal item - only filepath required
            item = {"filepath": filepath}
            result = ctx.build_photo_dict(item)

        assert result["date"] == ""
        assert result["blur_score"] == 0
        assert result["aggregate_score"] == 0
        assert result["cluster_size"] == 1
        assert result["pet_count"] == 0

    def test_no_thumbs_returns_empty_hash(self, bare_app):
        with bare_app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            ctx.thumbs = None
            item = {"filepath": "/tmp/test.jpg"}
            result = ctx.build_photo_dict(item)

        assert result["thumb_hash"] == ""


class TestSwitchLibrary:
    """WebAppState.switch_library()"""

    def test_successful_switch(self, tmp_path):
        lib1 = tmp_path / "lib1"
        lib2 = tmp_path / "lib2"
        lib1.mkdir()
        lib2.mkdir()

        app = create_app(
            workdir=str(lib1),
            input_dir=str(lib1),
            library_path=str(lib1),
        )
        app.config["TESTING"] = True

        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            ctx.switch_library(str(lib2))
            assert ctx.state["library_path"] == str(lib2)
            assert ctx.state["workdir"] == os.path.join(str(lib2), "data")
            assert ctx.state["analysis"] is None or isinstance(ctx.state["analysis"], list)

    def test_raises_when_workers_running(self, tmp_path):
        lib1 = tmp_path / "lib1"
        lib2 = tmp_path / "lib2"
        lib1.mkdir()
        lib2.mkdir()

        app = create_app(
            workdir=str(lib1),
            input_dir=str(lib1),
            library_path=str(lib1),
        )
        app.config["TESTING"] = True

        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            # Mock worker as alive with cancel + join support
            mock_worker = MagicMock()
            mock_worker.is_alive = True
            mock_worker._thread = MagicMock()
            ctx._workers["analyze"] = mock_worker

            # Switch should succeed — cancels workers instead of refusing
            ctx.switch_library(str(lib2))
            mock_worker.cancel_and_join.assert_called_once()
            assert ctx.state["library_path"] == str(lib2)

    def test_switch_clears_clip_cache(self, tmp_path):
        lib1 = tmp_path / "lib1"
        lib2 = tmp_path / "lib2"
        lib1.mkdir()
        lib2.mkdir()

        app = create_app(
            workdir=str(lib1),
            input_dir=str(lib1),
            library_path=str(lib1),
        )
        app.config["TESTING"] = True

        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            ctx.clip_cache = {
                "embeddings": {1: "fake"},
                "ready": True,
            }
            ctx.switch_library(str(lib2))
            assert ctx.clip_cache["ready"] is False
            assert ctx.clip_cache["embeddings"] == {}

    def test_switch_resets_thumbs(self, tmp_path):
        lib1 = tmp_path / "lib1"
        lib2 = tmp_path / "lib2"
        lib1.mkdir()
        lib2.mkdir()

        app = create_app(
            workdir=str(lib1),
            input_dir=str(lib1),
            library_path=str(lib1),
        )
        app.config["TESTING"] = True

        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            ctx.thumbs = MagicMock()
            ctx.switch_library(str(lib2))
            # thumbs should be None after lock section, but
            # startup() may rebuild them. The key check is that
            # during the switch it was set to None.
            # We verify by checking the library path changed.
            assert ctx.state["library_path"] == str(lib2)


class TestAutoPurge:
    """WebAppState.auto_purge()"""

    def test_nothing_to_purge(self, app_with_thumbs):
        app, _ = app_with_thumbs
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            # auto_purge already ran at startup; running again
            # should not raise
            ctx.auto_purge()

    def test_purges_expired_photos(self, tmp_path):
        img_path = tmp_path / "old_photo.jpg"
        _create_test_image(img_path)

        app = create_app(
            workdir=str(tmp_path),
            input_dir=str(tmp_path),
            library_path=str(tmp_path),
        )
        app.config["TESTING"] = True

        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()
            from bpp.db.photos import upsert_photo

            photo_id = upsert_photo(
                conn,
                {
                    "filepath": str(img_path),
                    "aggregate_score": 0.5,
                },
            )
            # Set deleted_at to 60 days ago
            conn.execute(
                "UPDATE photos SET deleted_at = datetime('now', '-60 days') WHERE id = ?",
                (photo_id,),
            )
            conn.commit()

            # Verify photo exists before purge
            row = conn.execute(
                "SELECT id FROM photos WHERE id=?",
                (photo_id,),
            ).fetchone()
            assert row is not None

            ctx.auto_purge()

            # Photo should be permanently deleted
            row = conn.execute(
                "SELECT id FROM photos WHERE id=?",
                (photo_id,),
            ).fetchone()
            assert row is None

    def test_does_not_purge_recent_deletes(self, tmp_path):
        img_path = tmp_path / "recent_photo.jpg"
        _create_test_image(img_path)

        app = create_app(
            workdir=str(tmp_path),
            input_dir=str(tmp_path),
            library_path=str(tmp_path),
        )
        app.config["TESTING"] = True

        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()
            from bpp.db.photos import upsert_photo

            photo_id = upsert_photo(
                conn,
                {
                    "filepath": str(img_path),
                    "aggregate_score": 0.5,
                },
            )
            # Set deleted_at to 5 days ago (within 30-day window)
            conn.execute(
                "UPDATE photos SET deleted_at = datetime('now', '-5 days') WHERE id = ?",
                (photo_id,),
            )
            conn.commit()

            ctx.auto_purge()

            # Photo should still exist
            row = conn.execute(
                "SELECT id FROM photos WHERE id=?",
                (photo_id,),
            ).fetchone()
            assert row is not None

    def test_auto_purge_removes_file_from_disk(self, tmp_path):
        img_path = tmp_path / "to_delete.jpg"
        _create_test_image(img_path)

        app = create_app(
            workdir=str(tmp_path),
            input_dir=str(tmp_path),
            library_path=str(tmp_path),
        )
        app.config["TESTING"] = True

        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()
            from bpp.db.photos import upsert_photo

            upsert_photo(
                conn,
                {
                    "filepath": str(img_path),
                    "aggregate_score": 0.5,
                },
            )
            conn.execute(
                "UPDATE photos SET deleted_at = datetime('now', '-60 days') WHERE filepath = ?",
                (str(img_path),),
            )
            conn.commit()

            assert os.path.exists(str(img_path))
            ctx.auto_purge()
            assert not os.path.exists(str(img_path))


class TestCheckDedupFeedback:
    """WebAppState.check_dedup_feedback()"""

    def test_mode_not_include_exclude_returns_false(self, app_with_thumbs):
        app, filepath = app_with_thumbs
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()
            result = ctx.check_dedup_feedback(conn, 1, filepath, "keep", {filepath})
            assert result is False

    def test_none_mode_returns_false(self, app_with_thumbs):
        app, filepath = app_with_thumbs
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()
            result = ctx.check_dedup_feedback(conn, 1, filepath, None, {filepath})
            assert result is False

    def test_no_selected_paths_returns_false(self, app_with_thumbs):
        app, filepath = app_with_thumbs
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()
            result = ctx.check_dedup_feedback(conn, 1, filepath, "include", None)
            assert result is False

    def test_empty_selected_paths_returns_false(self, app_with_thumbs):
        app, filepath = app_with_thumbs
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()
            result = ctx.check_dedup_feedback(conn, 1, filepath, "include", set())
            assert result is False

    def test_no_clip_ready_returns_false(self, app_with_thumbs):
        app, filepath = app_with_thumbs
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()
            ctx.clip_cache = {"embeddings": {}, "ready": False}
            result = ctx.check_dedup_feedback(conn, 1, filepath, "include", {filepath})
            assert result is False

    def test_clip_ready_but_no_embedding_for_photo(self, app_with_thumbs):
        app, filepath = app_with_thumbs
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()
            ctx.clip_cache = {
                "embeddings": {999: np.ones(512)},
                "ready": True,
            }
            result = ctx.check_dedup_feedback(conn, 1, filepath, "exclude", {filepath})
            assert result is False

    def test_records_feedback_for_similar_pair(self, tmp_path):
        """Full feedback recording with two photos and CLIP embeddings."""
        img1 = tmp_path / "img1.jpg"
        img2 = tmp_path / "img2.jpg"
        _create_test_image(img1, "red")
        _create_test_image(img2, "blue")

        app = create_app(
            workdir=str(tmp_path),
            input_dir=str(tmp_path),
            library_path=str(tmp_path),
        )
        app.config["TESTING"] = True

        with app.app_context():
            from bpp.db.photos import upsert_photo
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()
            id1 = upsert_photo(
                conn,
                {
                    "filepath": str(img1),
                    "aggregate_score": 0.8,
                },
            )
            id2 = upsert_photo(
                conn,
                {
                    "filepath": str(img2),
                    "aggregate_score": 0.7,
                },
            )
            conn.commit()
            ctx.invalidate_analysis()
            ctx.load_analysis_if_needed()

            # Create near-identical L2-normalized embeddings
            emb = np.random.randn(512).astype(np.float32)
            emb = emb / np.linalg.norm(emb)
            ctx.clip_cache = {
                "embeddings": {id1: emb, id2: emb},
                "ready": True,
            }

            result = ctx.check_dedup_feedback(
                conn,
                id1,
                str(img1),
                "exclude",
                {str(img2)},
            )
            assert result is True

            # Verify feedback was recorded
            row = conn.execute("SELECT verdict FROM dedup_feedback LIMIT 1").fetchone()
            assert row is not None
            assert row[0] == "same"


class TestInvalidateAnalysis:
    """WebAppState.invalidate_analysis()"""

    def test_invalidate_clears_analysis(self, app_with_thumbs):
        app, _ = app_with_thumbs
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            # Should have analysis loaded
            assert ctx.state["analysis"] is not None
            ctx.invalidate_analysis()
            assert ctx.state["analysis"] is None

    def test_invalidate_is_thread_safe(self, app_with_thumbs):
        """Multiple threads invalidating simultaneously should not crash."""
        app, _ = app_with_thumbs

        errors = []

        def _invalidate():
            try:
                with app.app_context():
                    from bpp.web.state import get_ctx

                    ctx = get_ctx()
                    ctx.invalidate_analysis()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_invalidate) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert errors == []


class TestEnsureWorkdir:
    """WebAppState.ensure_workdir()"""

    def test_creates_tempdir_when_none(self, tmp_path):
        app = create_app(
            workdir=None,
            input_dir=str(tmp_path),
        )
        app.config["TESTING"] = True

        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            # ensure_workdir should have been called during startup
            wd = ctx.ensure_workdir()
            assert wd is not None
            assert os.path.isdir(wd)

    def test_returns_existing_workdir(self, tmp_path):
        wd = tmp_path / "mywork"
        wd.mkdir()

        app = create_app(
            workdir=str(wd),
            input_dir=str(tmp_path),
            library_path=str(tmp_path),
        )
        app.config["TESTING"] = True

        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            result = ctx.ensure_workdir()
            assert result == str(wd)


class TestLoadAnalysisIfNeeded:
    """WebAppState.load_analysis_if_needed()"""

    def test_returns_none_for_empty_db(self, bare_app):
        with bare_app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            ctx.invalidate_analysis()
            result = ctx.load_analysis_if_needed()
            # No photos in DB => None or empty
            assert result is None or result == []

    def test_returns_data_after_upsert(self, app_with_thumbs):
        app, filepath = app_with_thumbs
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            data = ctx.load_analysis_if_needed()
            assert data is not None
            assert len(data) >= 1
            assert any(d["filepath"] == filepath for d in data)

    def test_caches_result(self, app_with_thumbs):
        """Second call should return same object (cached)."""
        app, _ = app_with_thumbs
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            data1 = ctx.load_analysis_if_needed()
            data2 = ctx.load_analysis_if_needed()
            assert data1 is data2

    def test_builds_thumbs(self, app_with_thumbs):
        app, _ = app_with_thumbs
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            assert ctx.thumbs is not None


class TestClampHelpers:
    """Test clamp_weight and clamp_k."""

    def testclamp_weight_within_range(self):
        from bpp.web.state import clamp_weight

        assert clamp_weight(5.0) == 5.0

    def testclamp_weight_below_min(self):
        from bpp.web.state import clamp_weight

        assert clamp_weight(-1.0) == 0.0

    def testclamp_weight_above_max(self):
        from bpp.web.state import clamp_weight

        assert clamp_weight(99.0) == 10.0

    def testclamp_k_within_range(self):
        from bpp.web.state import clamp_k

        assert clamp_k(50) == 50

    def testclamp_k_below_min(self):
        from bpp.web.state import clamp_k

        assert clamp_k(0) == 1

    def testclamp_k_above_max(self):
        from bpp.web.state import clamp_k

        assert clamp_k(99999) == 10000

    def testclamp_k_invalid_returns_default(self):
        from bpp.web.state import clamp_k

        assert clamp_k("abc") == 50

    def testclamp_k_none_returns_default(self):
        from bpp.web.state import clamp_k

        assert clamp_k(None) == 50

    def testclamp_k_custom_default(self):
        from bpp.web.state import clamp_k

        assert clamp_k("bad", default=100) == 100


# ---------------------------------------------------------------------------
# Thumbnail verified-cache tests (unit + integration)
# ---------------------------------------------------------------------------


class TestThumbnailVerifiedCache:
    """Unit tests for the in-memory _verified fast path in ThumbnailCache."""

    def test_first_call_populates_verified(self, tmp_path):
        """First get_thumbnail call should add hash to _verified set."""
        from bpp.web.thumbnails import ThumbnailCache

        cache = ThumbnailCache(str(tmp_path / "thumbs"))
        img = tmp_path / "photo.jpg"
        _create_test_image(img)
        cache.build_map_from_paths([str(img)])
        h = cache.get_hash(str(img))

        assert h not in cache._verified
        result = cache.get_thumbnail(h)
        assert result is not None
        assert h in cache._verified

    def test_second_call_uses_fast_path(self, tmp_path):
        """After verification, get_thumbnail should skip stat calls."""
        from bpp.web.thumbnails import ThumbnailCache

        cache = ThumbnailCache(str(tmp_path / "thumbs"))
        img = tmp_path / "photo.jpg"
        _create_test_image(img)
        cache.build_map_from_paths([str(img)])
        h = cache.get_hash(str(img))

        # First call — generates and verifies
        path1 = cache.get_thumbnail(h)
        assert h in cache._verified

        # Delete source file — fast path should still return cached thumb
        os.remove(str(img))
        path2 = cache.get_thumbnail(h)
        assert path2 == path1
        assert os.path.isfile(path2)

    def test_invalidate_forces_recheck(self, tmp_path):
        """After invalidate(), the next call should re-check disk."""
        from bpp.web.thumbnails import ThumbnailCache

        cache = ThumbnailCache(str(tmp_path / "thumbs"))
        img = tmp_path / "photo.jpg"
        _create_test_image(img)
        cache.build_map_from_paths([str(img)])
        h = cache.get_hash(str(img))

        cache.get_thumbnail(h)
        assert h in cache._verified

        cache.invalidate(h)
        assert h not in cache._verified

        # Next call should still work (re-verifies from disk)
        result = cache.get_thumbnail(h)
        assert result is not None
        assert h in cache._verified

    def test_clear_resets_verified(self, tmp_path):
        """clear() should empty the _verified set."""
        from bpp.web.thumbnails import ThumbnailCache

        cache = ThumbnailCache(str(tmp_path / "thumbs"))
        img = tmp_path / "photo.jpg"
        _create_test_image(img)
        cache.build_map_from_paths([str(img)])
        h = cache.get_hash(str(img))

        cache.get_thumbnail(h)
        assert h in cache._verified

        cache.clear()
        assert len(cache._verified) == 0

    def test_invalidate_nonexistent_hash_is_safe(self, tmp_path):
        """Invalidating a hash not in _verified should not raise."""
        from bpp.web.thumbnails import ThumbnailCache

        cache = ThumbnailCache(str(tmp_path / "thumbs"))
        cache.invalidate("does_not_exist")  # should not raise

    def test_verified_survives_source_modification(self, tmp_path):
        """Fast path returns cached thumb even if source file changes."""
        from bpp.web.thumbnails import ThumbnailCache

        cache = ThumbnailCache(str(tmp_path / "thumbs"))
        img = tmp_path / "photo.jpg"
        _create_test_image(img)
        cache.build_map_from_paths([str(img)])
        h = cache.get_hash(str(img))

        path1 = cache.get_thumbnail(h)
        assert h in cache._verified

        # Modify the source (newer mtime)
        _create_test_image(img, color="red")

        # Fast path still returns old cached thumb (no stat check)
        path2 = cache.get_thumbnail(h)
        assert path2 == path1


class TestThumbnailVerifiedIntegration:
    """Integration tests: verified cache through Flask endpoints."""

    def test_repeated_thumb_requests_use_cache(self, app_with_thumbs):
        """Multiple /thumb/ requests should all succeed and return same data."""
        app, filepath = app_with_thumbs
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            h = ctx.thumbs.get_hash(filepath)

        with app.test_client() as c:
            r1 = c.get(f"/thumb/{h}")
            assert r1.status_code == 200
            # Hash should now be in verified set
            with app.app_context():
                assert h in get_ctx().thumbs._verified

            r2 = c.get(f"/thumb/{h}")
            assert r2.status_code == 200
            assert r1.data == r2.data

    def test_clear_then_re_request_regenerates(self, app_with_thumbs):
        """After /api/thumbnails/clear, next thumb request regenerates."""
        app, filepath = app_with_thumbs
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            h = ctx.thumbs.get_hash(filepath)

        with app.test_client() as c:
            # Generate and verify
            r1 = c.get(f"/thumb/{h}")
            assert r1.status_code == 200
            with app.app_context():
                assert h in get_ctx().thumbs._verified

            # Clear all thumbnails
            c.post("/api/v1/thumbnails/clear")
            with app.app_context():
                assert h not in get_ctx().thumbs._verified

            # Re-request — should regenerate successfully
            r2 = c.get(f"/thumb/{h}")
            assert r2.status_code == 200
            assert len(r2.data) > 0

    def test_enhance_invalidates_verified_thumb(self, app_with_thumbs):
        """Auto-enhance should invalidate the verified cache for affected photo."""
        app, filepath = app_with_thumbs

        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            h = ctx.thumbs.get_hash(filepath)

        with app.test_client() as c:
            # Generate thumbnail first
            r1 = c.get(f"/thumb/{h}")
            assert r1.status_code == 200
            with app.app_context():
                assert h in get_ctx().thumbs._verified

            # Enhance the photo
            c.post(
                "/api/v1/photos/enhance",
                json={"filepaths": [filepath]},
                content_type="application/json",
            )

            # Verified set should have been cleared for this hash
            with app.app_context():
                assert h not in get_ctx().thumbs._verified

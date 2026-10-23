"""Tests for bp_media, bp_pets, and state modules — part 1 of 4."""

# Shared fixtures are imported by name and re-bound as test parameters.
# ruff: noqa: F811

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from PIL import Image

from bpp.web.app import create_app
from bpp.web.state import heic_available
from tests._media_state_shared import (  # noqa: F401
    _create_png_image,
    _create_test_image,
    app_with_pets,
    app_with_png,
    app_with_thumbs,
    bare_app,
)

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

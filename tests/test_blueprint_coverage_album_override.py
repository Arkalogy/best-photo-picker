"""Comprehensive Flask blueprint route tests for maximum coverage — part 2 of 5: TestAlbumOverride,
TestAlbumFavorite, TestAlbumBatchOverride."""

# Shared fixtures are imported by name and re-bound as test parameters.
# ruff: noqa: F811

from __future__ import annotations

from tests._blueprint_coverage_shared import (  # noqa: F401
    _add_photos_to_album,
    _create_manual_album,
    _get_all_album_id,
    _get_ctx,
    _get_filepaths,
    _get_photo_ids,
    _make_analysis,
    bp_app,
    client,
)


class TestAlbumOverride:
    """Tests for POST /api/albums/<id>/override."""

    def test_override_missing_filepath(self, client, bp_app):
        all_id = _get_all_album_id(bp_app)
        resp = client.post(
            f"/api/v1/albums/{all_id}/override",
            json={"mode": "include"},
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert "filepath required" in resp.get_json()["error"]

    def test_override_photo_not_found(self, client, bp_app):
        all_id = _get_all_album_id(bp_app)
        resp = client.post(
            f"/api/v1/albums/{all_id}/override",
            json={"filepath": "/nonexistent/photo.jpg", "mode": "include"},
            content_type="application/json",
        )
        assert resp.status_code == 404

    def test_override_include(self, client, bp_app):
        all_id = _get_all_album_id(bp_app)
        filepaths = _get_filepaths(bp_app)
        resp = client.post(
            f"/api/v1/albums/{all_id}/override",
            json={"filepath": filepaths[0], "mode": "include"},
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "ok"

    def test_override_exclude(self, client, bp_app):
        all_id = _get_all_album_id(bp_app)
        filepaths = _get_filepaths(bp_app)
        resp = client.post(
            f"/api/v1/albums/{all_id}/override",
            json={"filepath": filepaths[1], "mode": "exclude"},
            content_type="application/json",
        )
        assert resp.status_code == 200

    def test_override_clear(self, client, bp_app):
        all_id = _get_all_album_id(bp_app)
        filepaths = _get_filepaths(bp_app)
        resp = client.post(
            f"/api/v1/albums/{all_id}/override",
            json={"filepath": filepaths[0], "mode": None},
            content_type="application/json",
        )
        assert resp.status_code == 200


class TestAlbumFavorite:
    """Tests for POST /api/albums/<id>/favorite."""

    def test_favorite_missing_filepath(self, client, bp_app):
        all_id = _get_all_album_id(bp_app)
        resp = client.post(
            f"/api/v1/albums/{all_id}/favorite",
            json={},
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert "filepath required" in resp.get_json()["error"]

    def test_favorite_photo_not_found(self, client, bp_app):
        all_id = _get_all_album_id(bp_app)
        resp = client.post(
            f"/api/v1/albums/{all_id}/favorite",
            json={"filepath": "/nonexistent/photo.jpg"},
            content_type="application/json",
        )
        assert resp.status_code == 404

    def test_toggle_favorite(self, client, bp_app):
        all_id = _get_all_album_id(bp_app)
        filepaths = _get_filepaths(bp_app)
        resp = client.post(
            f"/api/v1/albums/{all_id}/favorite",
            json={"filepath": filepaths[0]},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "favorite" in data

    def test_toggle_favorite_twice(self, client, bp_app):
        all_id = _get_all_album_id(bp_app)
        filepaths = _get_filepaths(bp_app)
        resp1 = client.post(
            f"/api/v1/albums/{all_id}/favorite",
            json={"filepath": filepaths[0]},
            content_type="application/json",
        )
        state1 = resp1.get_json()["favorite"]
        resp2 = client.post(
            f"/api/v1/albums/{all_id}/favorite",
            json={"filepath": filepaths[0]},
            content_type="application/json",
        )
        state2 = resp2.get_json()["favorite"]
        assert state1 != state2


class TestAlbumBatchOverride:
    """Tests for POST /api/albums/<id>/batch/override."""

    def test_batch_override_missing_filepaths(self, client, bp_app):
        all_id = _get_all_album_id(bp_app)
        resp = client.post(
            f"/api/v1/albums/{all_id}/batch/override",
            json={"mode": "include"},
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert "filepaths required" in resp.get_json()["error"]

    def test_batch_override_empty_filepaths(self, client, bp_app):
        all_id = _get_all_album_id(bp_app)
        resp = client.post(
            f"/api/v1/albums/{all_id}/batch/override",
            json={"filepaths": [], "mode": "include"},
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_batch_override_include(self, client, bp_app):
        all_id = _get_all_album_id(bp_app)
        filepaths = _get_filepaths(bp_app)
        resp = client.post(
            f"/api/v1/albums/{all_id}/batch/override",
            json={"filepaths": filepaths[:3], "mode": "include"},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["count"] == 3

    def test_batch_override_clear(self, client, bp_app):
        all_id = _get_all_album_id(bp_app)
        filepaths = _get_filepaths(bp_app)
        resp = client.post(
            f"/api/v1/albums/{all_id}/batch/override",
            json={"filepaths": filepaths[:2], "mode": None},
            content_type="application/json",
        )
        assert resp.status_code == 200


class TestAlbumBatchFavorite:
    """Tests for POST /api/albums/<id>/batch/favorite."""

    def test_batch_favorite_missing_filepaths(self, client, bp_app):
        all_id = _get_all_album_id(bp_app)
        resp = client.post(
            f"/api/v1/albums/{all_id}/batch/favorite",
            json={"favorite": True},
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert "filepaths required" in resp.get_json()["error"]

    def test_batch_favorite_set_true(self, client, bp_app):
        all_id = _get_all_album_id(bp_app)
        filepaths = _get_filepaths(bp_app)
        resp = client.post(
            f"/api/v1/albums/{all_id}/batch/favorite",
            json={"filepaths": filepaths[:4], "favorite": True},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["count"] == 4

    def test_batch_favorite_set_false(self, client, bp_app):
        all_id = _get_all_album_id(bp_app)
        filepaths = _get_filepaths(bp_app)
        # First set favorites
        client.post(
            f"/api/v1/albums/{all_id}/batch/favorite",
            json={"filepaths": filepaths[:2], "favorite": True},
            content_type="application/json",
        )
        # Then remove them
        resp = client.post(
            f"/api/v1/albums/{all_id}/batch/favorite",
            json={"filepaths": filepaths[:2], "favorite": False},
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.get_json()["count"] == 2


class TestAlbumAddRemovePhotos:
    """Tests for POST /api/albums/<id>/add-photos and remove-photos."""

    def test_add_photos_missing_filepaths(self, client):
        album_id = _create_manual_album(client)
        resp = client.post(
            f"/api/v1/albums/{album_id}/add-photos",
            json={},
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert "filepaths required" in resp.get_json()["error"]

    def test_add_photos_album_not_found(self, client, bp_app):
        filepaths = _get_filepaths(bp_app)
        resp = client.post(
            "/api/v1/albums/99999/add-photos",
            json={"filepaths": filepaths[:1]},
            content_type="application/json",
        )
        assert resp.status_code == 404

    def test_add_photos_to_album(self, client, bp_app):
        album_id = _create_manual_album(client, "AddTest")
        filepaths = _get_filepaths(bp_app)
        resp = client.post(
            f"/api/v1/albums/{album_id}/add-photos",
            json={"filepaths": filepaths[:3]},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["count"] == 3

    def test_remove_photos_missing_filepaths(self, client):
        album_id = _create_manual_album(client)
        resp = client.post(
            f"/api/v1/albums/{album_id}/remove-photos",
            json={},
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_remove_photos_album_not_found(self, client, bp_app):
        filepaths = _get_filepaths(bp_app)
        resp = client.post(
            "/api/v1/albums/99999/remove-photos",
            json={"filepaths": filepaths[:1]},
            content_type="application/json",
        )
        assert resp.status_code == 404

    def test_remove_photos_from_album(self, client, bp_app):
        album_id = _create_manual_album(client, "RemoveTest")
        filepaths = _get_filepaths(bp_app)
        # Add then remove
        client.post(
            f"/api/v1/albums/{album_id}/add-photos",
            json={"filepaths": filepaths[:5]},
            content_type="application/json",
        )
        resp = client.post(
            f"/api/v1/albums/{album_id}/remove-photos",
            json={"filepaths": filepaths[:2]},
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.get_json()["count"] == 2
        # Verify remaining
        photos_resp = client.get(f"/api/v1/albums/{album_id}/photos")
        assert photos_resp.get_json()["count"] == 3


class TestRefreshSmartAlbums:
    """Tests for POST /api/albums/refresh-smart."""

    def test_refresh_smart_albums(self, client):
        resp = client.post("/api/v1/albums/refresh-smart")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "refreshed"
        assert "albums" in data
        assert isinstance(data["albums"], list)


# ===========================================================================
# bp_pets.py
# ===========================================================================


class TestPetsClusters:
    """Tests for GET /api/pets/clusters."""

    def test_pet_clusters_empty(self, client):
        resp = client.get("/api/v1/pets/clusters")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["clusters"] == []

    def test_pet_clusters_with_data(self, client, bp_app):
        """Insert pet detection data and verify clusters are returned."""
        ctx = _get_ctx(bp_app)
        with bp_app.app_context():
            conn = ctx.get_conn()
            photo_ids = [r[0] for r in conn.execute("SELECT id FROM photos LIMIT 3").fetchall()]
            for pid in photo_ids:
                conn.execute(
                    "INSERT INTO pet_detections"
                    " (photo_id, detection_index, class, confidence,"
                    " bbox_x, bbox_y, bbox_w, bbox_h, cluster_id)"
                    " VALUES (?, 0, 'cat', 0.95, 10, 10, 100, 100, 0)",
                    (pid,),
                )
            conn.commit()

        resp = client.get("/api/v1/pets/clusters")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["clusters"]) >= 1
        cluster = data["clusters"][0]
        assert cluster["pet_class"] == "cat"
        assert cluster["photo_count"] >= 1


class TestPetsCrop:
    """Tests for GET /api/pets/crop/<hash>/<idx>."""

    def test_pet_crop_unknown_hash(self, client):
        resp = client.get("/api/v1/pets/crop/deadbeef12345678/0")
        assert resp.status_code == 404

    def test_pet_crop_no_thumbnails_context(self, client, bp_app):
        """When thumbs is None, expect 404."""
        ctx = _get_ctx(bp_app)
        orig_thumbs = ctx.thumbs
        ctx.thumbs = None
        try:
            resp = client.get("/api/v1/pets/crop/deadbeef12345678/0")
            assert resp.status_code == 404
            assert "no thumbnails" in resp.get_json()["error"].lower()
        finally:
            ctx.thumbs = orig_thumbs


class TestPetsDetections:
    """Tests for GET /api/pets/detections/<hash>."""

    def test_detections_unknown_hash(self, client):
        resp = client.get("/api/v1/pets/detections/deadbeef12345678")
        assert resp.status_code == 200
        assert resp.get_json()["detections"] == []

    def test_detections_no_data(self, client, bp_app):
        """Even with a known photo hash, returns empty if no detections."""
        ctx = _get_ctx(bp_app)
        with bp_app.app_context():
            filepaths = _get_filepaths(bp_app)
            if ctx.thumbs:
                path_hash = ctx.thumbs.get_hash(filepaths[0])
                resp = client.get(f"/api/v1/pets/detections/{path_hash}")
                assert resp.status_code == 200
                assert resp.get_json()["detections"] == []

    def test_detections_no_thumbs(self, client, bp_app):
        """When thumbs is None, returns empty."""
        ctx = _get_ctx(bp_app)
        orig_thumbs = ctx.thumbs
        ctx.thumbs = None
        try:
            resp = client.get("/api/v1/pets/detections/somehash")
            assert resp.status_code == 200
            assert resp.get_json()["detections"] == []
        finally:
            ctx.thumbs = orig_thumbs


# ===========================================================================
# bp_faces.py — Groups & face management
# ===========================================================================


class TestGroupsRoute:
    """Tests for GET /api/groups."""

    def test_groups_empty(self, client):
        resp = client.get("/api/v1/groups")
        assert resp.status_code == 200
        assert resp.get_json()["groups"] == []

    def test_groups_with_min_photos_param(self, client):
        resp = client.get("/api/v1/groups?min_photos=5")
        assert resp.status_code == 200
        assert resp.get_json()["groups"] == []

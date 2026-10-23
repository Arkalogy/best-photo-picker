"""Comprehensive Flask blueprint route tests for maximum coverage — part 1 of 5."""

# Shared fixtures are imported by name and re-bound as test parameters.
# ruff: noqa: F811

from __future__ import annotations

import pytest

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

# ===========================================================================
# bp_core.py — Presets
# ===========================================================================


class TestPresetsRoutes:
    """Tests for POST /api/presets and DELETE /api/presets/<name>."""

    def test_save_preset(self, client):
        resp = client.post(
            "/api/v1/presets",
            json={"name": "MyPreset", "settings": {"blur_weight": 2.0}},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "saved"
        assert data["name"] == "MyPreset"

    def test_save_preset_name_required(self, client):
        resp = client.post(
            "/api/v1/presets",
            json={"settings": {"blur_weight": 1.0}},
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert "required" in resp.get_json()["error"].lower()

    def test_save_preset_empty_name(self, client):
        resp = client.post(
            "/api/v1/presets",
            json={"name": "   ", "settings": {}},
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_save_preset_no_body(self, client):
        resp = client.post("/api/v1/presets", content_type="application/json")
        assert resp.status_code == 400

    def test_save_preset_overwrite(self, client):
        client.post(
            "/api/v1/presets",
            json={"name": "P1", "settings": {"blur_weight": 1.0}},
            content_type="application/json",
        )
        client.post(
            "/api/v1/presets",
            json={"name": "P1", "settings": {"blur_weight": 9.0}},
            content_type="application/json",
        )
        resp = client.get("/api/v1/presets")
        presets = resp.get_json()["presets"]
        assert presets["P1"]["blur_weight"] == 9.0

    def test_delete_preset(self, client):
        client.post(
            "/api/v1/presets",
            json={"name": "ToDelete", "settings": {}},
            content_type="application/json",
        )
        resp = client.delete("/api/v1/presets/ToDelete")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "deleted"

    def test_delete_preset_not_found(self, client):
        resp = client.delete("/api/v1/presets/NonExistent")
        assert resp.status_code == 404
        assert "not found" in resp.get_json()["error"].lower()

    def test_list_presets_after_save(self, client):
        client.post(
            "/api/v1/presets",
            json={"name": "Alpha", "settings": {"k": 10}},
            content_type="application/json",
        )
        resp = client.get("/api/v1/presets")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "Alpha" in data["presets"]

    def test_save_preset_empty_settings(self, client):
        resp = client.post(
            "/api/v1/presets",
            json={"name": "Empty", "settings": {}},
            content_type="application/json",
        )
        assert resp.status_code == 200


# ===========================================================================
# bp_albums.py — Album CRUD
# ===========================================================================


class TestAlbumCreate:
    """Tests for POST /api/albums."""

    def test_create_album(self, client):
        resp = client.post(
            "/api/v1/albums",
            json={"name": "Vacation"},
            content_type="application/json",
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["status"] == "created"
        assert isinstance(data["id"], int)

    def test_create_album_empty_name(self, client):
        resp = client.post(
            "/api/v1/albums",
            json={"name": ""},
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert "required" in resp.get_json()["error"].lower()

    def test_create_album_whitespace_name(self, client):
        resp = client.post(
            "/api/v1/albums",
            json={"name": "   "},
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_create_album_name_too_long(self, client):
        resp = client.post(
            "/api/v1/albums",
            json={"name": "x" * 256},
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert "too long" in resp.get_json()["error"].lower()

    def test_create_album_with_config(self, client):
        resp = client.post(
            "/api/v1/albums",
            json={"name": "Custom", "config": {"blur_weight": 3.0}, "k": 25},
            content_type="application/json",
        )
        assert resp.status_code == 201

    def test_create_album_no_body(self, client):
        resp = client.post("/api/v1/albums", content_type="application/json")
        assert resp.status_code == 400


class TestAlbumGet:
    """Tests for GET /api/albums/<id>."""

    def test_get_album(self, client, bp_app):
        album_id = _create_manual_album(client, "GetTest")
        resp = client.get(f"/api/v1/albums/{album_id}")
        assert resp.status_code == 200
        album = resp.get_json()["album"]
        assert album["name"] == "GetTest"
        assert album["album_type"] == "manual"

    def test_get_album_not_found(self, client):
        resp = client.get("/api/v1/albums/99999")
        assert resp.status_code == 404
        assert "not found" in resp.get_json()["error"].lower()

    def test_get_all_photos_album(self, client, bp_app):
        all_id = _get_all_album_id(bp_app)
        assert all_id is not None
        resp = client.get(f"/api/v1/albums/{all_id}")
        assert resp.status_code == 200
        assert resp.get_json()["album"]["album_type"] == "all"


class TestAlbumUpdate:
    """Tests for PUT /api/albums/<id>."""

    def test_update_album_name(self, client):
        album_id = _create_manual_album(client, "OldName")
        resp = client.put(
            f"/api/v1/albums/{album_id}",
            json={"name": "NewName"},
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "updated"
        # Verify
        get_resp = client.get(f"/api/v1/albums/{album_id}")
        assert get_resp.get_json()["album"]["name"] == "NewName"

    def test_update_album_config(self, client):
        album_id = _create_manual_album(client)
        resp = client.put(
            f"/api/v1/albums/{album_id}",
            json={"config": {"blur_weight": 5.0}},
            content_type="application/json",
        )
        assert resp.status_code == 200

    def test_update_album_k(self, client):
        album_id = _create_manual_album(client)
        resp = client.put(
            f"/api/v1/albums/{album_id}",
            json={"k": 100},
            content_type="application/json",
        )
        assert resp.status_code == 200

    def test_update_album_not_found(self, client):
        resp = client.put(
            "/api/v1/albums/99999",
            json={"name": "X"},
            content_type="application/json",
        )
        assert resp.status_code == 404

    def test_update_album_name_too_long(self, client):
        album_id = _create_manual_album(client)
        resp = client.put(
            f"/api/v1/albums/{album_id}",
            json={"name": "z" * 256},
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert "too long" in resp.get_json()["error"].lower()

    def test_update_album_empty_body(self, client):
        album_id = _create_manual_album(client)
        resp = client.put(
            f"/api/v1/albums/{album_id}",
            json={},
            content_type="application/json",
        )
        assert resp.status_code == 200


class TestAlbumDelete:
    """Tests for DELETE /api/albums/<id>."""

    def test_delete_album(self, client):
        album_id = _create_manual_album(client, "ToRemove")
        resp = client.delete(f"/api/v1/albums/{album_id}")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "deleted"
        # Verify it's gone
        assert client.get(f"/api/v1/albums/{album_id}").status_code == 404

    def test_delete_album_not_found(self, client):
        resp = client.delete("/api/v1/albums/99999")
        assert resp.status_code == 404

    def test_delete_all_photos_album_forbidden(self, client, bp_app):
        all_id = _get_all_album_id(bp_app)
        resp = client.delete(f"/api/v1/albums/{all_id}")
        assert resp.status_code == 400
        assert "cannot delete" in resp.get_json()["error"].lower()


class TestAlbumPhotos:
    """Tests for GET /api/albums/<id>/photos."""

    def test_get_album_photos(self, client, bp_app):
        all_id = _get_all_album_id(bp_app)
        resp = client.get(f"/api/v1/albums/{all_id}/photos")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "photos" in data
        assert "count" in data
        assert "album" in data
        assert data["count"] == 10

    def test_get_album_photos_not_found(self, client):
        resp = client.get("/api/v1/albums/99999/photos")
        assert resp.status_code == 404

    def test_get_album_photos_empty_album(self, client):
        album_id = _create_manual_album(client)
        resp = client.get(f"/api/v1/albums/{album_id}/photos")
        assert resp.status_code == 200
        assert resp.get_json()["count"] == 0
        assert resp.get_json()["photos"] == []


class TestAlbumStats:
    """Tests for GET /api/albums/<id>/stats."""

    def test_get_album_stats(self, client, bp_app):
        all_id = _get_all_album_id(bp_app)
        resp = client.get(f"/api/v1/albums/{all_id}/stats")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "total" in data
        assert "date_min" in data
        assert "date_max" in data
        assert "avg_score" in data
        assert "disk_size" in data
        assert "people_count" in data
        assert "gps_count" in data
        assert "video_count" in data
        assert data["total"] == 10

    def test_get_stats_not_found(self, client):
        resp = client.get("/api/v1/albums/99999/stats")
        assert resp.status_code == 404

    def test_get_stats_empty_album(self, client):
        album_id = _create_manual_album(client)
        resp = client.get(f"/api/v1/albums/{album_id}/stats")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] == 0
        assert data["disk_size"] == 0


class TestAlbumRecompute:
    """Tests for POST /api/albums/<id>/recompute."""

    def test_recompute_album_not_found(self, client):
        resp = client.post("/api/v1/albums/99999/recompute")
        assert resp.status_code == 404

    def test_recompute_empty_album(self, client):
        album_id = _create_manual_album(client)
        resp = client.post(f"/api/v1/albums/{album_id}/recompute")
        assert resp.status_code == 404
        assert "no photos" in resp.get_json()["error"].lower()

    def test_recompute_all_photos_album(self, client, bp_app):
        all_id = _get_all_album_id(bp_app)
        resp = client.post(
            f"/api/v1/albums/{all_id}/recompute",
            json={"k": 5},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "photos" in data
        assert "selected_paths" in data
        assert "stats" in data
        assert len(data["selected_paths"]) <= 5

    def test_recompute_with_weights(self, client, bp_app):
        all_id = _get_all_album_id(bp_app)
        resp = client.post(
            f"/api/v1/albums/{all_id}/recompute",
            json={
                "k": 3,
                "blur_weight": 2.0,
                "exposure_weight": 1.5,
                "face_weight": 0.5,
                "composition_weight": 1.0,
            },
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert len(resp.get_json()["selected_paths"]) <= 3

    def test_recompute_delta_mode(self, client, bp_app):
        all_id = _get_all_album_id(bp_app)
        resp = client.post(
            f"/api/v1/albums/{all_id}/recompute",
            json={"k": 5, "delta": True},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "selected_paths" in data
        assert "scores" in data
        assert "stats" in data
        # Delta mode should NOT include "photos" key
        assert "photos" not in data

    def test_recompute_413_when_full_payload_too_large(self, client, bp_app, monkeypatch):
        """Album recompute cap fires BEFORE recompute()
        runs. Inject 5001 fake album rows and assert recompute() is
        never called — previously the cap was post-recompute, so
        oversized albums still paid the full CPU/RAM cost just to
        be 413'd."""

        all_id = _get_all_album_id(bp_app)

        fake_rows = [
            {
                "id": i,
                "filepath": f"/tmp/fake_{i}.jpg",
                "aggregate_score": 0.5,
                "blur_raw": 100.0,
                "deleted_at": None,
                "override": None,
            }
            for i in range(5001)
        ]

        # api_album_recompute moved from bp_albums to bp_recompute in the
        # v0.1 split. Patch the names where they actually resolve.
        from bpp.web import bp_recompute

        monkeypatch.setattr(bp_recompute, "get_album_photos", lambda *a, **kw: list(fake_rows))

        def _must_not_run(_opts):
            pytest.fail("recompute() was called even though album exceeds the cap")

        monkeypatch.setattr(bp_recompute, "recompute", _must_not_run)

        resp = client.post(
            f"/api/v1/albums/{all_id}/recompute",
            json={"k": 5},  # Non-delta
            content_type="application/json",
        )
        assert resp.status_code == 413, (
            f"Expected 413 for >5000-photo non-delta payload, got {resp.status_code}"
        )
        data = resp.get_json()
        assert data.get("code") == "delta_required"
        assert "delta" in data["error"].lower()
        assert data["context"]["photo_count"] == 5001

    def test_recompute_manual_album_with_photos(self, client, bp_app):
        album_id = _create_manual_album(client, "RecompTest")
        photo_ids = _get_photo_ids(bp_app)
        _add_photos_to_album(bp_app, album_id, photo_ids[:5])
        resp = client.post(
            f"/api/v1/albums/{album_id}/recompute",
            json={"k": 3},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["selected_paths"]) <= 3

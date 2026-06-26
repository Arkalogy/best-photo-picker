"""Comprehensive Flask blueprint route tests for maximum coverage."""

from __future__ import annotations

import json
import os

import numpy as np
import pytest

from bpp.web.app import create_app


def _make_analysis(n: int = 10) -> list[dict]:
    """Create synthetic analysis data for testing."""
    items = []
    for i in range(n):
        items.append(
            {
                "filepath": f"/tmp/test_photos/img_{i:03d}.jpg",
                "date": f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}T12:00:00",
                "date_day": f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}",
                "date_month": f"2024-{(i % 12) + 1:02d}",
                "file_size": 1024 * (i + 1),
                "file_mtime": 1700000000.0 + i,
                "blur_raw": 100.0 + i * 50,
                "blur_score": i / max(n - 1, 1),
                "exposure_score": 0.5 + (i % 3) * 0.15,
                "face_score": 0.3 + (i % 4) * 0.1,
                "face_count": i % 3,
                "largest_face_ratio": 0.05,
                "face_center_dist": 0.3,
                "composition_score": 0.4 + (i % 5) * 0.1,
                "aggregate_score": 0.3 + i * 0.05,
            }
        )
    return items


@pytest.fixture
def bp_app(tmp_path):
    """Create a Flask app for blueprint testing with analysis data loaded."""
    workdir = str(tmp_path / "workdir")
    os.makedirs(workdir)
    analysis = _make_analysis(10)
    with open(os.path.join(workdir, "analysis.json"), "w") as f:
        json.dump(analysis, f)
    app = create_app(workdir=workdir)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(bp_app):
    return bp_app.test_client()


def _get_ctx(bp_app):
    """Get the WebAppState from the app."""
    return bp_app.extensions["bpp"]


def _get_all_album_id(bp_app):
    """Get the 'All Photos' album ID."""
    ctx = _get_ctx(bp_app)
    with bp_app.app_context():
        conn = ctx.get_conn()
        row = conn.execute("SELECT id FROM albums WHERE album_type='all' LIMIT 1").fetchone()
        return row[0] if row else None


def _get_photo_ids(bp_app):
    """Get all photo IDs from the DB."""
    ctx = _get_ctx(bp_app)
    with bp_app.app_context():
        conn = ctx.get_conn()
        rows = conn.execute("SELECT id FROM photos ORDER BY id").fetchall()
        return [r[0] for r in rows]


def _get_filepaths(bp_app):
    """Get all filepaths from the DB."""
    ctx = _get_ctx(bp_app)
    with bp_app.app_context():
        conn = ctx.get_conn()
        rows = conn.execute("SELECT filepath FROM photos ORDER BY id").fetchall()
        return [r[0] for r in rows]


def _create_manual_album(client, name="Test Album"):
    """Helper to create a manual album and return its ID."""
    resp = client.post("/api/v1/albums", json={"name": name}, content_type="application/json")
    return resp.get_json()["id"]


def _add_photos_to_album(bp_app, album_id, photo_ids):
    """Directly add photos to an album via DB."""
    ctx = _get_ctx(bp_app)
    with bp_app.app_context():
        conn = ctx.get_conn()
        conn.executemany(
            "INSERT OR IGNORE INTO album_photos (album_id, photo_id) VALUES (?, ?)",
            [(album_id, pid) for pid in photo_ids],
        )
        conn.commit()


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
        """R4-M6 + R6-M1: album recompute cap fires BEFORE recompute()
        runs. Inject 5001 fake album rows and assert recompute() is
        never called — previously the cap was post-recompute, so
        oversized albums still paid the full CPU/RAM cost just to
        be 413'd."""
        import pytest

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
        assert data.get("delta_required") is True
        assert "delta" in data["error"].lower()
        assert data.get("photo_count") == 5001

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


class TestFacesMerge:
    """Tests for POST /api/faces/merge."""

    def test_merge_missing_params(self, client):
        resp = client.post(
            "/api/v1/faces/merge",
            json={},
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert "primary_cluster_id" in resp.get_json()["error"].lower()

    def test_merge_missing_merge_ids(self, client):
        resp = client.post(
            "/api/v1/faces/merge",
            json={"primary_cluster_id": 0},
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_merge_invalid_cluster_ids(self, client):
        resp = client.post(
            "/api/v1/faces/merge",
            json={"primary_cluster_id": -1, "merge_cluster_ids": [1]},
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert "non-negative" in resp.get_json()["error"].lower()

    def test_merge_valid(self, client, bp_app):
        """Insert face embeddings and test a valid merge."""
        ctx = _get_ctx(bp_app)
        with bp_app.app_context():
            conn = ctx.get_conn()
            photo_ids = [r[0] for r in conn.execute("SELECT id FROM photos LIMIT 4").fetchall()]
            emb = np.random.randn(128).astype(np.float32).tobytes()
            for i, pid in enumerate(photo_ids):
                cluster = 0 if i < 2 else 1
                conn.execute(
                    "INSERT INTO face_embeddings"
                    " (photo_id, face_index, embedding, cluster_id,"
                    " bbox_x, bbox_y, bbox_w, bbox_h)"
                    " VALUES (?, 0, ?, ?, 10, 10, 50, 50)",
                    (pid, emb, cluster),
                )
            conn.commit()

        resp = client.post(
            "/api/v1/faces/merge",
            json={"primary_cluster_id": 0, "merge_cluster_ids": [1]},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "merged"
        assert "albums" in data


class TestFacesSplit:
    """Tests for POST /api/faces/split — move selected faces to a new cluster."""

    def test_split_missing_params(self, client):
        resp = client.post(
            "/api/v1/faces/split",
            json={},
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_split_empty_face_ids(self, client):
        resp = client.post(
            "/api/v1/faces/split",
            json={"face_ids": []},
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_split_invalid_face_ids_type(self, client):
        resp = client.post(
            "/api/v1/faces/split",
            json={"face_ids": "bad"},
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_split_moves_faces_to_new_cluster(self, client, bp_app):
        """Selected faces move to a new cluster; others stay."""
        ctx = _get_ctx(bp_app)
        with bp_app.app_context():
            conn = ctx.get_conn()
            pid = conn.execute("SELECT id FROM photos LIMIT 1").fetchone()[0]

            # 3 faces in cluster 10
            face_ids = []
            for fi in range(80, 83):
                emb = np.random.randn(128).astype(np.float32).tobytes()
                conn.execute(
                    "INSERT INTO face_embeddings"
                    " (photo_id, face_index, embedding, cluster_id,"
                    " bbox_x, bbox_y, bbox_w, bbox_h)"
                    " VALUES (?, ?, ?, 10, 10, 10, 50, 50)",
                    (pid, fi, emb),
                )
                fid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                face_ids.append(fid)
            conn.commit()

        # Split face_ids[0] out of cluster 10
        resp = client.post(
            "/api/v1/faces/split",
            json={"face_ids": [face_ids[0]]},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "split"
        assert "new_cluster_id" in data
        new_cid = data["new_cluster_id"]
        assert new_cid != 10

        # Verify: split face is in new cluster, others still in 10
        with bp_app.app_context():
            conn = ctx.get_conn()
            row = conn.execute(
                "SELECT cluster_id FROM face_embeddings WHERE id=?",
                (face_ids[0],),
            ).fetchone()
            assert row[0] == new_cid

            for fid in face_ids[1:]:
                row = conn.execute(
                    "SELECT cluster_id FROM face_embeddings WHERE id=?",
                    (fid,),
                ).fetchone()
                assert row[0] == 10

    def test_split_multiple_faces(self, client, bp_app):
        """Splitting multiple faces puts them all in the same new cluster."""
        ctx = _get_ctx(bp_app)
        with bp_app.app_context():
            conn = ctx.get_conn()
            pid = conn.execute("SELECT id FROM photos LIMIT 1").fetchone()[0]

            face_ids = []
            for fi in range(90, 94):
                emb = np.random.randn(128).astype(np.float32).tobytes()
                conn.execute(
                    "INSERT INTO face_embeddings"
                    " (photo_id, face_index, embedding, cluster_id,"
                    " bbox_x, bbox_y, bbox_w, bbox_h)"
                    " VALUES (?, ?, ?, 20, 10, 10, 50, 50)",
                    (pid, fi, emb),
                )
                fid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                face_ids.append(fid)
            conn.commit()

        # Split first two faces out
        resp = client.post(
            "/api/v1/faces/split",
            json={"face_ids": [face_ids[0], face_ids[1]]},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        new_cid = data["new_cluster_id"]

        with bp_app.app_context():
            conn = ctx.get_conn()
            for fid in [face_ids[0], face_ids[1]]:
                row = conn.execute(
                    "SELECT cluster_id FROM face_embeddings WHERE id=?", (fid,)
                ).fetchone()
                assert row[0] == new_cid
            for fid in [face_ids[2], face_ids[3]]:
                row = conn.execute(
                    "SELECT cluster_id FROM face_embeddings WHERE id=?", (fid,)
                ).fetchone()
                assert row[0] == 20

    def test_split_records_hard_negative(self, client, bp_app):
        """Split should record a hard negative between old and new cluster."""
        ctx = _get_ctx(bp_app)
        with bp_app.app_context():
            conn = ctx.get_conn()
            pid = conn.execute("SELECT id FROM photos LIMIT 1").fetchone()[0]

            face_ids = []
            for fi in range(95, 97):
                emb = np.random.randn(128).astype(np.float32).tobytes()
                conn.execute(
                    "INSERT INTO face_embeddings"
                    " (photo_id, face_index, embedding, cluster_id,"
                    " bbox_x, bbox_y, bbox_w, bbox_h)"
                    " VALUES (?, ?, ?, 30, 10, 10, 50, 50)",
                    (pid, fi, emb),
                )
                fid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                face_ids.append(fid)
            conn.commit()

        resp = client.post(
            "/api/v1/faces/split",
            json={"face_ids": [face_ids[0]]},
            content_type="application/json",
        )
        assert resp.status_code == 200
        new_cid = resp.get_json()["new_cluster_id"]

        # Hard negative should exist between old cluster 30 and new cluster
        with bp_app.app_context():
            conn = ctx.get_conn()
            from bpp.db.face_feedback import get_hard_negatives_for_cluster

            negs = get_hard_negatives_for_cluster(conn, 30)
            assert new_cid in negs

    def test_split_returns_albums(self, client, bp_app):
        """Split response includes updated album list."""
        ctx = _get_ctx(bp_app)
        with bp_app.app_context():
            conn = ctx.get_conn()
            pid = conn.execute("SELECT id FROM photos LIMIT 1").fetchone()[0]
            emb = np.random.randn(128).astype(np.float32).tobytes()
            conn.execute(
                "INSERT INTO face_embeddings"
                " (photo_id, face_index, embedding, cluster_id,"
                " bbox_x, bbox_y, bbox_w, bbox_h)"
                " VALUES (?, 99, ?, 40, 10, 10, 50, 50)",
                (pid, emb),
            )
            conn.commit()
            fid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        resp = client.post(
            "/api/v1/faces/split",
            json={"face_ids": [fid]},
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert "albums" in resp.get_json()


class TestMergeDoesNotPropagate:
    """Merge must ONLY move the requested faces — never absorb nearby unassigned."""

    def test_merge_does_not_absorb_unassigned_faces(self, client, bp_app):
        """An unassigned face near the merged cluster must stay unassigned."""
        ctx = _get_ctx(bp_app)
        with bp_app.app_context():
            conn = ctx.get_conn()
            pid = conn.execute("SELECT id FROM photos LIMIT 1").fetchone()[0]

            # Cluster 10: one face pointing in direction [1, 0, 0, ...]
            emb_a = np.zeros(128, dtype=np.float32)
            emb_a[0] = 1.0
            conn.execute(
                "INSERT INTO face_embeddings"
                " (photo_id, face_index, embedding, cluster_id,"
                " bbox_x, bbox_y, bbox_w, bbox_h)"
                " VALUES (?, 70, ?, 10, 10, 10, 50, 50)",
                (pid, emb_a.tobytes()),
            )

            # Cluster 11: similar face (will be merged into 10)
            emb_b = np.zeros(128, dtype=np.float32)
            emb_b[0] = 0.95
            emb_b[1] = 0.05
            conn.execute(
                "INSERT INTO face_embeddings"
                " (photo_id, face_index, embedding, cluster_id,"
                " bbox_x, bbox_y, bbox_w, bbox_h)"
                " VALUES (?, 71, ?, 11, 10, 10, 50, 50)",
                (pid, emb_b.tobytes()),
            )

            # Unassigned face (-1) also close to cluster 10
            emb_c = np.zeros(128, dtype=np.float32)
            emb_c[0] = 0.9
            emb_c[2] = 0.1
            conn.execute(
                "INSERT INTO face_embeddings"
                " (photo_id, face_index, embedding, cluster_id,"
                " bbox_x, bbox_y, bbox_w, bbox_h)"
                " VALUES (?, 72, ?, -1, 10, 10, 50, 50)",
                (pid, emb_c.tobytes()),
            )
            conn.commit()

            unassigned_id = conn.execute(
                "SELECT id FROM face_embeddings WHERE face_index=72 AND photo_id=?",
                (pid,),
            ).fetchone()[0]

        # Merge cluster 11 into 10
        resp = client.post(
            "/api/v1/faces/merge",
            json={"primary_cluster_id": 10, "merge_cluster_ids": [11]},
            content_type="application/json",
        )
        assert resp.status_code == 200

        # The unassigned face must still be unassigned — NOT absorbed
        with bp_app.app_context():
            conn = ctx.get_conn()
            row = conn.execute(
                "SELECT cluster_id FROM face_embeddings WHERE id=?",
                (unassigned_id,),
            ).fetchone()
            assert row[0] == -1, (
                f"Unassigned face was absorbed into cluster {row[0]} — merge must not propagate"
            )


class TestFacesDismiss:
    """Tests for POST /api/faces/dismiss."""

    def test_dismiss_missing_params(self, client):
        resp = client.post(
            "/api/v1/faces/dismiss",
            json={},
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_dismiss_invalid_cluster_id(self, client):
        resp = client.post(
            "/api/v1/faces/dismiss",
            json={"cluster_id": -5},
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert "non-negative" in resp.get_json()["error"].lower()

    def test_dismiss_valid_single(self, client, bp_app):
        """Insert face data and dismiss a cluster."""
        ctx = _get_ctx(bp_app)
        with bp_app.app_context():
            conn = ctx.get_conn()
            pid = conn.execute("SELECT id FROM photos LIMIT 1").fetchone()[0]
            emb = np.random.randn(128).astype(np.float32).tobytes()
            conn.execute(
                "INSERT OR IGNORE INTO face_embeddings"
                " (photo_id, face_index, embedding, cluster_id,"
                " bbox_x, bbox_y, bbox_w, bbox_h)"
                " VALUES (?, 0, ?, 5, 10, 10, 50, 50)",
                (pid, emb),
            )
            conn.commit()

        resp = client.post(
            "/api/v1/faces/dismiss",
            json={"cluster_id": 5},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "dismissed"
        assert data["count"] == 1

    def test_dismiss_multiple(self, client, bp_app):
        """Dismiss multiple clusters at once."""
        ctx = _get_ctx(bp_app)
        with bp_app.app_context():
            conn = ctx.get_conn()
            photos = conn.execute("SELECT id FROM photos LIMIT 2").fetchall()
            emb = np.random.randn(128).astype(np.float32).tobytes()
            for i, row in enumerate(photos):
                conn.execute(
                    "INSERT OR IGNORE INTO face_embeddings"
                    " (photo_id, face_index, embedding, cluster_id,"
                    " bbox_x, bbox_y, bbox_w, bbox_h)"
                    " VALUES (?, 0, ?, ?, 10, 10, 50, 50)",
                    (row[0], emb, 10 + i),
                )
            conn.commit()

        resp = client.post(
            "/api/v1/faces/dismiss",
            json={"cluster_ids": [10, 11]},
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.get_json()["count"] == 2


class TestFacesRestore:
    """Tests for POST /api/faces/restore."""

    def test_restore_missing_params(self, client):
        resp = client.post(
            "/api/v1/faces/restore",
            json={},
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_restore_invalid_face_ids(self, client):
        resp = client.post(
            "/api/v1/faces/restore",
            json={"face_ids": "bad"},
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_restore_dismissed_by_face_id(self, client, bp_app):
        """Dismiss a cluster, then restore individual face by ID."""
        ctx = _get_ctx(bp_app)
        with bp_app.app_context():
            conn = ctx.get_conn()
            pid = conn.execute("SELECT id FROM photos LIMIT 1").fetchone()[0]
            emb = np.random.randn(128).astype(np.float32).tobytes()
            conn.execute(
                "INSERT OR IGNORE INTO face_embeddings"
                " (photo_id, face_index, embedding, cluster_id,"
                " bbox_x, bbox_y, bbox_w, bbox_h)"
                " VALUES (?, 0, ?, 7, 10, 10, 50, 50)",
                (pid, emb),
            )
            conn.commit()
            face_id = conn.execute("SELECT id FROM face_embeddings WHERE cluster_id=7").fetchone()[
                0
            ]

        # Dismiss cluster 7
        resp = client.post(
            "/api/v1/faces/dismiss",
            json={"cluster_id": 7},
            content_type="application/json",
        )
        assert resp.status_code == 200

        # Restore by face_id
        resp = client.post(
            "/api/v1/faces/restore",
            json={"face_ids": [face_id]},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "restored"
        assert data["count"] >= 1

    def test_restore_all(self, client, bp_app):
        """Restore all dismissed faces at once."""
        ctx = _get_ctx(bp_app)
        with bp_app.app_context():
            conn = ctx.get_conn()
            pid = conn.execute("SELECT id FROM photos LIMIT 1").fetchone()[0]
            emb = np.random.randn(128).astype(np.float32).tobytes()
            conn.execute(
                "INSERT OR IGNORE INTO face_embeddings"
                " (photo_id, face_index, embedding, cluster_id,"
                " bbox_x, bbox_y, bbox_w, bbox_h)"
                " VALUES (?, 0, ?, 8, 10, 10, 50, 50)",
                (pid, emb),
            )
            conn.commit()

        # Dismiss
        client.post(
            "/api/v1/faces/dismiss",
            json={"cluster_id": 8},
            content_type="application/json",
        )
        # Restore all
        resp = client.post(
            "/api/v1/faces/restore",
            json={"all": True},
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "restored"

    def test_restore_nothing_to_restore(self, client):
        """Restoring face_ids that aren't dismissed returns count=0."""
        resp = client.post(
            "/api/v1/faces/restore",
            json={"face_ids": [999999]},
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.get_json()["count"] == 0

    def test_restore_respects_hard_negatives(self, client, bp_app):
        """Restored face ambiguous between two hard-negative clusters gets a new cluster."""
        ctx = _get_ctx(bp_app)
        with bp_app.app_context():
            conn = ctx.get_conn()
            pid = conn.execute("SELECT id FROM photos LIMIT 1").fetchone()[0]

            # Create two clusters (10, 11) with distinct embeddings
            emb_a = np.zeros(128, dtype=np.float32)
            emb_a[0] = 1.0  # cluster 10 centroid direction
            emb_b = np.zeros(128, dtype=np.float32)
            emb_b[1] = 1.0  # cluster 11 centroid direction

            conn.execute(
                "INSERT OR IGNORE INTO face_embeddings"
                " (photo_id, face_index, embedding, cluster_id,"
                " bbox_x, bbox_y, bbox_w, bbox_h)"
                " VALUES (?, 10, ?, 10, 10, 10, 50, 50)",
                (pid, emb_a.tobytes()),
            )
            conn.execute(
                "INSERT OR IGNORE INTO face_embeddings"
                " (photo_id, face_index, embedding, cluster_id,"
                " bbox_x, bbox_y, bbox_w, bbox_h)"
                " VALUES (?, 11, ?, 11, 10, 10, 50, 50)",
                (pid, emb_b.tobytes()),
            )

            # Create a dismissed face equally close to both clusters
            emb_mid = np.zeros(128, dtype=np.float32)
            emb_mid[0] = 0.5
            emb_mid[1] = 0.5  # equidistant from a and b
            from bpp.constants import CLUSTER_DISMISSED

            conn.execute(
                "INSERT OR IGNORE INTO face_embeddings"
                " (photo_id, face_index, embedding, cluster_id,"
                " bbox_x, bbox_y, bbox_w, bbox_h)"
                " VALUES (?, 12, ?, ?, 10, 10, 50, 50)",
                (pid, emb_mid.tobytes(), CLUSTER_DISMISSED),
            )
            conn.commit()

            mid_id = conn.execute(
                "SELECT id FROM face_embeddings WHERE face_index = 12 AND photo_id = ?",
                (pid,),
            ).fetchone()[0]

            # Record hard negative between clusters 10 and 11
            from bpp.db.face_feedback import store_hard_negative

            store_hard_negative(conn, 10, 11)

        # Restore the dismissed face
        resp = client.post(
            "/api/v1/faces/restore",
            json={"face_ids": [mid_id]},
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.get_json()["count"] == 1

        # Verify it was NOT assigned to either confusable cluster
        with bp_app.app_context():
            conn = ctx.get_conn()
            row = conn.execute(
                "SELECT cluster_id FROM face_embeddings WHERE id = ?", (mid_id,)
            ).fetchone()
            assert row[0] not in (10, 11, CLUSTER_DISMISSED), f"Expected new cluster, got {row[0]}"
            assert row[0] >= 0, "Should be assigned to a real cluster"

    def test_dismissed_list_endpoint(self, client, bp_app):
        """GET /api/faces/dismissed returns dismissed face entries."""
        ctx = _get_ctx(bp_app)
        with bp_app.app_context():
            conn = ctx.get_conn()
            pid = conn.execute("SELECT id FROM photos LIMIT 1").fetchone()[0]
            emb = np.random.randn(128).astype(np.float32).tobytes()
            from bpp.constants import CLUSTER_DISMISSED

            conn.execute(
                "INSERT OR IGNORE INTO face_embeddings"
                " (photo_id, face_index, embedding, cluster_id,"
                " bbox_x, bbox_y, bbox_w, bbox_h, quality)"
                f" VALUES (?, 0, ?, {CLUSTER_DISMISSED}, 10, 10, 50, 50, 0.7)",
                (pid, emb),
            )
            conn.commit()

        resp = client.get("/api/v1/faces/dismissed")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "faces" in data
        assert "total" in data
        assert data["total"] >= 1
        face = data["faces"][0]
        assert "face_id" in face
        assert "face_index" in face
        assert "thumb_hash" in face
        # Source-photo context for the full-photo preview caption: the
        # Ignored-faces grid crops are unjudgeable without it.
        for key in ("filename", "date", "score"):
            assert key in face, f"dismissed face missing {key}: {face}"


class TestAvatarPickerQuality:
    """Avatar picker API must return faces sorted by quality."""

    def test_cluster_detail_includes_quality(self, client, bp_app):
        """GET /api/faces/cluster/<id> must return quality in face entries."""
        ctx = _get_ctx(bp_app)
        with bp_app.app_context():
            conn = ctx.get_conn()
            pid = conn.execute("SELECT id FROM photos LIMIT 1").fetchone()[0]
            emb = np.random.randn(128).astype(np.float32).tobytes()
            conn.execute(
                "INSERT OR IGNORE INTO face_embeddings"
                " (photo_id, face_index, embedding, cluster_id,"
                " bbox_x, bbox_y, bbox_w, bbox_h, quality)"
                " VALUES (?, 0, ?, 3, 10, 10, 50, 50, 0.85)",
                (pid, emb),
            )
            conn.commit()

        resp = client.get("/api/v1/faces/cluster/3")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["faces"]) >= 1
        assert "quality" in data["faces"][0], "Avatar picker API must include quality score"

    def test_cluster_detail_sorted_by_quality_desc(self, client, bp_app):
        """Faces should be returned highest-quality first."""
        ctx = _get_ctx(bp_app)
        with bp_app.app_context():
            conn = ctx.get_conn()
            photos = conn.execute("SELECT id FROM photos LIMIT 3").fetchall()
            emb = np.random.randn(128).astype(np.float32).tobytes()
            for i, row in enumerate(photos):
                conn.execute(
                    "INSERT OR IGNORE INTO face_embeddings"
                    " (photo_id, face_index, embedding, cluster_id,"
                    " bbox_x, bbox_y, bbox_w, bbox_h, quality)"
                    " VALUES (?, 0, ?, 4, 10, 10, 50, 50, ?)",
                    (row[0], emb, 0.1 + i * 0.3),  # 0.1, 0.4, 0.7
                )
            conn.commit()

        resp = client.get("/api/v1/faces/cluster/4")
        assert resp.status_code == 200
        faces = resp.get_json()["faces"]
        qualities = [f["quality"] for f in faces]
        assert qualities == sorted(qualities, reverse=True), (
            "Avatar picker must return faces sorted by quality DESC"
        )


class TestFacesRecluster:
    """Tests for POST /api/faces/recluster."""

    def test_recluster_missing_threshold(self, client):
        resp = client.post(
            "/api/v1/faces/recluster",
            json={},
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert "threshold required" in resp.get_json()["error"]

    def test_recluster_threshold_too_low(self, client):
        resp = client.post(
            "/api/v1/faces/recluster",
            json={"threshold": 0.1},
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert "between" in resp.get_json()["error"]

    def test_recluster_threshold_too_high(self, client):
        resp = client.post(
            "/api/v1/faces/recluster",
            json={"threshold": 2.0},
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_recluster_threshold_not_number(self, client):
        resp = client.post(
            "/api/v1/faces/recluster",
            json={"threshold": "abc"},
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert "number" in resp.get_json()["error"]

    def test_recluster_no_embeddings(self, client):
        """When no face embeddings exist, recluster returns clusters=0."""
        resp = client.post(
            "/api/v1/faces/recluster",
            json={"threshold": 0.72},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "reclustered"
        assert data["clusters"] == 0

    def test_recluster_with_embeddings(self, client, bp_app):
        """Insert face embeddings and test reclustering."""
        ctx = _get_ctx(bp_app)
        with bp_app.app_context():
            conn = ctx.get_conn()
            # Clear any existing face embeddings
            conn.execute("DELETE FROM face_embeddings")
            conn.commit()

            photos = conn.execute("SELECT id FROM photos LIMIT 4").fetchall()
            for i, row in enumerate(photos):
                # Use distinct embeddings so clustering can work
                emb = np.zeros(128, dtype=np.float32)
                emb[i] = 1.0
                conn.execute(
                    "INSERT INTO face_embeddings"
                    " (photo_id, face_index, embedding, cluster_id,"
                    " bbox_x, bbox_y, bbox_w, bbox_h)"
                    " VALUES (?, 0, ?, ?, 10, 10, 50, 50)",
                    (row[0], emb.tobytes(), i),
                )
            conn.commit()

        resp = client.post(
            "/api/v1/faces/recluster",
            json={"threshold": 0.72},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "reclustered"
        assert data["clusters"] >= 1


class TestFacesClusters:
    """Tests for GET /api/faces/clusters."""

    def test_face_clusters_empty(self, client, bp_app):
        """When no face embeddings, returns empty clusters."""
        ctx = _get_ctx(bp_app)
        with bp_app.app_context():
            conn = ctx.get_conn()
            conn.execute("DELETE FROM face_embeddings")
            conn.commit()

        resp = client.get("/api/v1/faces/clusters")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "clusters" in data


# ===========================================================================
# bp_media.py
# ===========================================================================


class TestMediaThumbnail:
    """Tests for GET /thumb/<hash> and POST /api/thumbnails/clear."""

    def test_thumb_nonexistent(self, client):
        resp = client.get("/thumb/nonexistent_hash")
        assert resp.status_code == 404

    def test_thumb_no_thumbs_context(self, client, bp_app):
        """When thumbs is None, returns 404."""
        ctx = _get_ctx(bp_app)
        orig_thumbs = ctx.thumbs
        ctx.thumbs = None
        try:
            resp = client.get("/thumb/anyhash")
            assert resp.status_code == 404
            assert "no thumbnails" in resp.get_json()["error"].lower()
        finally:
            ctx.thumbs = orig_thumbs

    def test_thumbnails_clear(self, client):
        resp = client.post("/api/v1/thumbnails/clear")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "cleared"
        assert "count" in data

    def test_thumbnails_clear_no_thumbs(self, client, bp_app):
        ctx = _get_ctx(bp_app)
        orig_thumbs = ctx.thumbs
        ctx.thumbs = None
        try:
            resp = client.post("/api/v1/thumbnails/clear")
            assert resp.status_code == 404
        finally:
            ctx.thumbs = orig_thumbs


class TestMediaFullPhoto:
    """Tests for GET /photo/<hash>."""

    def test_photo_nonexistent(self, client):
        resp = client.get("/photo/nonexistent_hash")
        assert resp.status_code == 404

    def test_photo_no_thumbs_context(self, client, bp_app):
        ctx = _get_ctx(bp_app)
        orig_thumbs = ctx.thumbs
        ctx.thumbs = None
        try:
            resp = client.get("/photo/somehash")
            assert resp.status_code == 404
            assert "no photos" in resp.get_json()["error"].lower()
        finally:
            ctx.thumbs = orig_thumbs


# ===========================================================================
# bp_core.py — Status
# ===========================================================================


class TestCoreStatus:
    """Tests for GET /api/status and GET /."""

    def test_index_page(self, client):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_status(self, client):
        resp = client.get("/api/v1/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["has_analysis"] is True
        assert data["image_count"] == 10
        assert "defaults" in data
        assert "serve_mode" in data
        assert "analyzing" in data
        assert "face_recognition_available" in data
        assert "clip_available" in data
        assert "heic_available" in data
        assert "pets_available" in data
        assert "pet_detection_done" in data

    def test_status_workdir_set(self, client):
        resp = client.get("/api/v1/status")
        data = resp.get_json()
        assert data["workdir"] is not None


# ===========================================================================
# Integration: album workflow end-to-end
# ===========================================================================


class TestAlbumWorkflow:
    """End-to-end album workflow: create, add photos, recompute, override, delete."""

    def test_full_album_lifecycle(self, client, bp_app):
        # 1. Create album
        create_resp = client.post(
            "/api/v1/albums",
            json={"name": "Workflow Test"},
            content_type="application/json",
        )
        assert create_resp.status_code == 201
        album_id = create_resp.get_json()["id"]

        # 2. Add photos
        filepaths = _get_filepaths(bp_app)
        add_resp = client.post(
            f"/api/v1/albums/{album_id}/add-photos",
            json={"filepaths": filepaths[:5]},
            content_type="application/json",
        )
        assert add_resp.status_code == 200

        # 3. Verify photos are in album
        photos_resp = client.get(f"/api/v1/albums/{album_id}/photos")
        assert photos_resp.get_json()["count"] == 5

        # 4. Recompute selection
        recomp_resp = client.post(
            f"/api/v1/albums/{album_id}/recompute",
            json={"k": 3},
            content_type="application/json",
        )
        assert recomp_resp.status_code == 200
        assert len(recomp_resp.get_json()["selected_paths"]) <= 3

        # 5. Set override
        override_resp = client.post(
            f"/api/v1/albums/{album_id}/override",
            json={"filepath": filepaths[0], "mode": "include"},
            content_type="application/json",
        )
        assert override_resp.status_code == 200

        # 6. Toggle favorite
        fav_resp = client.post(
            f"/api/v1/albums/{album_id}/favorite",
            json={"filepath": filepaths[1]},
            content_type="application/json",
        )
        assert fav_resp.status_code == 200

        # 7. Remove some photos
        remove_resp = client.post(
            f"/api/v1/albums/{album_id}/remove-photos",
            json={"filepaths": filepaths[:2]},
            content_type="application/json",
        )
        assert remove_resp.status_code == 200

        # 8. Delete album
        del_resp = client.delete(f"/api/v1/albums/{album_id}")
        assert del_resp.status_code == 200

    def test_album_list_includes_created(self, client):
        client.post(
            "/api/v1/albums",
            json={"name": "Listed Album"},
            content_type="application/json",
        )
        resp = client.get("/api/v1/albums")
        assert resp.status_code == 200
        albums = resp.get_json()["albums"]
        names = [a["name"] for a in albums]
        assert "Listed Album" in names


# ===========================================================================
# bp_faces.py — Dedup feedback stats
# ===========================================================================


class TestDedupFeedbackStats:
    """Tests for GET /api/dedup/feedback/stats."""

    def test_dedup_feedback_stats(self, client):
        resp = client.get("/api/v1/dedup/feedback/stats")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "threshold" in data
        assert "default_threshold" in data
        assert "feedback_count" in data
        assert data["feedback_count"] == 0

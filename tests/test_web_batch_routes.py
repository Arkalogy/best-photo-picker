"""Tests for the web UI: recompute logic + route smoke tests — part 2 of 3: TestBatchRoutes,
TestAlbumValidation, TestFaceRoutes."""

# Shared fixtures are imported by name and re-bound as test parameters.
# ruff: noqa: F811

from __future__ import annotations

import json
import os

import pytest

from tests._web_shared import (  # noqa: F401
    _default_config,
    _make_analysis,
    _setup_face_db,
    face_web_client,
    web_client,
)

# --- Batch operation route tests ---


class TestBatchRoutes:
    def test_batch_override_include(self, web_client):
        filepaths = [f"/tmp/test_photos/img_{i:03d}.jpg" for i in range(3)]
        resp = web_client.post(
            "/api/v1/batch/override",
            json={"filepaths": filepaths, "mode": "include"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["count"] == 3

    def test_batch_override_exclude(self, web_client):
        filepaths = [f"/tmp/test_photos/img_{i:03d}.jpg" for i in range(2)]
        resp = web_client.post(
            "/api/v1/batch/override",
            json={"filepaths": filepaths, "mode": "exclude"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 2

    def test_batch_override_clear(self, web_client):
        filepaths = ["/tmp/test_photos/img_000.jpg"]
        # First set an override
        web_client.post(
            "/api/v1/batch/override",
            json={"filepaths": filepaths, "mode": "include"},
        )
        # Then clear it
        resp = web_client.post(
            "/api/v1/batch/override",
            json={"filepaths": filepaths, "mode": None},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 1

    def test_batch_override_empty(self, web_client):
        resp = web_client.post(
            "/api/v1/batch/override",
            json={"filepaths": [], "mode": "include"},
        )
        assert resp.status_code == 400

    def test_batch_favorite(self, web_client):
        filepaths = [f"/tmp/test_photos/img_{i:03d}.jpg" for i in range(4)]
        resp = web_client.post(
            "/api/v1/batch/favorite",
            json={"filepaths": filepaths, "favorite": True},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["count"] == 4

    def test_batch_unfavorite(self, web_client):
        filepaths = [f"/tmp/test_photos/img_{i:03d}.jpg" for i in range(2)]
        # First favorite them
        web_client.post(
            "/api/v1/batch/favorite",
            json={"filepaths": filepaths, "favorite": True},
        )
        # Then unfavorite
        resp = web_client.post(
            "/api/v1/batch/favorite",
            json={"filepaths": filepaths, "favorite": False},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 2

    def test_batch_favorite_empty(self, web_client):
        resp = web_client.post(
            "/api/v1/batch/favorite",
            json={"filepaths": [], "favorite": True},
        )
        assert resp.status_code == 400

    def test_batch_override_unknown_photo(self, web_client):
        resp = web_client.post(
            "/api/v1/batch/override",
            json={"filepaths": ["/nonexistent/photo.jpg"], "mode": "include"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 0

    def test_add_photos_to_album(self, web_client):
        resp = web_client.post("/api/v1/albums", json={"name": "My Album"})
        assert resp.status_code == 201
        album_id = resp.get_json()["id"]
        filepaths = [f"/tmp/test_photos/img_{i:03d}.jpg" for i in range(3)]
        resp = web_client.post(
            f"/api/v1/albums/{album_id}/add-photos",
            json={"filepaths": filepaths},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["count"] == 3

    def test_add_photos_to_album_empty(self, web_client):
        resp = web_client.post("/api/v1/albums", json={"name": "Empty"})
        album_id = resp.get_json()["id"]
        resp = web_client.post(
            f"/api/v1/albums/{album_id}/add-photos",
            json={"filepaths": []},
        )
        assert resp.status_code == 400

    def test_add_photos_to_nonexistent_album(self, web_client):
        resp = web_client.post(
            "/api/v1/albums/9999/add-photos",
            json={"filepaths": ["/tmp/test_photos/img_000.jpg"]},
        )
        assert resp.status_code == 404


class TestAlbumValidation:
    """Tests for album name length and parameter validation."""

    @pytest.fixture
    def web_client(self, tmp_path):
        from bpp.web.app import create_app

        workdir = str(tmp_path / "workdir")
        os.makedirs(workdir)
        analysis = _make_analysis(5)
        with open(os.path.join(workdir, "analysis.json"), "w") as f:
            json.dump(analysis, f)
        app = create_app(workdir=workdir)
        app.config["TESTING"] = True
        return app.test_client()

    def test_create_album_name_too_long(self, web_client):
        resp = web_client.post("/api/v1/albums", json={"name": "x" * 256})
        assert resp.status_code == 400
        assert "too long" in resp.get_json()["error"].lower()

    def test_create_album_name_max_length(self, web_client):
        resp = web_client.post("/api/v1/albums", json={"name": "x" * 255})
        assert resp.status_code == 201

    def test_update_album_name_too_long(self, web_client):
        resp = web_client.post("/api/v1/albums", json={"name": "Original"})
        album_id = resp.get_json()["id"]
        resp = web_client.put(
            f"/api/v1/albums/{album_id}",
            json={"name": "y" * 256},
        )
        assert resp.status_code == 400
        assert "too long" in resp.get_json()["error"].lower()

    def test_update_album_config_merges_not_replaces(self, web_client):
        resp = web_client.post("/api/v1/albums", json={"name": "MergeTest"})
        album_id = resp.get_json()["id"]
        # Write initial config with two keys
        web_client.put(
            f"/api/v1/albums/{album_id}",
            json={"config": {"face_weight": 0.5, "blur_weight": 0.3}},
        )
        # Write a partial update — should merge, not replace
        web_client.put(f"/api/v1/albums/{album_id}", json={"config": {"k_user_set": True}})
        resp = web_client.get(f"/api/v1/albums/{album_id}")
        cfg = resp.get_json()["album"]["config"]
        assert cfg["k_user_set"] is True
        assert cfg["face_weight"] == 0.5, "existing keys must survive a partial config update"
        assert cfg["blur_weight"] == 0.3

    def test_update_album_config_null_does_not_crash(self, web_client):
        resp = web_client.post("/api/v1/albums", json={"name": "NullConfigTest"})
        album_id = resp.get_json()["id"]
        resp = web_client.put(f"/api/v1/albums/{album_id}", json={"config": None})
        assert resp.status_code == 200

    def test_update_album_config_filters_unknown_keys(self, web_client):
        resp = web_client.post("/api/v1/albums", json={"name": "FilterTest"})
        album_id = resp.get_json()["id"]
        web_client.put(
            f"/api/v1/albums/{album_id}",
            json={
                "config": {"k_user_set": True, "face_weight": 0.4, "injected_key": "evil"},
            },
        )
        resp = web_client.get(f"/api/v1/albums/{album_id}")
        cfg = resp.get_json()["album"]["config"]
        assert cfg.get("k_user_set") is True
        assert cfg.get("face_weight") == 0.4
        assert "injected_key" not in cfg, "unknown keys must be stripped"

    def test_create_album_empty_name(self, web_client):
        resp = web_client.post("/api/v1/albums", json={"name": "  "})
        assert resp.status_code == 400


class TestFaceRoutes:
    def test_merge_missing_params(self, face_web_client):
        resp = face_web_client.post("/api/v1/faces/merge", json={})
        assert resp.status_code == 400

    def test_merge_clusters(self, face_web_client):
        resp = face_web_client.post(
            "/api/v1/faces/merge",
            json={"primary_cluster_id": 0, "merge_cluster_ids": [1]},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "merged"
        assert "albums" in data
        # After merging cluster 1 into 0, there should be only one person album
        person_albums = [a for a in data["albums"] if a["album_type"] == "smart_person"]
        assert len(person_albums) == 1
        assert person_albums[0]["name"] == "Person 1"

    def test_dismiss_missing_params(self, face_web_client):
        resp = face_web_client.post("/api/v1/faces/dismiss", json={})
        assert resp.status_code == 400

    def test_dismiss_cluster(self, face_web_client):
        resp = face_web_client.post(
            "/api/v1/faces/dismiss",
            json={"cluster_id": 1},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "dismissed"
        assert "albums" in data
        # After dismissing cluster 1, only cluster 0 person album should remain
        person_albums = [a for a in data["albums"] if a["album_type"] == "smart_person"]
        assert len(person_albums) == 1
        assert person_albums[0]["name"] == "Person 1"

    def test_merge_rejects_reserved_cluster_ids(self, face_web_client):
        """Merge must reject reserved negative cluster IDs."""
        resp = face_web_client.post(
            "/api/v1/faces/merge",
            json={"primary_cluster_id": 0, "merge_cluster_ids": [-1]},
        )
        assert resp.status_code == 400

    def test_merge_rejects_negative_primary(self, face_web_client):
        resp = face_web_client.post(
            "/api/v1/faces/merge",
            json={"primary_cluster_id": -2, "merge_cluster_ids": [1]},
        )
        assert resp.status_code == 400

    def test_dismiss_rejects_reserved_cluster_ids(self, face_web_client):
        """Dismiss must reject reserved negative cluster IDs."""
        resp = face_web_client.post(
            "/api/v1/faces/dismiss",
            json={"cluster_ids": [-1]},
        )
        assert resp.status_code == 400

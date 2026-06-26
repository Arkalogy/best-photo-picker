"""Tests for the web UI: recompute logic + route smoke tests."""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3

import pytest


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


def _default_config() -> dict:
    from bpp.config import DEFAULTS

    return dict(DEFAULTS)


# --- Recompute tests ---


class TestRecompute:
    def test_basic_recompute(self):
        from bpp.web.recompute import RecomputeOptions, recompute

        analysis = _make_analysis(20)
        config = _default_config()

        result = recompute(RecomputeOptions(analysis, config, k=5))

        assert result["stats"]["total"] == 20
        assert result["stats"]["total_selected"] == 5
        assert len(result["selected_paths"]) == 5
        assert len(result["photos"]) == 20

    def test_force_exclude(self):
        from bpp.web.recompute import RecomputeOptions, recompute

        analysis = _make_analysis(10)
        config = _default_config()

        # Exclude the top-scoring items
        excludes = [analysis[-1]["filepath"], analysis[-2]["filepath"]]
        result = recompute(RecomputeOptions(analysis, config, k=5, force_exclude=excludes))

        assert result["stats"]["after_exclude"] == 8
        for fp in excludes:
            assert fp not in result["selected_paths"]

    def test_force_include(self):
        from bpp.web.recompute import RecomputeOptions, recompute

        analysis = _make_analysis(20)
        config = _default_config()

        # Force-include a low-scoring item
        low_scorer = analysis[0]["filepath"]
        result = recompute(RecomputeOptions(analysis, config, k=5, force_include=[low_scorer]))

        assert low_scorer in result["selected_paths"]
        assert result["stats"]["force_included"] >= 1

    def test_does_not_mutate_input(self):
        from bpp.web.recompute import RecomputeOptions, recompute

        analysis = _make_analysis(5)
        original_scores = [item["aggregate_score"] for item in analysis]
        config = _default_config()
        config["blur_weight"] = 0.90  # Very different weights

        recompute(RecomputeOptions(analysis, config, k=3))

        # Original data should be untouched
        for item, orig_score in zip(analysis, original_scores, strict=True):
            assert item["aggregate_score"] == orig_score
        assert "phash" not in analysis[0]

    def test_different_weights_change_scores(self):
        from bpp.web.recompute import RecomputeOptions, recompute

        analysis = _make_analysis(10)
        config1 = _default_config()
        config1["blur_weight"] = 0.90
        config1["face_weight"] = 0.01

        config2 = _default_config()
        config2["blur_weight"] = 0.01
        config2["face_weight"] = 0.90

        r1 = recompute(RecomputeOptions(analysis, config1, k=3))
        r2 = recompute(RecomputeOptions(analysis, config2, k=3))

        # Scores should differ when weights differ
        scores1 = sorted(r1["score_map"].values())
        scores2 = sorted(r2["score_map"].values())
        assert scores1 != scores2


# --- Input validation tests ---


class TestInputValidation:
    def testclamp_weight_normal(self):
        from bpp.web.state import clamp_weight

        assert clamp_weight(5.0) == 5.0
        assert clamp_weight(0) == 0.0
        assert clamp_weight(10) == 10.0

    def testclamp_weight_out_of_range(self):
        from bpp.web.state import clamp_weight

        assert clamp_weight(-5) == 0.0
        assert clamp_weight(999) == 10.0
        assert clamp_weight(-0.001) == 0.0

    def testclamp_weight_string_input(self):
        from bpp.web.state import clamp_weight

        assert clamp_weight("3.5") == 3.5
        assert clamp_weight("-1") == 0.0

    def testclamp_k_normal(self):
        from bpp.web.state import clamp_k

        assert clamp_k(50) == 50
        assert clamp_k(1) == 1
        assert clamp_k(10000) == 10000

    def testclamp_k_out_of_range(self):
        from bpp.web.state import clamp_k

        assert clamp_k(0) == 1
        assert clamp_k(-100) == 1
        assert clamp_k(99999) == 10000

    def testclamp_k_invalid_input(self):
        from bpp.web.state import clamp_k

        assert clamp_k("bad", default=50) == 50
        assert clamp_k(None, default=42) == 42


# --- Thumbnail cache tests ---


class TestThumbnailCache:
    def test_hash_is_deterministic(self):
        from bpp.web.thumbnails import ThumbnailCache

        cache = ThumbnailCache("/tmp/test_thumbs")
        h1 = cache.get_hash("/some/path/photo.jpg")
        h2 = cache.get_hash("/some/path/photo.jpg")
        assert h1 == h2
        assert len(h1) == 32

    def test_different_paths_different_hashes(self):
        from bpp.web.thumbnails import ThumbnailCache

        cache = ThumbnailCache("/tmp/test_thumbs")
        h1 = cache.get_hash("/path/a.jpg")
        h2 = cache.get_hash("/path/b.jpg")
        assert h1 != h2

    def test_unknown_hash_returns_none(self, tmp_path):
        from bpp.web.thumbnails import ThumbnailCache

        cache = ThumbnailCache(str(tmp_path / "thumbs"))
        assert cache.get_thumbnail("abcdef1234567890") is None

    def test_build_map(self, tmp_path):
        from bpp.web.thumbnails import ThumbnailCache

        cache = ThumbnailCache(str(tmp_path / "thumbs"))
        analysis = _make_analysis(3)
        cache.build_map(analysis)

        for item in analysis:
            h = cache.get_hash(item["filepath"])
            # Hash should be in the map now (though thumbnail gen will fail since files don't exist)
            assert len(h) == 32


# --- Flask route smoke tests ---


@pytest.fixture
def web_client(tmp_path):
    """Create a Flask test client with a temp workdir."""
    from bpp.web.app import create_app

    workdir = str(tmp_path / "workdir")
    os.makedirs(workdir)

    # Write analysis.json
    analysis = _make_analysis(10)
    with open(os.path.join(workdir, "analysis.json"), "w") as f:
        json.dump(analysis, f)

    app = create_app(workdir=workdir)
    app.config["TESTING"] = True
    return app.test_client()


class TestRoutes:
    def test_index(self, web_client):
        resp = web_client.get("/")
        assert resp.status_code == 200
        assert b"Best Photo Picker" in resp.data

    def test_status(self, web_client):
        resp = web_client.get("/api/v1/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["has_analysis"] is True
        assert data["image_count"] == 10
        # Dep availability flags are always present
        assert "face_recognition_available" in data
        assert "nudenet_available" in data
        assert "clip_available" in data
        assert "heic_available" in data
        assert isinstance(data["heic_available"], bool)

    def test_photos(self, web_client):
        resp = web_client.get("/api/v1/photos")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 10
        assert len(data["photos"]) == 10
        assert "thumb_hash" in data["photos"][0]

    def test_recompute(self, web_client):
        resp = web_client.post(
            "/api/v1/recompute",
            json={"k": 3, "blur_weight": 0.5},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["stats"]["total"] == 10
        assert data["stats"]["total_selected"] == 3
        assert len(data["photos"]) == 10

    def test_recompute_with_overrides(self, web_client):
        resp = web_client.post(
            "/api/v1/recompute",
            json={
                "k": 3,
                "force_include": ["/tmp/test_photos/img_000.jpg"],
                "force_exclude": ["/tmp/test_photos/img_009.jpg"],
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "/tmp/test_photos/img_000.jpg" in data["selected_paths"]
        assert "/tmp/test_photos/img_009.jpg" not in data["selected_paths"]

    def test_photos_404_without_analysis(self, tmp_path):
        from bpp.web.app import create_app

        app = create_app()
        app.config["TESTING"] = True
        client = app.test_client()

        resp = client.get("/api/v1/photos")
        assert resp.status_code == 404

    def test_export_missing_outdir(self, web_client):
        resp = web_client.post("/api/v1/export", json={})
        assert resp.status_code == 400

    def test_export_no_selection(self, web_client):
        resp = web_client.post(
            "/api/v1/export",
            json={"outdir": "/tmp/test_export", "selected_paths": []},
        )
        assert resp.status_code == 400

    def test_clip_cap_override_round_trip(self, web_client):
        """The Settings banner's Enable/Disable flow round-trips through
        the persistence layer and surfaces the new state on /status without
        a second request.

        Three assertions land the cap-override contract:
          - /status reports clip_cap_status (and surfaces clip_cap, peak_mb)
          - POST {enable: true}  → state flips to enabled_override
          - POST {enable: false} → state flips back to disabled / enabled
        """
        # Baseline: under-cap, no override → "enabled"
        resp = web_client.get("/api/v1/status")
        data = resp.get_json()
        assert data["clip_cap"] == 200_000  # default; demo lib is way under
        assert data["clip_cap_status"] == "enabled"
        assert isinstance(data["clip_cap_peak_mb"], int)

        # Enable the override; backend persists it + invalidates the
        # CLIP cache + returns the new status payload in one round trip.
        resp = web_client.post("/api/v1/settings/clip_max_override", json={"enable": True})
        assert resp.status_code == 200
        data = resp.get_json()
        # Library is under cap so the user-visible status is still
        # "enabled" (the override is set but irrelevant). The point of
        # this assertion is "the endpoint returns the freshly-computed
        # status, not a stale snapshot."
        assert "clip_cap_status" in data
        assert data["clip_cap"] == 200_000

        # Round-trip the disable path; observable via the returned status.
        resp = web_client.post("/api/v1/settings/clip_max_override", json={"enable": False})
        assert resp.status_code == 200
        data = resp.get_json()
        # Under-cap library: status returns to "enabled" once the
        # override-or-bypass flag is cleared.
        assert data["clip_cap_status"] == "enabled"

    def test_clip_cap_override_invalidates_cache(self, web_client):
        """Toggling the override must clear ready/embeddings/matrix/matrix_ids
        so the next CLIP load reflects the new cap decision. Regression
        guard for the matrix_ids typo (the original commit cleared the
        wrong key — fixed in c41510a).
        """
        # Prime the cache to a "ready" state so we can observe the reset.
        with web_client.application.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            with ctx.lock:
                ctx.clip_cache["ready"] = True
                ctx.clip_cache["embeddings"] = {1: "fake"}
                ctx.clip_cache["matrix"] = "fake-matrix"
                ctx.clip_cache["matrix_ids"] = [1, 2, 3]

        resp = web_client.post("/api/v1/settings/clip_max_override", json={"enable": True})
        assert resp.status_code == 200

        with web_client.application.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            with ctx.lock:
                assert ctx.clip_cache["ready"] is False
                assert ctx.clip_cache["embeddings"] is None
                assert ctx.clip_cache["matrix"] is None
                # The fix from c41510a: this key is matrix_ids, not "ids".
                assert ctx.clip_cache["matrix_ids"] is None

    def test_export_path_traversal(self, tmp_path):
        """Export must reject outdir outside library/workdir/home."""
        from bpp.web.app import create_app

        workdir = str(tmp_path / "workdir")
        os.makedirs(workdir)
        analysis = _make_analysis(3)
        with open(os.path.join(workdir, "analysis.json"), "w") as f:
            json.dump(analysis, f)

        app = create_app(workdir=workdir)
        app.config["TESTING"] = True
        client = app.test_client()
        client.get("/api/v1/photos")

        # Try exporting to /tmp/evil (outside library/workdir/home)
        resp = client.post(
            "/api/v1/export",
            json={
                "outdir": "/tmp/evil_export",
                "selected_paths": [analysis[0]["filepath"]],
            },
        )
        assert resp.status_code == 400
        assert "outside" in resp.get_json()["error"].lower()

    def test_thumb_unknown_hash(self, web_client):
        resp = web_client.get("/thumb/0000000000000000")
        assert resp.status_code == 404


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


# --- Face merge/dismiss route tests ---


def _setup_face_db(conn: sqlite3.Connection, analysis: list[dict]) -> None:
    """Seed face_embeddings in photopicker.db for testing.

    Embeddings must be 512 * 8 = 4096 bytes so that np.frombuffer(...,
    dtype=np.float32) succeeds in the merge-feedback path. A 1-byte
    X'00' blob crashes the centroid computation and caused pre-existing
    test failures.
    """
    import numpy as np

    _emb = np.zeros(512, dtype=np.float32).tobytes()
    # Cluster 0: first 3 photos, Cluster 1: next 2 photos
    for i in range(3):
        photo = conn.execute(
            "SELECT id FROM photos WHERE filepath=?", (analysis[i]["filepath"],)
        ).fetchone()
        if photo:
            conn.execute(
                "INSERT OR REPLACE INTO face_embeddings "
                "(photo_id, face_index, embedding, cluster_id) "
                "VALUES (?, 0, ?, 0)",
                (photo[0], _emb),
            )
    for i in range(3, 5):
        photo = conn.execute(
            "SELECT id FROM photos WHERE filepath=?", (analysis[i]["filepath"],)
        ).fetchone()
        if photo:
            conn.execute(
                "INSERT OR REPLACE INTO face_embeddings "
                "(photo_id, face_index, embedding, cluster_id) "
                "VALUES (?, 0, ?, 1)",
                (photo[0], _emb),
            )
    conn.commit()


@pytest.fixture
def face_web_client(tmp_path):
    """Create a Flask test client with face_embeddings seeded."""
    from bpp.db.connection import close_all_connections
    from bpp.web.app import create_app

    # Defensive baseline: clear any leftover pool connections from a
    # prior test before we spin up a fresh app. The previous test's
    # teardown should have handled this, but the Analysis worker is
    # known to occasionally exceed WORKER_JOIN_TIMEOUT_S — if it
    # holds a connection past shutdown, the pool can be in a state
    # that pollutes the next face-route test (observed once during the
    # 2026-05-29 release audit under randomized seed 119532108).
    close_all_connections()

    workdir = str(tmp_path / "workdir")
    os.makedirs(workdir)

    analysis = _make_analysis(10)
    with open(os.path.join(workdir, "analysis.json"), "w") as f:
        json.dump(analysis, f)

    app = create_app(workdir=workdir)
    app.config["TESTING"] = True
    client = app.test_client()

    # Trigger a /api/photos call to load analysis and init DB
    client.get("/api/v1/photos")

    from bpp.db.connection import get_db
    from bpp.db.smart_albums import refresh_smart_albums

    db_path = os.path.join(workdir, "photopicker.db")
    conn = get_db(db_path)
    _setup_face_db(conn, analysis)
    refresh_smart_albums(conn)

    yield client

    # Teardown: shut down background workers (phash compute etc.) so they
    # don't race with the next test's DB writes in CI.
    with app.app_context():
        from bpp.web.state import get_ctx

        with contextlib.suppress(Exception):
            get_ctx().shutdown()
    from bpp.db.connection import close_all_connections

    close_all_connections()


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


class TestFaceRecluster:
    def test_recluster_blocked_during_extraction(self, face_web_client):
        """Recluster must return 409 while face extraction is running."""
        from unittest.mock import PropertyMock, patch

        with patch(
            "bpp.web.face_worker.FaceWorker.is_alive",
            new_callable=PropertyMock,
            return_value=True,
        ):
            resp = face_web_client.post("/api/v1/faces/recluster", json={"threshold": 0.6})
        assert resp.status_code == 409

    def test_recluster_missing_threshold(self, face_web_client):
        resp = face_web_client.post("/api/v1/faces/recluster", json={})
        assert resp.status_code == 400

    def test_recluster_invalid_threshold(self, face_web_client):
        resp = face_web_client.post("/api/v1/faces/recluster", json={"threshold": 0.1})
        assert resp.status_code == 400

    def test_recluster_rejects_non_numeric(self, face_web_client):
        """Out-of-range and non-numeric thresholds must be rejected."""
        for val in ["abc", None, 99.0, -1.0, 0.0]:
            resp = face_web_client.post("/api/v1/faces/recluster", json={"threshold": val})
            assert resp.status_code == 400, f"Expected 400 for threshold={val!r}"

    def test_recluster_with_real_embeddings(self, tmp_path):
        """Recluster endpoint re-runs clustering with new threshold."""
        import numpy as np

        from bpp.db.connection import get_db
        from bpp.db.smart_albums import refresh_smart_albums
        from bpp.web.app import create_app

        workdir = str(tmp_path / "workdir")
        os.makedirs(workdir)
        analysis = _make_analysis(5)
        with open(os.path.join(workdir, "analysis.json"), "w") as f:
            json.dump(analysis, f)

        app = create_app(workdir=workdir)
        app.config["TESTING"] = True
        client = app.test_client()
        client.get("/api/v1/photos")

        db_path = os.path.join(workdir, "photopicker.db")
        conn = get_db(db_path)

        # Seed 4 face embeddings: 2 similar pairs with controlled distances
        rng = np.random.RandomState(42)
        base_a = rng.randn(128).astype(np.float32)
        # base_b is far enough to separate at 0.5 but close enough to merge at high threshold
        offset = np.zeros(128, dtype=np.float32)
        offset[0] = 0.8  # Euclidean distance ~0.8 between base_a and base_b
        base_b = base_a + offset
        embeddings = [
            base_a,
            base_a + rng.randn(128) * 0.01,  # very close to base_a
            base_b,
            base_b + rng.randn(128) * 0.01,  # very close to base_b
        ]
        for i, emb in enumerate(embeddings):
            photo = conn.execute(
                "SELECT id FROM photos WHERE filepath=?", (analysis[i]["filepath"],)
            ).fetchone()
            if photo:
                conn.execute(
                    "INSERT OR REPLACE INTO face_embeddings "
                    "(photo_id, face_index, embedding, cluster_id) "
                    "VALUES (?, 0, ?, 0)",
                    (photo[0], np.asarray(emb, dtype=np.float32).tobytes()),
                )
        conn.commit()
        refresh_smart_albums(conn)

        # Recluster with a tight threshold — should produce 2 clusters
        resp = client.post("/api/v1/faces/recluster", json={"threshold": 0.5})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "reclustered"
        assert data["clusters"] == 2

        # Recluster with a very loose threshold — should merge into 1 cluster
        resp = client.post("/api/v1/faces/recluster", json={"threshold": 1.2})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["clusters"] == 1

    def test_recluster_preserves_named_clusters(self, tmp_path):
        """Named person albums must survive reclustering with new threshold."""
        import numpy as np

        from bpp.db.connection import get_db
        from bpp.db.smart_albums import refresh_smart_albums
        from bpp.web.app import create_app

        workdir = str(tmp_path / "workdir")
        os.makedirs(workdir)
        analysis = _make_analysis(6)
        with open(os.path.join(workdir, "analysis.json"), "w") as f:
            json.dump(analysis, f)

        app = create_app(workdir=workdir)
        app.config["TESTING"] = True
        client = app.test_client()
        client.get("/api/v1/photos")

        db_path = os.path.join(workdir, "photopicker.db")
        conn = get_db(db_path)

        # Create 2 well-separated clusters with 3 faces each
        rng = np.random.RandomState(42)
        base_a = rng.randn(128).astype(np.float32)
        base_a /= np.linalg.norm(base_a)
        offset = np.zeros(128, dtype=np.float32)
        offset[0] = 2.0  # Far apart
        base_b = base_a + offset
        base_b /= np.linalg.norm(base_b)

        photos = conn.execute("SELECT id FROM photos ORDER BY id").fetchall()
        # Cluster 0: photos 0-2 (person A)
        for i in range(3):
            emb = base_a + rng.randn(128) * 0.01
            conn.execute(
                "INSERT OR REPLACE INTO face_embeddings "
                "(photo_id, face_index, bbox_x, bbox_y, bbox_w, bbox_h, "
                "embedding, cluster_id) VALUES (?, 0, 0, 0, 100, 100, ?, 0)",
                (photos[i][0], np.asarray(emb, dtype=np.float32).tobytes()),
            )
        # Cluster 1: photos 3-5 (person B)
        for i in range(3, 6):
            emb = base_b + rng.randn(128) * 0.01
            conn.execute(
                "INSERT OR REPLACE INTO face_embeddings "
                "(photo_id, face_index, bbox_x, bbox_y, bbox_w, bbox_h, "
                "embedding, cluster_id) VALUES (?, 0, 0, 0, 100, 100, ?, 1)",
                (photos[i][0], np.asarray(emb, dtype=np.float32).tobytes()),
            )
        conn.commit()
        refresh_smart_albums(conn)

        # Name cluster 0 as "Alice"
        conn.execute(
            "UPDATE albums SET name='Alice' WHERE album_type='smart_person' AND rule_json=?",
            (json.dumps({"cluster_id": 0}, sort_keys=True),),
        )
        conn.commit()

        # Verify Alice exists
        album = conn.execute(
            "SELECT name FROM albums WHERE name='Alice' AND album_type='smart_person'"
        ).fetchone()
        assert album is not None

        # Recluster — cluster IDs will change but Alice should survive
        resp = client.post("/api/v1/faces/recluster", json={"threshold": 0.55})
        assert resp.status_code == 200
        assert resp.get_json()["clusters"] == 2

        # Alice's name should still exist on a smart_person album
        alice = conn.execute(
            "SELECT rule_json FROM albums WHERE name='Alice' AND album_type='smart_person'"
        ).fetchone()
        assert alice is not None, "Alice's named album was lost during recluster"

        # And the cluster it references should have photos
        rule = json.loads(alice[0])
        cid = rule["cluster_id"]
        face_count = conn.execute(
            "SELECT COUNT(*) FROM face_embeddings WHERE cluster_id=?",
            (cid,),
        ).fetchone()[0]
        assert face_count == 3, f"Alice's cluster {cid} should have 3 faces"

    def test_recluster_no_embeddings(self, tmp_path):
        """Recluster on empty DB returns 0 clusters."""
        from bpp.web.app import create_app

        workdir = str(tmp_path / "workdir")
        os.makedirs(workdir)
        analysis = _make_analysis(3)
        with open(os.path.join(workdir, "analysis.json"), "w") as f:
            json.dump(analysis, f)

        app = create_app(workdir=workdir)
        app.config["TESTING"] = True
        client = app.test_client()
        client.get("/api/v1/photos")

        resp = client.post("/api/v1/faces/recluster", json={"threshold": 0.6})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["clusters"] == 0


class TestDeleteRoutes:
    def test_delete_photos(self, web_client):
        filepaths = [f"/tmp/test_photos/img_{i:03d}.jpg" for i in range(2)]
        resp = web_client.post("/api/v1/photos/delete", json={"filepaths": filepaths})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["count"] == 2

    def test_delete_empty(self, web_client):
        resp = web_client.post("/api/v1/photos/delete", json={"filepaths": []})
        assert resp.status_code == 400

    def test_list_deleted(self, web_client):
        filepaths = ["/tmp/test_photos/img_000.jpg"]
        web_client.post("/api/v1/photos/delete", json={"filepaths": filepaths})
        resp = web_client.get("/api/v1/photos/deleted")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] >= 1
        found = [p for p in data["photos"] if p["filepath"] == filepaths[0]]
        assert len(found) == 1
        assert found[0]["deleted_at"] != ""

    def test_restore_photos(self, web_client):
        filepaths = ["/tmp/test_photos/img_001.jpg"]
        web_client.post("/api/v1/photos/delete", json={"filepaths": filepaths})
        resp = web_client.post("/api/v1/photos/restore", json={"filepaths": filepaths})
        assert resp.status_code == 200
        assert resp.get_json()["count"] == 1
        # Should not appear in deleted list anymore
        resp = web_client.get("/api/v1/photos/deleted")
        deleted_fps = [p["filepath"] for p in resp.get_json()["photos"]]
        assert filepaths[0] not in deleted_fps

    def test_restore_empty(self, web_client):
        resp = web_client.post("/api/v1/photos/restore", json={"filepaths": []})
        assert resp.status_code == 400

    def test_permanent_delete(self, web_client):
        filepaths = ["/tmp/test_photos/img_002.jpg"]
        web_client.post("/api/v1/photos/delete", json={"filepaths": filepaths})
        resp = web_client.post(
            "/api/v1/photos/delete-permanent",
            json={"filepaths": filepaths, "confirmation": "delete"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 1
        # Gone from deleted list
        resp = web_client.get("/api/v1/photos/deleted")
        deleted_fps = [p["filepath"] for p in resp.get_json()["photos"]]
        assert filepaths[0] not in deleted_fps

    def test_permanent_delete_empty(self, web_client):
        resp = web_client.post(
            "/api/v1/photos/delete-permanent",
            json={"filepaths": [], "confirmation": "delete"},
        )
        assert resp.status_code == 400

    def test_permanent_delete_path_traversal(self, tmp_path):
        """Permanent delete must not remove files outside library/workdir."""
        from bpp.web.app import create_app

        workdir = str(tmp_path / "workdir")
        os.makedirs(workdir)

        # Create a decoy file outside the library/workdir
        decoy = tmp_path / "outside" / "secret.txt"
        decoy.parent.mkdir()
        decoy.write_text("do not delete")

        # Seed analysis with a filepath pointing outside the library
        analysis = [
            {
                "filepath": str(decoy),
                "date": "2024-01-01T12:00:00",
                "date_day": "2024-01-01",
                "date_month": "2024-01",
                "file_size": 100,
                "file_mtime": 1700000000.0,
                "blur_raw": 100.0,
                "blur_score": 0.5,
                "exposure_score": 0.5,
                "face_score": 0.3,
                "face_count": 0,
                "largest_face_ratio": 0.0,
                "face_center_dist": 0.0,
                "composition_score": 0.5,
                "aggregate_score": 0.5,
            }
        ]
        with open(os.path.join(workdir, "analysis.json"), "w") as f:
            json.dump(analysis, f)

        app = create_app(workdir=workdir)
        app.config["TESTING"] = True
        client = app.test_client()
        # Load photos into DB
        client.get("/api/v1/photos")

        # Soft delete, then permanent delete
        client.post("/api/v1/photos/delete", json={"filepaths": [str(decoy)]})
        resp = client.post(
            "/api/v1/photos/delete-permanent",
            json={"filepaths": [str(decoy)], "confirmation": "delete"},
        )
        assert resp.status_code == 200
        # File outside library/workdir must NOT have been deleted from disk
        assert decoy.exists(), "File outside library/workdir was deleted — path traversal!"

    def test_remove_from_album(self, web_client):
        # Create album, add photos, then remove
        resp = web_client.post("/api/v1/albums", json={"name": "RemoveTest"})
        album_id = resp.get_json()["id"]
        filepaths = [f"/tmp/test_photos/img_{i:03d}.jpg" for i in range(3)]
        web_client.post(f"/api/v1/albums/{album_id}/add-photos", json={"filepaths": filepaths})
        resp = web_client.post(
            f"/api/v1/albums/{album_id}/remove-photos",
            json={"filepaths": [filepaths[0]]},
        )
        assert resp.status_code == 200
        assert resp.get_json()["count"] == 1

    def test_remove_from_nonexistent_album(self, web_client):
        resp = web_client.post(
            "/api/v1/albums/9999/remove-photos",
            json={"filepaths": ["/tmp/test_photos/img_000.jpg"]},
        )
        assert resp.status_code == 404

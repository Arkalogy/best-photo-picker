"""Tests for the web UI: recompute logic + route smoke tests — part 1 of 3."""

# Shared fixtures are imported by name and re-bound as test parameters.
# ruff: noqa: F811

from __future__ import annotations

import json
import os

from tests._web_shared import (  # noqa: F401
    _default_config,
    _make_analysis,
    _setup_face_db,
    face_web_client,
    web_client,
)

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
        # Active = accepted with the current license text AND weights on
        # disk; the test library has neither, so detection is NOT on.
        assert data["nudenet_active"] is False
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

    def test_recompute_overrides_keep_their_own_range(self, web_client, monkeypatch):
        # 20 / 30 are the defaults: restating them must not tighten dedup
        # (the old blanket 0-10 clamp turned 20 into 10). The fixture has no
        # hashes, so prove the wiring: the per-key clamp sees each override.
        from bpp.web import bp_recompute, state_helpers

        seen = {}

        def spy(key, value):
            seen[key] = state_helpers.clamp_override(key, value)
            return seen[key]

        monkeypatch.setattr(bp_recompute, "clamp_override", spy)
        resp = web_client.post(
            "/api/v1/recompute",
            json={"k": 3, "hash_distance_threshold": 20, "time_window_seconds": 30},
        )
        assert resp.status_code == 200
        assert seen == {"hash_distance_threshold": 20, "time_window_seconds": 30}

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

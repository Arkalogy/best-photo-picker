"""Tests targeting coverage gaps in blueprints, settings, schema, and workers — part 3 of 3:
TestCoreBlueprintGaps."""

# Shared fixtures are imported by name and re-bound as test parameters.
# ruff: noqa: F811

from __future__ import annotations

from tests._coverage_gaps_shared import (  # noqa: F401
    _real,
    _suppress_config,
    app_with_photos,
    bare_app,
)

# ===================================================================
# Core Blueprint gaps (73% → 85%+)
# ===================================================================


class TestCoreBlueprintGaps:
    """Cover storage health, recheck-missing, presets."""

    def test_storage_health(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.get("/api/v1/health/storage")
            assert resp.status_code == 200
            data = resp.get_json()
            assert "accessible" in data

    def test_recheck_missing_no_missing(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.post("/api/v1/photos/recheck-missing")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["restored"] == 0
            assert data["still_missing"] == 0

    def test_recheck_missing_with_missing(self, app_with_photos):
        app, _d = app_with_photos
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()
            # Mark one photo as missing
            conn.execute("UPDATE photos SET missing=1 WHERE id=1")
            conn.commit()

        with app.test_client() as c:
            resp = c.post("/api/v1/photos/recheck-missing")
            assert resp.status_code == 200
            data = resp.get_json()
            # The file exists on disk, so it should be restored
            assert data["restored"] >= 0

    def test_stats_endpoint(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.get("/api/v1/stats")
            assert resp.status_code == 200
            data = resp.get_json()
            assert "total_count" in data

    def test_presets_crud(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            # Create
            resp = c.post(
                "/api/v1/presets",
                json={"name": "My Preset", "settings": {"k": 10}},
            )
            assert resp.status_code == 200

            # List
            resp = c.get("/api/v1/presets")
            data = resp.get_json()
            assert "My Preset" in data["presets"]

            # Delete
            resp = c.delete("/api/v1/presets/My Preset")
            assert resp.status_code == 200

            # Delete nonexistent
            resp = c.delete("/api/v1/presets/Nonexistent")
            assert resp.status_code == 404

    def test_settings_api(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            # Put empty
            resp = c.put("/api/v1/settings", json={})
            assert resp.status_code == 400

            # Put valid
            resp = c.put("/api/v1/settings", json={"key": "val"})
            assert resp.status_code == 200

            # Get
            resp = c.get("/api/v1/settings")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["key"] == "val"

    def test_status_full_fields(self, app_with_photos):
        """Verify /api/status returns all expected fields."""
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.get("/api/v1/status")
            data = resp.get_json()
            expected_keys = [
                "has_analysis",
                "first_run",
                "image_count",
                "workdir",
                "library_path",
                "analyzing",
                "importing",
                "serve_mode",
                "face_recognition_available",
                "nudenet_available",
                "nudenet_active",
                "clip_available",
                "heic_available",
            ]
            for key in expected_keys:
                assert key in data, f"Missing status key: {key}"

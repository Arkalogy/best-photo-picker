"""Tests targeting coverage gaps in blueprints, settings, schema, and workers — part 1 of 3."""

# Shared fixtures are imported by name and re-bound as test parameters.
# ruff: noqa: F811

from __future__ import annotations

import os

import pytest
from PIL import Image

from tests._coverage_gaps_shared import (  # noqa: F401
    _real,
    _suppress_config,
    app_with_photos,
    bare_app,
)

# ===================================================================
# Memories Blueprint (29% → 80%+)
# ===================================================================


class TestMemoriesBlueprint:
    """Cover all 3 endpoints in bp_memories."""

    def test_list_memories_empty(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.get("/api/v1/memories")
            assert resp.status_code == 200
            data = resp.get_json()
            assert "memories" in data
            assert isinstance(data["memories"], list)

    def test_memory_detail_not_found(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.get("/api/v1/memories/9999")
            assert resp.status_code == 404

    def test_refresh_memories_empty(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.post("/api/v1/memories/refresh")
            assert resp.status_code == 200
            data = resp.get_json()
            assert "count" in data

    @pytest.fixture()
    def app_with_clustered_photos(self, tmp_path, _suppress_config):
        """App with photos close enough in time to form a memory cluster (< 4h gap)."""
        d = _real(tmp_path)
        for i in range(5):
            p = os.path.join(d, f"photo_{i}.jpg")
            Image.new("RGB", (100, 100), (50 + i * 40, 100, 150)).save(p, "JPEG")

        from bpp.web.app import create_app

        app = create_app(workdir=d, input_dir=d, library_path=d)
        app.config["TESTING"] = True

        with app.app_context():
            from bpp.db.albums import sync_all_photos_album
            from bpp.db.photos import upsert_photo
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()
            # All 5 photos within 1 hour → single cluster ≥ 3 photos → 1 memory
            for i in range(5):
                upsert_photo(
                    conn,
                    {
                        "filepath": os.path.join(d, f"photo_{i}.jpg"),
                        "aggregate_score": 0.5 + i * 0.1,
                        "blur_score": 0.7,
                        "exposure_score": 0.8,
                        "face_score": 0.3,
                        "composition_score": 0.6,
                        "date": f"2024-06-15T12:{i * 10:02d}:00",
                        "date_day": "2024-06-15",
                        "date_month": "2024-06",
                        "sha256": f"cluster{i:04d}",
                    },
                )
            sync_all_photos_album(conn)
            ctx.invalidate_analysis()

        return app, d

    def test_refresh_memories_with_photos(self, app_with_clustered_photos):
        app, _ = app_with_clustered_photos
        with app.test_client() as c:
            resp = c.post("/api/v1/memories/refresh")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["count"] >= 1

    def test_list_memories_after_refresh(self, app_with_clustered_photos):
        app, _ = app_with_clustered_photos
        with app.test_client() as c:
            c.post("/api/v1/memories/refresh")
            resp = c.get("/api/v1/memories")
            assert resp.status_code == 200
            data = resp.get_json()
            assert len(data["memories"]) >= 1

    def test_memory_detail_after_refresh(self, app_with_clustered_photos):
        app, _ = app_with_clustered_photos
        with app.test_client() as c:
            refresh_resp = c.post("/api/v1/memories/refresh")
            memories = refresh_resp.get_json().get("memories", [])
            assert len(memories) >= 1
            mid = memories[0]["id"]
            resp = c.get(f"/api/v1/memories/{mid}")
            assert resp.status_code == 200
            data = resp.get_json()
            assert "photos" in data
            assert len(data["photos"]) >= 3

    def test_memory_detail_excludes_photos_inactivated_after_generation(
        self, app_with_clustered_photos
    ):
        """A memory's photo_ids are a snapshot; a member can become inactive
        AFTER generation (deleted, hidden, or tagged as a Live Photo sidecar
        by the phash backfill). The detail endpoint must re-check the active
        filter at resolution time -- stored id lists never bypass it.

        Regression: sidecar-tagged photos leaked into a user-facing view
        with 0% scores (2026-06-12)."""
        app, _ = app_with_clustered_photos
        with app.test_client() as c:
            refresh_resp = c.post("/api/v1/memories/refresh")
            mid = refresh_resp.get_json()["memories"][0]["id"]
            before = c.get(f"/api/v1/memories/{mid}").get_json()["photos"]
            assert len(before) >= 3

            # Tag one member as a sidecar after the memory was generated.
            with app.app_context():
                from bpp.web.state import get_ctx

                conn = get_ctx().get_conn()
                conn.execute(
                    "UPDATE photos SET is_live_photo_sidecar=1 WHERE id=?",
                    (before[0]["id"],),
                )
                conn.commit()

            after = c.get(f"/api/v1/memories/{mid}").get_json()["photos"]
            after_ids = {p["id"] for p in after}
            assert before[0]["id"] not in after_ids, (
                f"sidecar-tagged photo {before[0]['id']} still served by "
                f"memories detail: {sorted(after_ids)}"
            )
            assert len(after) == len(before) - 1


# ===================================================================
# Calendar Blueprint (64% → 80%+)
# ===================================================================


class TestCalendarBlueprint:
    """Cover calendar endpoints."""

    def test_calendar_months_empty(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.get("/api/v1/calendar/months")
            assert resp.status_code == 200
            data = resp.get_json()
            assert "months" in data

    def test_calendar_months_with_photos(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.get("/api/v1/calendar/months")
            assert resp.status_code == 200
            data = resp.get_json()
            assert len(data["months"]) >= 1

    def test_calendar_days_missing_params(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.get("/api/v1/calendar/days")
            assert resp.status_code == 400

    def test_calendar_days_invalid_month(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.get("/api/v1/calendar/days?year=2024&month=13")
            assert resp.status_code == 400

    def test_calendar_days_valid(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.get("/api/v1/calendar/days?year=2024&month=6")
            assert resp.status_code == 200
            data = resp.get_json()
            assert "days" in data
            assert data["year"] == 2024
            assert data["month"] == 6

    def test_calendar_year_missing_param(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.get("/api/v1/calendar/year")
            assert resp.status_code == 400

    def test_calendar_year_invalid(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.get("/api/v1/calendar/year?year=999")
            assert resp.status_code == 400

    def test_calendar_year_valid(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.get("/api/v1/calendar/year?year=2024")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["year"] == 2024
            assert "months" in data

    def test_calendar_photos_missing_params(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.get("/api/v1/calendar/photos")
            assert resp.status_code == 400

    def test_calendar_photos_by_date(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.get("/api/v1/calendar/photos?date=2024-06-15")
            assert resp.status_code == 200
            data = resp.get_json()
            assert "photos" in data

    def test_calendar_photos_by_range(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.get("/api/v1/calendar/photos?start=2024-06-15&end=2024-06-20")
            assert resp.status_code == 200
            data = resp.get_json()
            assert "photos" in data
            assert len(data["photos"]) >= 1

    def test_on_this_day_empty(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.get("/api/v1/on-this-day")
            assert resp.status_code == 200
            data = resp.get_json()
            assert "years" in data

    def test_on_this_day_with_params(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.get("/api/v1/on-this-day?month=6&day=15")
            assert resp.status_code == 200
            data = resp.get_json()
            assert "years" in data

    def test_on_this_day_invalid_month(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.get("/api/v1/on-this-day?month=13")
            assert resp.status_code == 400

    def test_on_this_day_invalid_day(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.get("/api/v1/on-this-day?day=32")
            assert resp.status_code == 400


# ===================================================================
# Settings DB (68% → 90%+)
# ===================================================================


class TestSettingsDB:
    """Cover get_setting, set_setting, delete_setting functions."""

    @pytest.fixture()
    def conn(self, tmp_path):
        from bpp.db.connection import init_db

        db_path = str(tmp_path / "photopicker.db")
        return init_db(db_path)

    def test_get_setting_default(self, conn):
        from bpp.db.settings import get_setting

        assert get_setting(conn, "nonexistent") is None
        assert get_setting(conn, "nonexistent", "fallback") == "fallback"

    def test_set_and_get_setting(self, conn):
        from bpp.db.settings import get_setting, set_setting

        set_setting(conn, "my_key", "my_value")
        assert get_setting(conn, "my_key") == "my_value"

    def test_set_setting_upsert(self, conn):
        from bpp.db.settings import get_setting, set_setting

        set_setting(conn, "k", "v1")
        set_setting(conn, "k", "v2")
        assert get_setting(conn, "k") == "v2"

    def test_delete_setting(self, conn):
        from bpp.db.settings import delete_setting, get_setting, set_setting

        set_setting(conn, "temp", "val")
        assert get_setting(conn, "temp") == "val"
        delete_setting(conn, "temp")
        assert get_setting(conn, "temp") is None

    def test_set_setting_numeric(self, conn):
        from bpp.db.settings import get_setting, set_setting

        set_setting(conn, "threshold", 0.55)
        assert get_setting(conn, "threshold") == "0.55"

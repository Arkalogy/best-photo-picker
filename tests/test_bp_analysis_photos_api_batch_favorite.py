"""Tests for bp_analysis and bp_photos blueprints (uncovered endpoints) — part 3 of 3:
TestApiBatchFavorite, TestApiPhotosDelete, TestApiPhotosRestore."""

# Shared fixtures are imported by name and re-bound as test parameters.
# ruff: noqa: F811

from __future__ import annotations

import os

import pytest

from tests._bp_analysis_photos_shared import (  # noqa: F401
    _real,
    _suppress_config,
    app_with_photos,
    bare_app,
)


class TestApiBatchFavorite:
    """POST /api/batch/favorite"""

    def test_batch_favorite_empty_filepaths_returns_400(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.post(
                "/api/v1/batch/favorite",
                json={"filepaths": []},
            )
            assert resp.status_code == 400

    def test_batch_favorite_success(self, app_with_photos):
        app, d = app_with_photos
        fps = [os.path.join(d, f"photo_{i}.jpg") for i in range(3)]
        with app.test_client() as c:
            resp = c.post(
                "/api/v1/batch/favorite",
                json={"filepaths": fps, "favorite": True},
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "ok"
            assert data["count"] == 3


class TestApiPhotosDelete:
    """POST /api/photos/delete (soft-delete)"""

    def test_delete_missing_filepaths_returns_400(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.post(
                "/api/v1/photos/delete",
                json={"filepaths": []},
            )
            assert resp.status_code == 400

    def test_soft_delete_success(self, app_with_photos):
        app, d = app_with_photos
        fp = os.path.join(d, "photo_0.jpg")
        with app.test_client() as c:
            resp = c.post(
                "/api/v1/photos/delete",
                json={"filepaths": [fp]},
            )
            assert resp.status_code == 200
            assert resp.get_json()["count"] == 1


class TestApiPhotosRestore:
    """POST /api/photos/restore"""

    def test_restore_missing_filepaths_returns_400(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.post(
                "/api/v1/photos/restore",
                json={"filepaths": []},
            )
            assert resp.status_code == 400

    def test_restore_success(self, app_with_photos):
        app, d = app_with_photos
        fp = os.path.join(d, "photo_0.jpg")
        with app.test_client() as c:
            c.post(
                "/api/v1/photos/delete",
                json={"filepaths": [fp]},
            )
            resp = c.post(
                "/api/v1/photos/restore",
                json={"filepaths": [fp]},
            )
            assert resp.status_code == 200
            assert resp.get_json()["count"] == 1
            deleted = c.get("/api/v1/photos/deleted").get_json()
            assert deleted["total"] == 0


class TestApiPhotosDeletePermanent:
    """POST /api/photos/delete-permanent"""

    def test_permanent_delete_missing_filepaths_returns_400(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.post(
                "/api/v1/photos/delete-permanent",
                json={"filepaths": [], "confirmation": "delete"},
            )
            assert resp.status_code == 400

    def test_permanent_delete_removes_from_db_and_disk(self, app_with_photos):
        app, d = app_with_photos
        fp = os.path.join(d, "photo_0.jpg")
        assert os.path.isfile(fp)
        with app.test_client() as c:
            # Must soft-delete first — permanent delete requires it
            c.post("/api/v1/photos/delete", json={"filepaths": [fp]})
            resp = c.post(
                "/api/v1/photos/delete-permanent",
                json={"filepaths": [fp], "confirmation": "delete"},
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["count"] == 1
            assert data["files_removed"] == 1
            assert not os.path.isfile(fp)


class TestApiRecompute:
    """POST /api/recompute"""

    def test_recompute_no_analysis_returns_404(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.post("/api/v1/recompute", json={})
            assert resp.status_code == 404

    def test_recompute_returns_photos(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.post("/api/v1/recompute", json={"k": 2})
            assert resp.status_code == 200
            data = resp.get_json()
            assert "photos" in data
            assert "selected_paths" in data
            assert "stats" in data
            assert len(data["photos"]) == 3

    def test_recompute_delta_mode(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.post(
                "/api/v1/recompute",
                json={"k": 2, "delta": True},
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert "scores" in data
            assert "selected_paths" in data
            assert "photos" not in data

    def test_recompute_with_weights(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.post(
                "/api/v1/recompute",
                json={
                    "k": 2,
                    "blur_weight": 0.5,
                    "exposure_weight": 0.3,
                    "face_weight": 0.1,
                    "composition_weight": 0.1,
                },
            )
            assert resp.status_code == 200
            selected = resp.get_json()["selected_paths"]
            assert len(selected) <= 2

    def test_recompute_stats_fields(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.post("/api/v1/recompute", json={"k": 50})
            stats = resp.get_json()["stats"]
            assert "total" in stats
            assert "after_dedupe" in stats
            assert "total_selected" in stats
            assert "dedup_mode" in stats

    def test_recompute_sensitive_in_picks_exclude_filters(self, app_with_photos):
        """End-to-end: sensitive_in_picks='exclude' in the POST body threads
        into cfg and drops the sensitive photo from selection; 'allow' keeps
        it. Guards the bp_recompute → recompute() string-param wiring (the
        param does NOT ride the float weight-key path)."""
        app, d = app_with_photos
        sensitive_fp = os.path.join(d, "photo_2.jpg")
        with app.app_context():
            from bpp.db.photos import upsert_photo
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            # Distinct phash AND ahash (pairwise hamming > 8) so hash-dedup
            # keeps all three as separate candidates — isolates the sensitive
            # filter from dedup collapse (the fixture's 3 images are
            # identical, and a NULL ahash otherwise merges them).
            hashes = [0x0, 0x7FFFFFFF00000000, 0x000000007FFFFFFF]
            for i in range(3):
                fp = os.path.join(d, f"photo_{i}.jpg")
                row = {
                    "filepath": fp,
                    # Sensitive photo is the top scorer, so in "allow" mode it
                    # is unambiguously selected; only the policy can drop it.
                    "aggregate_score": 0.99 if fp == sensitive_fp else 0.5 + i * 0.1,
                    "blur_score": 0.7,
                    "blur_raw": 100.0,
                    "exposure_score": 0.8,
                    "face_score": 0.3,
                    "composition_score": 0.6,
                    "phash": hashes[i],
                    "ahash": hashes[i],
                    "date": f"2024-01-{i + 1:02d}T12:00:00",
                    "date_day": f"2024-01-{i + 1:02d}",
                    "date_month": "2024-01",
                }
                if fp == sensitive_fp:
                    row["nudity_score"] = 0.95  # over SENSITIVE_NUDITY_THRESHOLD
                upsert_photo(ctx.get_conn(), row)
            ctx.invalidate_analysis()

        with app.test_client() as c:
            excl = c.post(
                "/api/v1/recompute",
                json={"k": 50, "delta": True, "sensitive_in_picks": "exclude"},
            ).get_json()["selected_paths"]
            assert sensitive_fp not in excl
            assert len(excl) == 2  # the two non-sensitive photos still picked

            allow = c.post(
                "/api/v1/recompute",
                json={"k": 50, "delta": True, "sensitive_in_picks": "allow"},
            ).get_json()["selected_paths"]
            assert sensitive_fp in allow

    def test_recompute_413_when_full_payload_too_large(self, app_with_photos, monkeypatch):
        """The cap on /api/v1/recompute fires BEFORE
        recompute() runs. Previously the check was post-recompute, so
        a 50k-photo library still paid the full CPU/RAM cost just to
        be 413'd. Inject 5001 fake analysis entries and assert
        recompute() is never called."""
        from bpp.web import bp_recompute as bp_photos

        app, _ = app_with_photos

        fake_analysis = [
            {
                "id": i,
                "filepath": f"/tmp/fake_{i}.jpg",
                "aggregate_score": 0.5,
                "blur_raw": 100.0,
                "deleted_at": None,
            }
            for i in range(5001)
        ]

        with app.app_context():
            ctx = app.extensions["bpp"]
            monkeypatch.setattr(ctx, "load_analysis_if_needed", lambda: fake_analysis)

        # If the cap moves back to post-recompute, this monkeypatch
        # will fail loudly with pytest.fail and the test will fail.
        def _must_not_run(_opts):
            pytest.fail("recompute() was called even though payload exceeds the cap")

        monkeypatch.setattr(bp_photos, "recompute", _must_not_run)

        with app.test_client() as c:
            resp = c.post("/api/v1/recompute", json={"k": 5})
        assert resp.status_code == 413, (
            f"Expected 413 for >5000-photo non-delta payload, got {resp.status_code}"
        )
        data = resp.get_json()
        assert data.get("code") == "delta_required"
        assert "delta" in data["error"].lower()
        assert data["context"]["photo_count"] == 5001

    def test_recompute_413_skipped_in_delta_mode(self, app_with_photos, monkeypatch):
        """Inverse: delta mode bypasses the cap and runs recompute()
        normally — clients above the size threshold use delta on
        purpose. Tripping the cap in delta mode would defeat the
        whole fallback path."""
        from bpp.web import bp_recompute as bp_photos

        app, _ = app_with_photos

        fake_analysis = [
            {
                "id": i,
                "filepath": f"/tmp/fake_{i}.jpg",
                "aggregate_score": 0.5,
                "blur_raw": 100.0,
                "deleted_at": None,
            }
            for i in range(5001)
        ]

        with app.app_context():
            ctx = app.extensions["bpp"]
            monkeypatch.setattr(ctx, "load_analysis_if_needed", lambda: fake_analysis)

        # Stub recompute to a cheap response so the test doesn't
        # actually run the selection pipeline on 5001 fake entries.
        recompute_calls: list[int] = []

        def _stub_recompute(opts):
            recompute_calls.append(len(opts.analysis))
            return {
                "selected_paths": [],
                "photos": opts.analysis,
                "stats": {"total": len(opts.analysis), "total_selected": 0},
            }

        monkeypatch.setattr(bp_photos, "recompute", _stub_recompute)

        with app.test_client() as c:
            resp = c.post("/api/v1/recompute", json={"k": 5, "delta": True})
        assert resp.status_code == 200
        data = resp.get_json()
        assert "photos" not in data
        assert "selected_paths" in data
        assert "scores" in data
        assert len(recompute_calls) == 1, "recompute() must run in delta mode"


class TestApiPhotos:
    """GET /api/photos"""

    def test_no_analysis_returns_404(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.get("/api/v1/photos")
            assert resp.status_code == 404

    def test_with_analysis(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.get("/api/v1/photos")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["count"] == 3
            assert len(data["photos"]) == 3

    def test_photo_fields(self, app_with_photos):
        """Verify all expected fields are present in photo dicts."""
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.get("/api/v1/photos")
            photo = resp.get_json()["photos"][0]
            for field in (
                "filepath",
                "filename",
                "date",
                "blur_score",
                "exposure_score",
                "face_score",
                "composition_score",
                "aggregate_score",
                "thumb_hash",
                "cluster_size",
            ):
                assert field in photo, f"missing {field}"

    # ── Pagination ─────────────────────────────────────────────────

    def test_pagination_metadata_present(self, app_with_photos):
        """Response must include total/limit/offset/has_more so
        clients know whether to fetch more."""
        app, _ = app_with_photos
        with app.test_client() as c:
            data = c.get("/api/v1/photos").get_json()
            for k in ("total", "limit", "offset", "has_more", "count"):
                assert k in data, f"missing pagination field {k}"
            assert data["total"] == 3
            assert data["offset"] == 0
            assert data["has_more"] is False  # 3 photos all fit in default page

    def test_pagination_limit(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            data = c.get("/api/v1/photos?limit=2").get_json()
            assert data["count"] == 2
            assert len(data["photos"]) == 2
            assert data["total"] == 3
            assert data["limit"] == 2
            assert data["has_more"] is True

    def test_pagination_offset(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            page1 = c.get("/api/v1/photos?limit=2&offset=0").get_json()
            page2 = c.get("/api/v1/photos?limit=2&offset=2").get_json()
            assert page1["count"] == 2
            assert page2["count"] == 1
            assert page2["has_more"] is False
            # Pages are disjoint: filepaths shouldn't overlap
            page1_paths = {p["filepath"] for p in page1["photos"]}
            page2_paths = {p["filepath"] for p in page2["photos"]}
            assert page1_paths.isdisjoint(page2_paths)

    def test_pagination_offset_past_end_is_empty_not_error(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            data = c.get("/api/v1/photos?offset=999").get_json()
            assert data["count"] == 0
            assert data["photos"] == []
            assert data["has_more"] is False
            assert data["total"] == 3

    def test_pagination_clamps_bad_input(self, app_with_photos):
        """Negative offset / non-numeric limit / oversized limit are
        clamped to safe defaults rather than 400-ing — listing APIs
        are forgiving."""
        app, _ = app_with_photos
        with app.test_client() as c:
            # Negative offset → clamped to 0
            r1 = c.get("/api/v1/photos?offset=-5").get_json()
            assert r1["offset"] == 0
            # Non-numeric → default
            r2 = c.get("/api/v1/photos?limit=banana&offset=banana").get_json()
            assert r2["limit"] == 5000
            assert r2["offset"] == 0
            # Oversized limit → capped at 50000
            r3 = c.get("/api/v1/photos?limit=999999").get_json()
            assert r3["limit"] == 50000

    def test_default_limit_caps_response(self, app_with_photos):
        """Without explicit limit, default cap is 5000 (so a
        100k-photo library doesn't ship the world in one response)."""
        app, _ = app_with_photos
        with app.test_client() as c:
            data = c.get("/api/v1/photos").get_json()
            assert data["limit"] == 5000

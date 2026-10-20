"""Comprehensive Flask blueprint route tests for maximum coverage — part 5 of 5: TestAlbumWorkflow,
TestDedupFeedbackStats."""

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

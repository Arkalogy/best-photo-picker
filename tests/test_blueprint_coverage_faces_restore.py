"""Comprehensive Flask blueprint route tests for maximum coverage — part 4 of 5: TestFacesRestore,
TestAvatarPickerQuality, TestFacesRecluster."""

# Shared fixtures are imported by name and re-bound as test parameters.
# ruff: noqa: F811

from __future__ import annotations

import numpy as np

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

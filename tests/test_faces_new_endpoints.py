"""Tests for new face endpoints: tag, untag, reassign, avatar, cluster detail, photo faces — part 1
of 2."""

# Shared fixtures are imported by name and re-bound as test parameters.
# ruff: noqa: F811

from __future__ import annotations

import json

import numpy as np

from bpp.constants import CLUSTER_DISMISSED, CLUSTER_UNASSIGNED
from tests._faces_new_endpoints_shared import (  # noqa: F401
    _ctx,
    _get_photo_id_and_hash,
    _insert_face,
    _make_analysis,
    bp_app,
    client,
)

# ── Phase 1.1: POST /api/faces/tag ──


class TestTagPerson:
    def test_tag_missing_params(self, client):
        resp = client.post("/api/v1/faces/tag", json={}, content_type="application/json")
        assert resp.status_code == 400
        assert "required" in resp.get_json()["error"]

    def test_tag_missing_cluster_id(self, client):
        resp = client.post(
            "/api/v1/faces/tag",
            json={"path_hash": "abc"},
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_tag_negative_cluster_id(self, client):
        resp = client.post(
            "/api/v1/faces/tag",
            json={"path_hash": "abc", "cluster_id": -1},
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert "non-negative" in resp.get_json()["error"]

    def test_tag_unknown_hash(self, client):
        resp = client.post(
            "/api/v1/faces/tag",
            json={"path_hash": "deadbeef00000000", "cluster_id": 0},
            content_type="application/json",
        )
        assert resp.status_code == 404

    def test_tag_valid(self, client, bp_app):
        pid, th = _get_photo_id_and_hash(bp_app)
        assert th is not None, "thumbs not loaded"
        resp = client.post(
            "/api/v1/faces/tag",
            json={"path_hash": th, "cluster_id": 0},
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "tagged"
        # Verify row in DB
        ctx = _ctx(bp_app)
        with bp_app.app_context():
            conn = ctx.get_conn()
            row = conn.execute(
                "SELECT * FROM photo_person_tags WHERE photo_id=? AND cluster_id=0",
                (pid,),
            ).fetchone()
            assert row is not None

    def test_tag_duplicate_is_noop(self, client, bp_app):
        pid, th = _get_photo_id_and_hash(bp_app)
        # Tag twice
        client.post(
            "/api/v1/faces/tag",
            json={"path_hash": th, "cluster_id": 0},
            content_type="application/json",
        )
        resp = client.post(
            "/api/v1/faces/tag",
            json={"path_hash": th, "cluster_id": 0},
            content_type="application/json",
        )
        assert resp.status_code == 200
        # Only one row
        ctx = _ctx(bp_app)
        with bp_app.app_context():
            conn = ctx.get_conn()
            count = conn.execute(
                "SELECT COUNT(*) FROM photo_person_tags WHERE photo_id=? AND cluster_id=0",
                (pid,),
            ).fetchone()[0]
            assert count == 1


# ── Phase 1.2: DELETE /api/faces/tag ──


class TestUntagPerson:
    def test_untag_missing_params(self, client):
        resp = client.delete("/api/v1/faces/tag", json={}, content_type="application/json")
        assert resp.status_code == 400

    def test_untag_valid(self, client, bp_app):
        pid, th = _get_photo_id_and_hash(bp_app)
        # Tag first
        client.post(
            "/api/v1/faces/tag",
            json={"path_hash": th, "cluster_id": 0},
            content_type="application/json",
        )
        # Untag
        resp = client.delete(
            "/api/v1/faces/tag",
            json={"path_hash": th, "cluster_id": 0},
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "untagged"
        # Row gone
        ctx = _ctx(bp_app)
        with bp_app.app_context():
            conn = ctx.get_conn()
            row = conn.execute(
                "SELECT * FROM photo_person_tags WHERE photo_id=? AND cluster_id=0",
                (pid,),
            ).fetchone()
            assert row is None

    def test_untag_nonexistent_is_noop(self, client, bp_app):
        _pid, th = _get_photo_id_and_hash(bp_app)
        resp = client.delete(
            "/api/v1/faces/tag",
            json={"path_hash": th, "cluster_id": 99},
            content_type="application/json",
        )
        assert resp.status_code == 200


# ── Phase 1.3: POST /api/faces/reassign ──


class TestReassignFace:
    def test_reassign_missing_params(self, client):
        resp = client.post("/api/v1/faces/reassign", json={}, content_type="application/json")
        assert resp.status_code == 400
        assert "required" in resp.get_json()["error"]

    def test_reassign_invalid_cluster_id(self, client):
        resp = client.post(
            "/api/v1/faces/reassign",
            json={"face_id": 1, "cluster_id": -5},
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_reassign_nonexistent_face(self, client):
        resp = client.post(
            "/api/v1/faces/reassign",
            json={"face_id": 999999, "cluster_id": 0},
            content_type="application/json",
        )
        assert resp.status_code == 404

    def test_reassign_valid(self, client, bp_app):
        pid, _th = _get_photo_id_and_hash(bp_app)
        face_id = _insert_face(bp_app, pid, face_index=0, cluster_id=0)
        resp = client.post(
            "/api/v1/faces/reassign",
            json={"face_id": face_id, "cluster_id": 5},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "reassigned"
        assert "albums" in data
        # Verify in DB
        ctx = _ctx(bp_app)
        with bp_app.app_context():
            conn = ctx.get_conn()
            row = conn.execute(
                "SELECT cluster_id FROM face_embeddings WHERE id=?", (face_id,)
            ).fetchone()
            assert row["cluster_id"] == 5

    def test_reassign_to_dismissed(self, client, bp_app):
        pid, _th = _get_photo_id_and_hash(bp_app)
        face_id = _insert_face(bp_app, pid, face_index=0, cluster_id=0)
        resp = client.post(
            "/api/v1/faces/reassign",
            json={"face_id": face_id, "cluster_id": -2},
            content_type="application/json",
        )
        assert resp.status_code == 200
        ctx = _ctx(bp_app)
        with bp_app.app_context():
            conn = ctx.get_conn()
            row = conn.execute(
                "SELECT cluster_id FROM face_embeddings WHERE id=?", (face_id,)
            ).fetchone()
            assert row["cluster_id"] == CLUSTER_DISMISSED

    def test_reassign_does_not_propagate(self, client, bp_app):
        """Reassign must only move the single face — no propagation."""
        ctx = _ctx(bp_app)
        with bp_app.app_context():
            conn = ctx.get_conn()
            photos = conn.execute("SELECT id FROM photos LIMIT 2").fetchall()
            base = np.random.randn(128).astype(np.float32)
            base /= np.linalg.norm(base)

            emb1 = (base + np.random.randn(128) * 0.01).astype(np.float32)
            conn.execute(
                "INSERT INTO face_embeddings"
                " (photo_id, face_index, embedding, cluster_id,"
                " bbox_x, bbox_y, bbox_w, bbox_h)"
                f" VALUES (?, 0, ?, {CLUSTER_UNASSIGNED}, 10, 10, 50, 50)",
                (photos[0][0], emb1.tobytes()),
            )
            face_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

            # Similar unassigned face — must NOT be absorbed
            emb2 = (base + np.random.randn(128) * 0.01).astype(np.float32)
            conn.execute(
                "INSERT INTO face_embeddings"
                " (photo_id, face_index, embedding, cluster_id,"
                " bbox_x, bbox_y, bbox_w, bbox_h)"
                f" VALUES (?, 0, ?, {CLUSTER_UNASSIGNED}, 10, 10, 50, 50)",
                (photos[1][0], emb2.tobytes()),
            )
            similar_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.commit()

        resp = client.post(
            "/api/v1/faces/reassign",
            json={"face_id": face_id, "cluster_id": 0},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "propagated" not in data

        with bp_app.app_context():
            conn = ctx.get_conn()
            row = conn.execute(
                "SELECT cluster_id FROM face_embeddings WHERE id=?",
                (similar_id,),
            ).fetchone()
            assert row["cluster_id"] == CLUSTER_UNASSIGNED


# ── Phase 1.4: POST /api/faces/avatar ──


class TestAvatarOverride:
    def test_avatar_missing_cluster_id(self, client):
        resp = client.post("/api/v1/faces/avatar", json={}, content_type="application/json")
        assert resp.status_code == 400

    def test_avatar_set(self, client, bp_app):
        resp = client.post(
            "/api/v1/faces/avatar",
            json={"cluster_id": 0, "filepath": "/tmp/photo.jpg", "face_index": 1},
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "ok"
        # Verify in DB
        ctx = _ctx(bp_app)
        with bp_app.app_context():
            conn = ctx.get_conn()
            row = conn.execute("SELECT value FROM settings WHERE key='person_avatar_0'").fetchone()
            assert row is not None
            data = json.loads(row["value"])
            assert data["filepath"] == "/tmp/photo.jpg"
            assert data["face_index"] == 1

    def test_avatar_clear(self, client, bp_app):
        # Set first
        client.post(
            "/api/v1/faces/avatar",
            json={"cluster_id": 0, "filepath": "/tmp/photo.jpg", "face_index": 1},
            content_type="application/json",
        )
        # Clear (null filepath/face_index)
        resp = client.post(
            "/api/v1/faces/avatar",
            json={"cluster_id": 0},
            content_type="application/json",
        )
        assert resp.status_code == 200
        ctx = _ctx(bp_app)
        with bp_app.app_context():
            conn = ctx.get_conn()
            row = conn.execute("SELECT value FROM settings WHERE key='person_avatar_0'").fetchone()
            assert row is None


# ── Phase 1.5: GET /api/faces/cluster/<id> ──


class TestClusterDetail:
    def test_cluster_empty(self, client):
        resp = client.get("/api/v1/faces/cluster/999")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["faces"] == []
        assert data["total"] == 0

    def test_cluster_with_faces(self, client, bp_app):
        pid, _th = _get_photo_id_and_hash(bp_app)
        _insert_face(bp_app, pid, face_index=0, cluster_id=3)
        resp = client.get("/api/v1/faces/cluster/3")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] == 1
        assert len(data["faces"]) == 1
        assert "filepath" in data["faces"][0]
        assert "face_index" in data["faces"][0]
        assert "thumb_hash" in data["faces"][0]

    def test_cluster_sampling(self, client, bp_app):
        """When more faces than limit, sample evenly."""
        ctx = _ctx(bp_app)
        with bp_app.app_context():
            conn = ctx.get_conn()
            photos = conn.execute("SELECT id FROM photos").fetchall()
            emb = np.random.randn(128).astype(np.float32).tobytes()
            for _i, row in enumerate(photos):
                conn.execute(
                    "INSERT INTO face_embeddings"
                    " (photo_id, face_index, embedding, cluster_id,"
                    " bbox_x, bbox_y, bbox_w, bbox_h)"
                    " VALUES (?, 0, ?, 7, 10, 10, 50, 50)",
                    (row[0], emb),
                )
            conn.commit()

        resp = client.get("/api/v1/faces/cluster/7?limit=3")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] == len(photos)
        assert len(data["faces"]) == 3


# ── Phase 1.6: GET /api/faces/photo/<hash> ──


class TestFacesForPhoto:
    def test_unknown_hash(self, client):
        resp = client.get("/api/v1/faces/photo/deadbeef00000000")
        assert resp.status_code == 404

    def test_photo_no_faces(self, client, bp_app):
        _pid, th = _get_photo_id_and_hash(bp_app)
        resp = client.get(f"/api/v1/faces/photo/{th}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["faces"] == []

    def test_photo_with_faces(self, client, bp_app):
        pid, th = _get_photo_id_and_hash(bp_app)
        _insert_face(bp_app, pid, face_index=0, cluster_id=0)
        resp = client.get(f"/api/v1/faces/photo/{th}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["faces"]) == 1
        f = data["faces"][0]
        assert "face_id" in f
        assert "face_index" in f
        assert "cluster_id" in f
        assert "bbox_w" in f
        assert "bbox_h" in f

    def test_photo_with_person_tags(self, client, bp_app):
        """Manual person tags should appear in the response."""
        _pid, th = _get_photo_id_and_hash(bp_app)
        # Tag a person
        client.post(
            "/api/v1/faces/tag",
            json={"path_hash": th, "cluster_id": 0},
            content_type="application/json",
        )
        resp = client.get(f"/api/v1/faces/photo/{th}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "person_tags" in data
        assert len(data["person_tags"]) == 1
        assert data["person_tags"][0]["cluster_id"] == 0


# ── Phase 2: Merge must NOT propagate ──
# Propagation was removed after it caused cascading merges that collapsed
# 90+ clusters into 4 mega-clusters during face review.


class TestMergeNoPropagation:
    def test_merge_does_not_absorb_unassigned(self, client, bp_app):
        """After merge, similar unassigned faces must stay unassigned."""
        ctx = _ctx(bp_app)
        with bp_app.app_context():
            conn = ctx.get_conn()
            photos = conn.execute("SELECT id FROM photos LIMIT 4").fetchall()
            base = np.random.randn(128).astype(np.float32)
            base /= np.linalg.norm(base)

            for i in range(2):
                emb = (base + np.random.randn(128) * 0.01).astype(np.float32)
                conn.execute(
                    "INSERT INTO face_embeddings"
                    " (photo_id, face_index, embedding, cluster_id,"
                    " bbox_x, bbox_y, bbox_w, bbox_h)"
                    " VALUES (?, 0, ?, 0, 10, 10, 50, 50)",
                    (photos[i][0], emb.tobytes()),
                )
            emb = (base + np.random.randn(128) * 0.01).astype(np.float32)
            conn.execute(
                "INSERT INTO face_embeddings"
                " (photo_id, face_index, embedding, cluster_id,"
                " bbox_x, bbox_y, bbox_w, bbox_h)"
                " VALUES (?, 0, ?, 1, 10, 10, 50, 50)",
                (photos[2][0], emb.tobytes()),
            )
            # Unassigned face similar to cluster 0 — must NOT be absorbed
            emb = (base + np.random.randn(128) * 0.01).astype(np.float32)
            conn.execute(
                "INSERT INTO face_embeddings"
                " (photo_id, face_index, embedding, cluster_id,"
                " bbox_x, bbox_y, bbox_w, bbox_h)"
                f" VALUES (?, 0, ?, {CLUSTER_UNASSIGNED}, 10, 10, 50, 50)",
                (photos[3][0], emb.tobytes()),
            )
            conn.commit()

        resp = client.post(
            "/api/v1/faces/merge",
            json={"primary_cluster_id": 0, "merge_cluster_ids": [1]},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "propagated" not in data

        # Verify the unassigned face is still unassigned
        with bp_app.app_context():
            conn = ctx.get_conn()
            row = conn.execute(
                "SELECT cluster_id FROM face_embeddings WHERE photo_id=?",
                (photos[3][0],),
            ).fetchone()
            assert row["cluster_id"] == CLUSTER_UNASSIGNED


# ── Phase 3: Silent error fixes ──


class TestBboxValidation:
    """Bbox values stored in DB should be non-negative."""

    def test_negative_bbox_clamped(self, bp_app):
        """Faces with negative bbox should be clamped to 0 at insertion."""
        from bpp.web.face_worker import _validate_bbox

        # Negative values clamped to 0
        assert _validate_bbox(-5, -10, 50, 60) == (0, 0, 50, 60)
        # Normal values unchanged
        assert _validate_bbox(10, 20, 50, 60) == (10, 20, 50, 60)
        # Zero-size bbox returns None (invalid)
        assert _validate_bbox(10, 20, 0, 60) is None
        assert _validate_bbox(10, 20, 50, 0) is None

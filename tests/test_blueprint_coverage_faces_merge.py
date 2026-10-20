"""Comprehensive Flask blueprint route tests for maximum coverage — part 3 of 5: TestFacesMerge,
TestFacesSplit, TestMergeDoesNotPropagate."""

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

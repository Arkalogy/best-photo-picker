"""Tests for new face endpoints: tag, untag, reassign, avatar, cluster detail, photo faces.

TDD: written red-first against bp_faces.py endpoints added Feb 2026.
"""

from __future__ import annotations

import json
import os

import numpy as np
import pytest

from bpp.constants import CLUSTER_DISMISSED, CLUSTER_UNASSIGNED
from bpp.web.app import create_app


def _make_analysis(n: int = 10) -> list[dict]:
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


@pytest.fixture
def bp_app(tmp_path):
    workdir = str(tmp_path / "workdir")
    os.makedirs(workdir)
    analysis = _make_analysis(10)
    with open(os.path.join(workdir, "analysis.json"), "w") as f:
        json.dump(analysis, f)
    app = create_app(workdir=workdir)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(bp_app):
    return bp_app.test_client()


def _ctx(bp_app):
    return bp_app.extensions["bpp"]


def _get_photo_id_and_hash(bp_app):
    """Return (photo_id, thumb_hash) for the first photo."""
    ctx = _ctx(bp_app)
    with bp_app.app_context():
        conn = ctx.get_conn()
        row = conn.execute("SELECT id, filepath FROM photos LIMIT 1").fetchone()
        pid = row["id"]
        fp = row["filepath"]
        th = ctx.thumbs.get_hash(fp) if ctx.thumbs else None
        return pid, th


def _insert_face(bp_app, photo_id, face_index=0, cluster_id=0):
    """Insert a face embedding and return its id."""
    ctx = _ctx(bp_app)
    with bp_app.app_context():
        conn = ctx.get_conn()
        emb = np.random.randn(128).astype(np.float32).tobytes()
        conn.execute(
            "INSERT INTO face_embeddings"
            " (photo_id, face_index, embedding, cluster_id,"
            " bbox_x, bbox_y, bbox_w, bbox_h)"
            " VALUES (?, ?, ?, ?, 10, 20, 50, 60)",
            (photo_id, face_index, emb, cluster_id),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id FROM face_embeddings WHERE photo_id=? AND face_index=?",
            (photo_id, face_index),
        ).fetchone()
        return row["id"]


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


class TestEmbeddingValidation:
    """Embedding shape/type should be validated before DB insert."""

    def test_valid_embedding(self):
        from bpp.web.face_worker import _validate_embedding

        emb = np.random.randn(128).astype(np.float32)
        assert _validate_embedding(emb) is True

    def test_wrong_shape_rejected(self):
        from bpp.web.face_worker import _validate_embedding

        emb = np.random.randn(64).astype(np.float32)
        assert _validate_embedding(emb) is False

    def test_nan_rejected(self):
        from bpp.web.face_worker import _validate_embedding

        emb = np.full(128, np.nan, dtype=np.float32)
        assert _validate_embedding(emb) is False

    def test_inf_rejected(self):
        from bpp.web.face_worker import _validate_embedding

        emb = np.full(128, np.inf, dtype=np.float32)
        assert _validate_embedding(emb) is False

    def test_zero_vector_rejected(self):
        """L3: zero-magnitude embeddings poison clustering — every distance
        collapses to the centroid norm, so threshold-based clustering snaps
        the zero face onto whatever cluster it sees first."""
        from bpp.web.face_worker import _validate_embedding

        emb = np.zeros(128, dtype=np.float32)
        assert _validate_embedding(emb) is False

    def test_near_zero_vector_rejected(self):
        from bpp.web.face_worker import _validate_embedding

        emb = np.full(128, 1e-9, dtype=np.float32)
        assert _validate_embedding(emb) is False

    def test_small_but_real_magnitude_accepted(self):
        from bpp.web.face_worker import _validate_embedding

        # Norm ≈ 1.0 (16 dims at 0.25 ≈ norm 1.0)
        emb = np.full(128, 0.0884, dtype=np.float32)
        assert _validate_embedding(emb) is True


class TestStaleAvatarFallback:
    """Avatar override pointing to deleted file should fall back gracefully."""

    def test_stale_avatar_ignored(self, client, bp_app):
        ctx = _ctx(bp_app)
        with bp_app.app_context():
            conn = ctx.get_conn()
            pid = conn.execute("SELECT id FROM photos LIMIT 1").fetchone()["id"]
            emb = np.random.randn(128).astype(np.float32).tobytes()
            conn.execute(
                "INSERT INTO face_embeddings"
                " (photo_id, face_index, embedding, cluster_id,"
                " bbox_x, bbox_y, bbox_w, bbox_h)"
                " VALUES (?, 0, ?, 0, 10, 10, 50, 50)",
                (pid, emb),
            )
            # Set avatar to non-existent file
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (
                    "person_avatar_0",
                    json.dumps({"filepath": "/nonexistent/photo.jpg", "face_index": 0}),
                ),
            )
            conn.commit()

        resp = client.get("/api/v1/faces/clusters")
        assert resp.status_code == 200
        clusters = resp.get_json()["clusters"]
        # Should still return clusters (not crash), avatar falls back
        assert len(clusters) >= 1
        # The representative should use the override filepath even if stale
        # (crop generation will fail later, but cluster listing shouldn't crash)
        rep = clusters[0]["representative"]
        assert "filepath" in rep


# ── Phase 4: Face worker logic tests ──


class TestLoadFaceClustersWithTags:
    """load_face_clusters() should include manually tagged photos."""

    def test_manual_tags_in_filepaths(self, bp_app):
        from bpp.web.face_worker import load_face_clusters

        ctx = _ctx(bp_app)
        with bp_app.app_context():
            conn = ctx.get_conn()
            photos = conn.execute("SELECT id, filepath FROM photos LIMIT 3").fetchall()
            emb = np.random.randn(128).astype(np.float32).tobytes()
            # One face embedding in cluster 0 for photo 0
            conn.execute(
                "INSERT INTO face_embeddings"
                " (photo_id, face_index, embedding, cluster_id,"
                " bbox_x, bbox_y, bbox_w, bbox_h)"
                " VALUES (?, 0, ?, 0, 10, 10, 50, 50)",
                (photos[0]["id"], emb),
            )
            # Manually tag photo 1 as cluster 0
            conn.execute(
                "INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (?, 0)",
                (photos[1]["id"],),
            )
            conn.commit()

            clusters = load_face_clusters(conn)
            cluster_0 = [c for c in clusters if c["cluster_id"] == 0]
            assert len(cluster_0) == 1
            fps = cluster_0[0]["filepaths"]
            # Both the embedded photo and the tagged photo should be in filepaths
            assert photos[0]["filepath"] in fps
            assert photos[1]["filepath"] in fps

    def test_tag_only_cluster(self, bp_app):
        """A cluster with only manual tags (no embeddings) should NOT appear
        in load_face_clusters since it only returns embedding-based clusters."""
        from bpp.web.face_worker import load_face_clusters

        ctx = _ctx(bp_app)
        with bp_app.app_context():
            conn = ctx.get_conn()
            pid = conn.execute("SELECT id FROM photos LIMIT 1").fetchone()["id"]
            conn.execute(
                "INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (?, 99)",
                (pid,),
            )
            conn.commit()

            clusters = load_face_clusters(conn)
            cluster_99 = [c for c in clusters if c["cluster_id"] == 99]
            # Not in load_face_clusters (it needs embeddings to compute representative)
            assert len(cluster_99) == 0


class TestSmartAlbumPersonTags:
    """Smart person albums should include manually tagged photos."""

    def test_person_album_includes_tagged_photos(self, bp_app):
        from bpp.db.smart_albums import (
            get_smart_album_photo_ids,
            refresh_smart_albums,
        )

        ctx = _ctx(bp_app)
        with bp_app.app_context():
            conn = ctx.get_conn()
            photos = conn.execute("SELECT id, filepath FROM photos LIMIT 3").fetchall()
            emb = np.random.randn(128).astype(np.float32).tobytes()
            # Embedding in cluster 0 for photo 0
            conn.execute(
                "INSERT INTO face_embeddings"
                " (photo_id, face_index, embedding, cluster_id,"
                " bbox_x, bbox_y, bbox_w, bbox_h)"
                " VALUES (?, 0, ?, 0, 10, 10, 50, 50)",
                (photos[0]["id"], emb),
            )
            # Tag photo 1 as cluster 0
            conn.execute(
                "INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (?, 0)",
                (photos[1]["id"],),
            )
            conn.commit()

            refresh_smart_albums(conn)

            # Find the smart_person album for cluster 0
            album = conn.execute(
                "SELECT * FROM albums WHERE album_type='smart_person'"
                " AND json_extract(rule_json, '$.cluster_id')=0"
            ).fetchone()
            assert album is not None

            photo_ids = get_smart_album_photo_ids(conn, dict(album))
            assert photos[0]["id"] in photo_ids
            assert photos[1]["id"] in photo_ids

    def test_tag_only_creates_person_album(self, bp_app):
        """A cluster with only manual tags should still get a person album."""
        from bpp.db.smart_albums import refresh_smart_albums

        ctx = _ctx(bp_app)
        with bp_app.app_context():
            conn = ctx.get_conn()
            pid = conn.execute("SELECT id FROM photos LIMIT 1").fetchone()["id"]
            conn.execute(
                "INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (?, 42)",
                (pid,),
            )
            conn.commit()

            refresh_smart_albums(conn)

            album = conn.execute(
                "SELECT * FROM albums WHERE album_type='smart_person'"
                " AND json_extract(rule_json, '$.cluster_id')=42"
            ).fetchone()
            assert album is not None


class TestGenerateFaceCrop:
    """Test generate_face_crop with real images."""

    def test_valid_crop(self, tmp_path):
        from PIL import Image

        from bpp.web.face_worker import generate_face_crop

        # Create a test image
        img = Image.new("RGB", (200, 200), color="red")
        img_path = str(tmp_path / "test.jpg")
        img.save(img_path)

        crop_dir = str(tmp_path / "crops")
        os.makedirs(crop_dir)

        result = generate_face_crop(
            filepath=img_path,
            bbox=(50, 50, 80, 80),
            crop_dir=crop_dir,
            path_hash="testhash",
            face_index=0,
            max_long_side=1024,
        )
        assert result is not None
        assert os.path.exists(result)

    def test_invalid_bbox_returns_none(self, tmp_path):
        from PIL import Image

        from bpp.web.face_worker import generate_face_crop

        img = Image.new("RGB", (200, 200), color="blue")
        img_path = str(tmp_path / "test.jpg")
        img.save(img_path)

        crop_dir = str(tmp_path / "crops")
        os.makedirs(crop_dir)

        # Zero-size bbox
        result = generate_face_crop(
            filepath=img_path,
            bbox=(0, 0, 0, 0),
            crop_dir=crop_dir,
            path_hash="testhash",
            face_index=0,
        )
        assert result is None

    def test_nonexistent_file_returns_none(self, tmp_path):
        from bpp.web.face_worker import generate_face_crop

        crop_dir = str(tmp_path / "crops")
        os.makedirs(crop_dir)

        result = generate_face_crop(
            filepath="/nonexistent/photo.jpg",
            bbox=(10, 10, 50, 50),
            crop_dir=crop_dir,
            path_hash="testhash",
            face_index=0,
        )
        assert result is None

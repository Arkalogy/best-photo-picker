"""End-to-end tests for the face review flow.

Covers: GET /api/faces/review (suggestions), merge via review,
dismiss via review, skip, hard-negative gating during restore,
and the full review → merge → verify cycle.
"""

from __future__ import annotations

import json
import os

import numpy as np
import pytest

from bpp.web.app import create_app


def _make_analysis(n: int = 5) -> list[dict]:
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
                "exposure_score": 0.5,
                "face_score": 0.5,
                "face_count": 1,
                "largest_face_ratio": 0.05,
                "face_center_dist": 0.3,
                "composition_score": 0.5,
                "aggregate_score": 0.5,
            }
        )
    return items


def _get_ctx(app):
    return app.extensions["bpp"]


def _make_embedding(direction: int, dim: int = 128) -> bytes:
    """Create a unit embedding pointing in a given axis direction."""
    emb = np.zeros(dim, dtype=np.float32)
    emb[direction % dim] = 1.0
    return emb.tobytes()


def _make_similar_embedding(direction: int, offset: float = 0.1, dim: int = 128) -> bytes:
    """Create an embedding close to a direction (for suggested match testing)."""
    emb = np.zeros(dim, dtype=np.float32)
    emb[direction % dim] = 1.0
    emb[(direction + 1) % dim] = offset
    # Normalize
    emb = emb / np.linalg.norm(emb)
    return emb.tobytes()


@pytest.fixture
def app(tmp_path):
    workdir = str(tmp_path / "workdir")
    os.makedirs(workdir)
    analysis = _make_analysis(5)
    with open(os.path.join(workdir, "analysis.json"), "w") as f:
        json.dump(analysis, f)
    app = create_app(workdir=workdir)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def _seed_clusters(app, named: dict[int, str], unnamed: list[int]):
    """Seed face embeddings and smart_person albums.

    named: {cluster_id: "Person Name"} — these get a smart_person album with a custom name.
    unnamed: [cluster_id, ...] — these get embeddings but no named album.
    """
    ctx = _get_ctx(app)
    with app.app_context():
        conn = ctx.get_conn()
        pid = conn.execute("SELECT id FROM photos LIMIT 1").fetchone()[0]

        fi = 100
        for cid, name in named.items():
            emb = _make_embedding(cid)
            conn.execute(
                "INSERT OR IGNORE INTO face_embeddings"
                " (photo_id, face_index, embedding, cluster_id,"
                " bbox_x, bbox_y, bbox_w, bbox_h)"
                " VALUES (?, ?, ?, ?, 10, 10, 50, 50)",
                (pid, fi, emb, cid),
            )
            fi += 1
            # Create smart_person album with user name
            rule = json.dumps({"cluster_id": cid})
            conn.execute(
                "INSERT OR IGNORE INTO albums (name, album_type, rule_json)"
                " VALUES (?, 'smart_person', ?)",
                (name, rule),
            )

        for cid in unnamed:
            emb = _make_embedding(cid)
            conn.execute(
                "INSERT OR IGNORE INTO face_embeddings"
                " (photo_id, face_index, embedding, cluster_id,"
                " bbox_x, bbox_y, bbox_w, bbox_h)"
                " VALUES (?, ?, ?, ?, 10, 10, 50, 50)",
                (pid, fi, emb, cid),
            )
            fi += 1
            # Default "Person N" album (counts as unreviewed)
            rule = json.dumps({"cluster_id": cid})
            conn.execute(
                "INSERT OR IGNORE INTO albums (name, album_type, rule_json)"
                " VALUES (?, 'smart_person', ?)",
                (f"Person {cid}", rule),
            )

        conn.commit()


class TestFaceReviewEndpoint:
    """GET /api/faces/review — returns unreviewed clusters with suggestions."""

    def test_no_clusters_returns_empty(self, client):
        resp = client.get("/api/v1/faces/review")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["unreviewed"] == []
        assert data["total"] == 0
        assert data["reviewed"] == 0

    def test_all_named_returns_zero_unreviewed(self, client, app):
        _seed_clusters(app, named={10: "Alice", 11: "Bob"}, unnamed=[])
        resp = client.get("/api/v1/faces/review")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["reviewed"] == 2
        assert len(data["unreviewed"]) == 0

    def test_unnamed_clusters_appear_as_unreviewed(self, client, app):
        _seed_clusters(app, named={10: "Alice"}, unnamed=[20, 21])
        resp = client.get("/api/v1/faces/review")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] == 3
        assert data["reviewed"] == 1
        assert len(data["unreviewed"]) == 2
        unrev_cids = {c["cluster_id"] for c in data["unreviewed"]}
        assert unrev_cids == {20, 21}

    def test_suggested_match_returned_when_close(self, client, app):
        """Unnamed cluster close to a named one gets a suggested_match."""
        ctx = _get_ctx(app)
        with app.app_context():
            conn = ctx.get_conn()
            pid = conn.execute("SELECT id FROM photos LIMIT 1").fetchone()[0]

            # Named cluster 10 = "Alice" with embedding [1, 0, 0, ...]
            emb_named = _make_embedding(0)
            conn.execute(
                "INSERT OR IGNORE INTO face_embeddings"
                " (photo_id, face_index, embedding, cluster_id,"
                " bbox_x, bbox_y, bbox_w, bbox_h)"
                " VALUES (?, 200, ?, 10, 10, 10, 50, 50)",
                (pid, emb_named),
            )
            rule = json.dumps({"cluster_id": 10})
            conn.execute(
                "INSERT OR IGNORE INTO albums (name, album_type, rule_json)"
                " VALUES ('Alice', 'smart_person', ?)",
                (rule,),
            )

            # Unnamed cluster 20 with similar embedding (close to Alice)
            emb_similar = _make_similar_embedding(0, offset=0.05)
            conn.execute(
                "INSERT OR IGNORE INTO face_embeddings"
                " (photo_id, face_index, embedding, cluster_id,"
                " bbox_x, bbox_y, bbox_w, bbox_h)"
                " VALUES (?, 201, ?, 20, 10, 10, 50, 50)",
                (pid, emb_similar),
            )
            rule2 = json.dumps({"cluster_id": 20})
            conn.execute(
                "INSERT OR IGNORE INTO albums (name, album_type, rule_json)"
                " VALUES ('Person 20', 'smart_person', ?)",
                (rule2,),
            )
            conn.commit()

        resp = client.get("/api/v1/faces/review")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["unreviewed"]) == 1
        suggestion = data["unreviewed"][0]["suggested_match"]
        assert suggestion is not None
        assert suggestion["cluster_id"] == 10
        assert suggestion["name"] == "Alice"
        assert suggestion["distance"] < 0.66  # within suggest_threshold

    def test_low_confidence_when_far(self, client, app):
        """Unnamed cluster far from all named ones gets low confidence suggestion."""
        _seed_clusters(app, named={10: "Alice"}, unnamed=[20])
        # Cluster 10 embedding direction 10, cluster 20 direction 20 — orthogonal
        resp = client.get("/api/v1/faces/review")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["unreviewed"]) == 1
        suggestion = data["unreviewed"][0]["suggested_match"]
        assert suggestion is not None
        # Orthogonal embeddings → distance ~1.41 → low confidence
        assert suggestion["distance"] > 1.0
        assert suggestion["confidence"] < 50


class TestFaceReviewMergeFlow:
    """Full review → merge → verify cycle."""

    def test_merge_reduces_unreviewed_count(self, client, app):
        """Merging an unnamed cluster into a named one removes it from review."""
        _seed_clusters(app, named={10: "Alice"}, unnamed=[20])

        # Verify 1 unreviewed
        resp = client.get("/api/v1/faces/review")
        assert len(resp.get_json()["unreviewed"]) == 1

        # Merge cluster 20 into 10
        resp = client.post(
            "/api/v1/faces/merge",
            json={"primary_cluster_id": 10, "merge_cluster_ids": [20]},
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "merged"

        # Verify 0 unreviewed now
        resp = client.get("/api/v1/faces/review")
        data = resp.get_json()
        assert len(data["unreviewed"]) == 0
        assert data["reviewed"] == 1


class TestFaceReviewDismissFlow:
    """Review → dismiss → verify cycle."""

    def test_dismiss_removes_from_review(self, client, app):
        _seed_clusters(app, named={10: "Alice"}, unnamed=[20, 21])

        # Verify 2 unreviewed
        resp = client.get("/api/v1/faces/review")
        assert len(resp.get_json()["unreviewed"]) == 2

        # Dismiss cluster 20
        resp = client.post(
            "/api/v1/faces/dismiss",
            json={"cluster_id": 20},
            content_type="application/json",
        )
        assert resp.status_code == 200

        # Verify 1 unreviewed now (cluster 21 remains)
        resp = client.get("/api/v1/faces/review")
        data = resp.get_json()
        assert len(data["unreviewed"]) == 1
        assert data["unreviewed"][0]["cluster_id"] == 21


class TestFaceRestoreHardNegative:
    """Restore respects hard negatives — does not merge confusable faces."""

    def test_restore_avoids_hard_negative_cluster(self, client, app):
        ctx = _get_ctx(app)
        with app.app_context():
            conn = ctx.get_conn()
            pid = conn.execute("SELECT id FROM photos LIMIT 1").fetchone()[0]

            # Two named clusters with distinct embeddings
            emb_a = np.zeros(128, dtype=np.float32)
            emb_a[0] = 1.0
            emb_b = np.zeros(128, dtype=np.float32)
            emb_b[1] = 1.0

            conn.execute(
                "INSERT OR IGNORE INTO face_embeddings"
                " (photo_id, face_index, embedding, cluster_id,"
                " bbox_x, bbox_y, bbox_w, bbox_h)"
                " VALUES (?, 50, ?, 10, 10, 10, 50, 50)",
                (pid, emb_a.tobytes()),
            )
            conn.execute(
                "INSERT OR IGNORE INTO face_embeddings"
                " (photo_id, face_index, embedding, cluster_id,"
                " bbox_x, bbox_y, bbox_w, bbox_h)"
                " VALUES (?, 51, ?, 11, 10, 10, 50, 50)",
                (pid, emb_b.tobytes()),
            )

            # Dismissed face equidistant from both
            emb_mid = np.zeros(128, dtype=np.float32)
            emb_mid[0] = 0.5
            emb_mid[1] = 0.5
            from bpp.constants import CLUSTER_DISMISSED

            conn.execute(
                "INSERT OR IGNORE INTO face_embeddings"
                " (photo_id, face_index, embedding, cluster_id,"
                " bbox_x, bbox_y, bbox_w, bbox_h)"
                " VALUES (?, 52, ?, ?, 10, 10, 50, 50)",
                (pid, emb_mid.tobytes(), CLUSTER_DISMISSED),
            )
            conn.commit()

            mid_id = conn.execute(
                "SELECT id FROM face_embeddings WHERE face_index = 52 AND photo_id = ?",
                (pid,),
            ).fetchone()[0]

            # Record hard negative
            from bpp.db.face_feedback import store_hard_negative

            store_hard_negative(conn, 10, 11)

        # Restore
        resp = client.post(
            "/api/v1/faces/restore",
            json={"face_ids": [mid_id]},
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.get_json()["count"] == 1

        # Verify NOT assigned to either confusable cluster
        with app.app_context():
            conn = ctx.get_conn()
            row = conn.execute(
                "SELECT cluster_id FROM face_embeddings WHERE id = ?", (mid_id,)
            ).fetchone()
            assert row[0] not in (10, 11, CLUSTER_DISMISSED)
            assert row[0] >= 0

    def test_restore_assigns_to_cluster_without_hard_negative(self, client, app):
        """Without hard negatives, restore assigns to nearest cluster normally."""
        ctx = _get_ctx(app)
        with app.app_context():
            conn = ctx.get_conn()
            pid = conn.execute("SELECT id FROM photos LIMIT 1").fetchone()[0]

            # One named cluster
            emb_a = np.zeros(128, dtype=np.float32)
            emb_a[0] = 1.0
            conn.execute(
                "INSERT OR IGNORE INTO face_embeddings"
                " (photo_id, face_index, embedding, cluster_id,"
                " bbox_x, bbox_y, bbox_w, bbox_h)"
                " VALUES (?, 60, ?, 10, 10, 10, 50, 50)",
                (pid, emb_a.tobytes()),
            )

            # Dismissed face very close to cluster 10
            emb_close = np.zeros(128, dtype=np.float32)
            emb_close[0] = 0.95
            emb_close[1] = 0.05
            from bpp.constants import CLUSTER_DISMISSED

            conn.execute(
                "INSERT OR IGNORE INTO face_embeddings"
                " (photo_id, face_index, embedding, cluster_id,"
                " bbox_x, bbox_y, bbox_w, bbox_h)"
                " VALUES (?, 61, ?, ?, 10, 10, 50, 50)",
                (pid, emb_close.tobytes(), CLUSTER_DISMISSED),
            )
            conn.commit()

            close_id = conn.execute(
                "SELECT id FROM face_embeddings WHERE face_index = 61 AND photo_id = ?",
                (pid,),
            ).fetchone()[0]

        # Restore — no hard negatives, should assign to cluster 10
        resp = client.post(
            "/api/v1/faces/restore",
            json={"face_ids": [close_id]},
            content_type="application/json",
        )
        assert resp.status_code == 200

        with app.app_context():
            conn = ctx.get_conn()
            row = conn.execute(
                "SELECT cluster_id FROM face_embeddings WHERE id = ?", (close_id,)
            ).fetchone()
            assert row[0] == 10, f"Expected cluster 10, got {row[0]}"

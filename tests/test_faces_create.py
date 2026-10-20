"""Tests for POST /api/v1/faces/create — create a face_embeddings row — part 1 of 2."""

# Shared fixtures are imported by name and re-bound as test parameters.
# ruff: noqa: F811

from __future__ import annotations

import numpy as np

from bpp.constants import CLUSTER_UNASSIGNED
from tests._faces_create_shared import (  # noqa: F401
    _ctx,
    _make_analysis,
    _path_hash_for,
    _seed_cluster,
    _stub_pipeline,
    bp_app,
    client,
)


class TestInputValidation:
    def test_missing_path_hash_returns_400(self, client):
        resp = client.post(
            "/api/v1/faces/create",
            json={"cluster_id": 0, "bbox_pct": {"x": 10, "y": 10, "w": 20, "h": 20}},
        )
        assert resp.status_code == 400
        assert "path_hash" in resp.get_json()["error"]

    def test_missing_cluster_id_returns_400(self, client, bp_app):
        ph = _path_hash_for(bp_app)
        resp = client.post(
            "/api/v1/faces/create",
            json={"path_hash": ph, "bbox_pct": {"x": 10, "y": 10, "w": 20, "h": 20}},
        )
        assert resp.status_code == 400

    def test_negative_cluster_id_returns_400(self, client, bp_app):
        ph = _path_hash_for(bp_app)
        resp = client.post(
            "/api/v1/faces/create",
            json={
                "path_hash": ph,
                "cluster_id": -1,
                "bbox_pct": {"x": 10, "y": 10, "w": 20, "h": 20},
            },
        )
        assert resp.status_code == 400

    def test_overflow_bbox_returns_400(self, client, bp_app):
        ph = _path_hash_for(bp_app)
        _seed_cluster(bp_app, 0, 0)
        resp = client.post(
            "/api/v1/faces/create",
            json={
                "path_hash": ph,
                "cluster_id": 0,
                "bbox_pct": {"x": 50, "y": 50, "w": 60, "h": 20},
            },
        )
        assert resp.status_code == 400

    def test_unknown_cluster_id_returns_400(self, client, bp_app):
        """A cluster that doesn't exist anywhere in face_embeddings is
        rejected — we won't create a phantom person."""
        ph = _path_hash_for(bp_app)
        resp = client.post(
            "/api/v1/faces/create",
            json={
                "path_hash": ph,
                "cluster_id": 99999,
                "bbox_pct": {"x": 10, "y": 10, "w": 20, "h": 20},
            },
        )
        assert resp.status_code == 400
        assert "Unknown cluster_id" in resp.get_json()["error"]


# ── Lookup / extraction failures ──────────────────────────────────


def test_unknown_path_hash_returns_404(client, bp_app):
    _seed_cluster(bp_app, 0, 0)
    resp = client.post(
        "/api/v1/faces/create",
        json={
            "path_hash": "deadbeef" * 8,
            "cluster_id": 0,
            "bbox_pct": {"x": 10, "y": 10, "w": 20, "h": 20},
        },
    )
    assert resp.status_code == 404


def test_no_face_in_region_returns_422(client, bp_app, monkeypatch):
    """Regression: refuse the create when YuNet finds no face in the
    drawn region. Same rule as update-bbox — synthesizing landmarks
    would let users tag the brick wall as Noah."""
    ph = _path_hash_for(bp_app)
    _seed_cluster(bp_app, 0, 0)
    monkeypatch.setattr(
        "bpp.scoring.aggregate.load_and_downscale",
        lambda *_a, **_kw: np.zeros((1024, 1024, 3), dtype=np.uint8),
    )
    monkeypatch.setattr(
        "bpp.scoring.face_embed.extract_embedding_for_region",
        lambda *_a, **_kw: None,
    )
    resp = client.post(
        "/api/v1/faces/create",
        json={
            "path_hash": ph,
            "cluster_id": 0,
            "bbox_pct": {"x": 10, "y": 10, "w": 20, "h": 20},
        },
    )
    assert resp.status_code == 422
    assert "no face" in resp.get_json()["error"].lower()


def test_creates_row_with_cluster(client, bp_app, monkeypatch):
    ctx = _ctx(bp_app)
    ph = _path_hash_for(bp_app, photo_idx=1)
    _seed_cluster(bp_app, 0, 0)

    new_emb = np.ones(128, dtype=np.float32) * 0.2
    _stub_pipeline(monkeypatch, new_emb, bbox=(150, 200, 100, 120), quality=0.66)

    resp = client.post(
        "/api/v1/faces/create",
        json={
            "path_hash": ph,
            "cluster_id": 0,
            "bbox_pct": {"x": 15, "y": 20, "w": 10, "h": 12},
        },
    )
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["status"] == "created"
    assert body["cluster_id"] == 0
    assert body["method"] == "yunet"
    assert body["face_id"] > 0

    with bp_app.app_context():
        row = (
            ctx.get_conn()
            .execute(
                "SELECT bbox_x, bbox_y, bbox_w, bbox_h, cluster_id, quality, embedding "
                "FROM face_embeddings WHERE id=?",
                (body["face_id"],),
            )
            .fetchone()
        )
    assert (row["bbox_x"], row["bbox_y"], row["bbox_w"], row["bbox_h"]) == (
        150,
        200,
        100,
        120,
    )
    assert row["cluster_id"] == 0
    assert abs(row["quality"] - 0.66) < 1e-6
    stored = np.frombuffer(row["embedding"], dtype=np.float32)
    np.testing.assert_allclose(stored, new_emb)


def test_face_index_increments_on_second_face(client, bp_app, monkeypatch):
    """If the photo already has face_index 0 and 1, the new face must
    get face_index 2 — face_index is UNIQUE per (photo_id, face_index)."""
    ctx = _ctx(bp_app)
    ph = _path_hash_for(bp_app, photo_idx=0)
    _seed_cluster(bp_app, 0, 0)  # seeds photo 0 with face_index=0
    # Add another existing detection on photo 0 with face_index=1.
    with bp_app.app_context():
        conn = ctx.get_conn()
        photo_id_row = conn.execute("SELECT id FROM photos ORDER BY id LIMIT 1").fetchone()
        emb = np.random.randn(128).astype(np.float32)
        conn.execute(
            "INSERT INTO face_embeddings"
            " (photo_id, face_index, embedding, cluster_id,"
            "  bbox_x, bbox_y, bbox_w, bbox_h, quality)"
            " VALUES (?, 1, ?, ?, 50, 50, 60, 60, 0.5)",
            (photo_id_row["id"], emb.tobytes(), 0),
        )
        conn.commit()

    _stub_pipeline(monkeypatch, np.ones(128, dtype=np.float32) * 0.3)
    resp = client.post(
        "/api/v1/faces/create",
        json={
            "path_hash": ph,
            "cluster_id": 0,
            "bbox_pct": {"x": 10, "y": 10, "w": 20, "h": 20},
        },
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["face_index"] == 2, "next face_index after [0, 1] should be 2"


def test_response_includes_corrected_bbox_pct(client, bp_app, monkeypatch):
    """The YuNet path may shift the bbox slightly; response should reflect
    the stored bbox, not the user's drop position."""
    ph = _path_hash_for(bp_app, photo_idx=0)
    _seed_cluster(bp_app, 0, 0)
    _stub_pipeline(
        monkeypatch,
        np.random.randn(128),
        bbox=(256, 256, 512, 512),
    )
    resp = client.post(
        "/api/v1/faces/create",
        json={
            "path_hash": ph,
            "cluster_id": 0,
            "bbox_pct": {"x": 30, "y": 30, "w": 40, "h": 40},
        },
    )
    body = resp.get_json()
    assert resp.status_code == 200
    assert abs(body["bbox_pct"]["x"] - 25.0) < 0.1
    assert abs(body["bbox_pct"]["w"] - 50.0) < 0.1


def test_does_not_corrupt_unassigned_sentinel(client, bp_app, monkeypatch):
    """Sanity: never accept cluster_id=CLUSTER_UNASSIGNED. The endpoint
    is for tagging a known person; routing through here with the
    sentinel would silently produce an unassigned face_embedding."""
    ph = _path_hash_for(bp_app)
    resp = client.post(
        "/api/v1/faces/create",
        json={
            "path_hash": ph,
            "cluster_id": CLUSTER_UNASSIGNED,
            "bbox_pct": {"x": 10, "y": 10, "w": 20, "h": 20},
        },
    )
    assert resp.status_code == 400

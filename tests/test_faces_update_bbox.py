"""Tests for POST /api/v1/faces/update-bbox — drag-to-fix face bbox.

Covers input validation, lookup failures, embedding extraction failures,
the happy match path, the no-match path, hard-negative ambiguity, and
DB-state side effects (bbox + embedding rewritten in place).
"""

from __future__ import annotations

import json
import os

import numpy as np
import pytest

from bpp.constants import CLUSTER_UNASSIGNED
from bpp.web.app import create_app


def _make_analysis(n: int = 4) -> list[dict]:
    items = []
    for i in range(n):
        items.append(
            {
                "filepath": f"/tmp/test_photos/img_{i:03d}.jpg",
                "date": f"2024-01-{(i % 28) + 1:02d}T12:00:00",
                "date_day": f"2024-01-{(i % 28) + 1:02d}",
                "date_month": "2024-01",
                "file_size": 1024 * (i + 1),
                "file_mtime": 1700000000.0 + i,
                "blur_raw": 100.0,
                "blur_score": 0.5,
                "exposure_score": 0.6,
                "face_score": 0.4,
                "face_count": 1,
                "largest_face_ratio": 0.05,
                "face_center_dist": 0.3,
                "composition_score": 0.5,
                "aggregate_score": 0.5,
            }
        )
    return items


@pytest.fixture
def bp_app(tmp_path):
    workdir = str(tmp_path / "workdir")
    os.makedirs(workdir)
    analysis = _make_analysis(4)
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


def _insert_face(bp_app, photo_idx, *, face_index=0, cluster_id, embedding=None):
    """Insert a face row with a known embedding for cluster setup."""
    ctx = _ctx(bp_app)
    with bp_app.app_context():
        conn = ctx.get_conn()
        photo_row = conn.execute(
            "SELECT id FROM photos ORDER BY id LIMIT ? OFFSET ?",
            (1, photo_idx),
        ).fetchone()
        if embedding is None:
            embedding = np.random.randn(128).astype(np.float32)
        conn.execute(
            "INSERT INTO face_embeddings"
            " (photo_id, face_index, embedding, cluster_id,"
            "  bbox_x, bbox_y, bbox_w, bbox_h, quality)"
            " VALUES (?, ?, ?, ?, 100, 100, 200, 200, 0.7)",
            (photo_row["id"], face_index, embedding.tobytes(), cluster_id),
        )
        conn.commit()
        return conn.execute(
            "SELECT id FROM face_embeddings WHERE photo_id=? AND face_index=?",
            (photo_row["id"], face_index),
        ).fetchone()["id"]


def _stub_pipeline(monkeypatch, embedding, *, method="yunet", quality=0.8, bbox=None):
    """Replace image loading + embedding extraction with deterministic stubs.

    Photo files don't exist on disk in tests, so we have to patch both
    load_and_downscale (image loader) and extract_embedding_for_region
    (the real one needs an SFace ONNX model).
    """
    fake_image = np.zeros((1024, 1024, 3), dtype=np.uint8)

    def fake_load(_filepath, _max_long_side):
        return fake_image

    def fake_extract(_image, _bbox, **_kw):
        return {
            "bbox": bbox or (50, 50, 200, 200),
            "embedding": np.asarray(embedding, dtype=np.float32),
            "quality": quality,
            "method": method,
        }

    monkeypatch.setattr("bpp.scoring.aggregate.load_and_downscale", fake_load)
    monkeypatch.setattr(
        "bpp.scoring.face_embed.extract_embedding_for_region",
        fake_extract,
    )


# ── Input validation ──────────────────────────────────────────────


class TestInputValidation:
    def test_missing_face_id_returns_400(self, client):
        resp = client.post(
            "/api/v1/faces/update-bbox",
            json={"bbox_pct": {"x": 10, "y": 10, "w": 20, "h": 20}},
        )
        assert resp.status_code == 400
        assert "face_id" in resp.get_json()["error"]

    def test_non_integer_face_id_returns_400(self, client):
        resp = client.post(
            "/api/v1/faces/update-bbox",
            json={
                "face_id": "abc",
                "bbox_pct": {"x": 10, "y": 10, "w": 20, "h": 20},
            },
        )
        assert resp.status_code == 400

    def test_missing_bbox_pct_returns_400(self, client):
        resp = client.post("/api/v1/faces/update-bbox", json={"face_id": 1})
        assert resp.status_code == 400

    def test_negative_bbox_returns_400(self, client, bp_app):
        face_id = _insert_face(bp_app, 0, cluster_id=CLUSTER_UNASSIGNED)
        resp = client.post(
            "/api/v1/faces/update-bbox",
            json={
                "face_id": face_id,
                "bbox_pct": {"x": -5, "y": 10, "w": 20, "h": 20},
            },
        )
        assert resp.status_code == 400, "negative x should be rejected"

    def test_overflow_bbox_returns_400(self, client, bp_app):
        face_id = _insert_face(bp_app, 0, cluster_id=CLUSTER_UNASSIGNED)
        resp = client.post(
            "/api/v1/faces/update-bbox",
            json={
                "face_id": face_id,
                "bbox_pct": {"x": 50, "y": 50, "w": 60, "h": 20},
            },
        )
        assert resp.status_code == 400, "x+w > 100 should be rejected"

    def test_zero_size_bbox_returns_400(self, client, bp_app):
        face_id = _insert_face(bp_app, 0, cluster_id=CLUSTER_UNASSIGNED)
        resp = client.post(
            "/api/v1/faces/update-bbox",
            json={
                "face_id": face_id,
                "bbox_pct": {"x": 10, "y": 10, "w": 0, "h": 20},
            },
        )
        assert resp.status_code == 400, "zero-width bbox should be rejected"


# ── Lookup / load failures ────────────────────────────────────────


def test_unknown_face_id_returns_404(client):
    resp = client.post(
        "/api/v1/faces/update-bbox",
        json={"face_id": 99999, "bbox_pct": {"x": 10, "y": 10, "w": 20, "h": 20}},
    )
    assert resp.status_code == 404


def test_image_load_failure_returns_500(client, bp_app, monkeypatch):
    face_id = _insert_face(bp_app, 0, cluster_id=CLUSTER_UNASSIGNED)
    monkeypatch.setattr("bpp.scoring.aggregate.load_and_downscale", lambda *_a, **_kw: None)
    resp = client.post(
        "/api/v1/faces/update-bbox",
        json={"face_id": face_id, "bbox_pct": {"x": 10, "y": 10, "w": 20, "h": 20}},
    )
    assert resp.status_code == 500


def test_embedding_extraction_failure_returns_422(client, bp_app, monkeypatch):
    """When extract_embedding_for_region returns None, surface a 422 with a
    user-facing message — the box couldn't yield a face."""
    face_id = _insert_face(bp_app, 0, cluster_id=CLUSTER_UNASSIGNED)
    monkeypatch.setattr(
        "bpp.scoring.aggregate.load_and_downscale",
        lambda *_a, **_kw: np.zeros((512, 512, 3), dtype=np.uint8),
    )
    monkeypatch.setattr(
        "bpp.scoring.face_embed.extract_embedding_for_region",
        lambda *_a, **_kw: None,
    )
    resp = client.post(
        "/api/v1/faces/update-bbox",
        json={"face_id": face_id, "bbox_pct": {"x": 10, "y": 10, "w": 20, "h": 20}},
    )
    assert resp.status_code == 422
    assert "no face detected" in resp.get_json()["error"].lower()


# ── Happy paths ──────────────────────────────────────────────────


class TestIdentityStickiness:
    """Identity is sticky across bbox updates: this endpoint never
    re-matches a face to a different cluster. Auto-matching from a
    user-drawn box proved unreliable — tiny crop shifts moved the
    SFace embedding marginally closer to an unrelated centroid, so
    a legitimate resize would silently flip the person. Reassignment
    lives in the explicit Reassign / Label flows instead.
    """

    def test_assigned_face_keeps_its_cluster_after_resize(self, client, bp_app, monkeypatch):
        """A face that was already labeled (cluster_id >= 0) must stay
        in the SAME cluster after a bbox edit — even if the re-extracted
        embedding lands closer to a different cluster's centroid.
        """
        # Two distinct clusters in the library — 2 photos each, different
        # face_index so they share photo slots safely.
        cluster_a_emb = np.ones(128, dtype=np.float32) * 0.05
        cluster_b_emb = np.ones(128, dtype=np.float32) * 0.95
        for i in range(2):
            _insert_face(bp_app, i, face_index=0, cluster_id=0, embedding=cluster_a_emb)
        for i in range(2):
            _insert_face(bp_app, i, face_index=1, cluster_id=1, embedding=cluster_b_emb)
        # Target face starts as cluster 0 on photo 2.
        target_face_id = _insert_face(
            bp_app, 2, face_index=0, cluster_id=0, embedding=cluster_a_emb
        )
        # After drag, the re-extracted embedding lands MUCH closer to
        # cluster 1's centroid — the old behavior would flip identity here.
        _stub_pipeline(monkeypatch, cluster_b_emb)
        resp = client.post(
            "/api/v1/faces/update-bbox",
            json={
                "face_id": target_face_id,
                "bbox_pct": {"x": 20, "y": 20, "w": 30, "h": 30},
            },
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["matched"] is True, body
        assert body["cluster_id"] == 0, "identity must stick — should NOT have flipped to cluster 1"

    def test_unassigned_face_stays_unassigned_after_resize(self, client, bp_app, monkeypatch):
        """A face that was unassigned stays unassigned. The user must
        use the explicit Label flow to claim identity — this endpoint
        only fixes geometry.
        """
        cluster_emb = np.ones(128, dtype=np.float32) * 0.05
        for i in range(2):
            _insert_face(bp_app, i, face_index=0, cluster_id=0, embedding=cluster_emb)
        target_face_id = _insert_face(bp_app, 2, face_index=0, cluster_id=CLUSTER_UNASSIGNED)
        # Even a perfect-match embedding is ignored — identity is sticky.
        _stub_pipeline(monkeypatch, cluster_emb)
        resp = client.post(
            "/api/v1/faces/update-bbox",
            json={
                "face_id": target_face_id,
                "bbox_pct": {"x": 20, "y": 20, "w": 30, "h": 30},
            },
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["matched"] is False, body
        assert body["cluster_id"] == CLUSTER_UNASSIGNED
        assert body["person_name"] is None

    def test_bbox_and_embedding_persisted_to_db(self, client, bp_app, monkeypatch):
        """The endpoint must overwrite bbox_x/y/w/h and embedding in face_embeddings."""
        ctx = _ctx(bp_app)
        target_face_id = _insert_face(bp_app, 0, face_index=0, cluster_id=CLUSTER_UNASSIGNED)
        new_emb = np.ones(128, dtype=np.float32) * 0.2
        _stub_pipeline(monkeypatch, new_emb, bbox=(123, 456, 78, 90), quality=0.55)
        resp = client.post(
            "/api/v1/faces/update-bbox",
            json={
                "face_id": target_face_id,
                "bbox_pct": {"x": 10, "y": 10, "w": 10, "h": 10},
            },
        )
        assert resp.status_code == 200
        with bp_app.app_context():
            row = (
                ctx.get_conn()
                .execute(
                    "SELECT bbox_x, bbox_y, bbox_w, bbox_h, embedding, quality "
                    "FROM face_embeddings WHERE id=?",
                    (target_face_id,),
                )
                .fetchone()
            )
        assert (row["bbox_x"], row["bbox_y"], row["bbox_w"], row["bbox_h"]) == (
            123,
            456,
            78,
            90,
        )
        stored = np.frombuffer(row["embedding"], dtype=np.float32)
        np.testing.assert_allclose(stored, new_emb)
        assert abs(row["quality"] - 0.55) < 1e-6, "quality should be persisted"


def test_drop_on_non_face_region_returns_422(client, bp_app, monkeypatch):
    """Regression: dropping the bbox on a non-face region (e.g. a
    strawberry-pattern blanket) must NOT be force-matched to a random
    cluster. The original implementation synthesized landmarks when
    YuNet found nothing, which produced a phantom embedding that landed
    near some cluster centroid and the matcher cheerfully said "James".
    Fix: when YuNet sees no face, refuse the update outright.
    """
    cluster_emb = np.ones(128, dtype=np.float32) * 0.05
    for i in range(3):
        _insert_face(bp_app, i, face_index=0, cluster_id=0, embedding=cluster_emb)
    target_face_id = _insert_face(bp_app, 3, face_index=0, cluster_id=CLUSTER_UNASSIGNED)

    # Image loads fine but extract_embedding_for_region returns None
    # (this is what happens when YuNet finds no face in the region).
    monkeypatch.setattr(
        "bpp.scoring.aggregate.load_and_downscale",
        lambda *_a, **_kw: np.zeros((1024, 1024, 3), dtype=np.uint8),
    )
    monkeypatch.setattr(
        "bpp.scoring.face_embed.extract_embedding_for_region",
        lambda *_a, **_kw: None,
    )

    resp = client.post(
        "/api/v1/faces/update-bbox",
        json={
            "face_id": target_face_id,
            "bbox_pct": {"x": 10, "y": 10, "w": 20, "h": 20},
        },
    )
    assert resp.status_code == 422
    msg = resp.get_json()["error"].lower()
    assert "no face" in msg, "error message should say 'no face detected'"


def test_response_includes_corrected_bbox_pct(client, bp_app, monkeypatch):
    """The YuNet path may shift the bbox slightly — response should reflect
    the actual stored bbox, not the user's drop position."""
    face_id = _insert_face(bp_app, 0, face_index=0, cluster_id=CLUSTER_UNASSIGNED)
    # Image is 1024x1024 (from _stub_pipeline). Server-corrected bbox at
    # (256, 256, 512, 512) should come back as ~25/25/50/50 in percent.
    _stub_pipeline(
        monkeypatch,
        np.random.randn(128),
        bbox=(256, 256, 512, 512),
    )
    resp = client.post(
        "/api/v1/faces/update-bbox",
        json={
            "face_id": face_id,
            "bbox_pct": {"x": 30, "y": 30, "w": 40, "h": 40},
        },
    )
    body = resp.get_json()
    assert resp.status_code == 200
    assert abs(body["bbox_pct"]["x"] - 25.0) < 0.1
    assert abs(body["bbox_pct"]["w"] - 50.0) < 0.1

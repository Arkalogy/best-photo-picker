"""Tests for POST /api/v1/faces/create — create a face_embeddings row
from a user-drawn bbox + chosen person.

Used when YuNet missed a face entirely. Same YuNet-must-confirm-a-face
rule as update-bbox; refuses 422 if no face is detected in the region.
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
                "face_count": 0,
                "largest_face_ratio": 0.0,
                "face_center_dist": 0.5,
                "composition_score": 0.5,
                "aggregate_score": 0.5,
            }
        )
    return items


@pytest.fixture
def bp_app(tmp_path):
    workdir = str(tmp_path / "workdir")
    os.makedirs(workdir)
    with open(os.path.join(workdir, "analysis.json"), "w") as f:
        json.dump(_make_analysis(4), f)
    app = create_app(workdir=workdir)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(bp_app):
    return bp_app.test_client()


def _ctx(bp_app):
    return bp_app.extensions["bpp"]


def _path_hash_for(bp_app, photo_idx=0):
    ctx = _ctx(bp_app)
    with bp_app.app_context():
        conn = ctx.get_conn()
        row = conn.execute(
            "SELECT filepath FROM photos ORDER BY id LIMIT 1 OFFSET ?",
            (photo_idx,),
        ).fetchone()
        return ctx.thumbs.get_hash(row["filepath"]) if ctx.thumbs else None


def _seed_cluster(bp_app, cluster_id, photo_idx, *, embedding=None):
    """Insert a face row to make ``cluster_id`` exist in the library."""
    ctx = _ctx(bp_app)
    with bp_app.app_context():
        conn = ctx.get_conn()
        photo_row = conn.execute(
            "SELECT id FROM photos ORDER BY id LIMIT 1 OFFSET ?",
            (photo_idx,),
        ).fetchone()
        if embedding is None:
            embedding = np.random.randn(128).astype(np.float32)
        conn.execute(
            "INSERT INTO face_embeddings"
            " (photo_id, face_index, embedding, cluster_id,"
            "  bbox_x, bbox_y, bbox_w, bbox_h, quality)"
            " VALUES (?, 0, ?, ?, 100, 100, 200, 200, 0.7)",
            (photo_row["id"], embedding.tobytes(), cluster_id),
        )
        conn.commit()


def _stub_pipeline(monkeypatch, embedding, *, method="yunet", quality=0.8, bbox=None):
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


# ── Happy paths ──────────────────────────────────────────────────


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


# ── New person branch ────────────────────────────────────────────


class TestNewPerson:
    """The Add Face flow can also mint a brand-new cluster + person album
    atomically. Server allocates cluster_id (high-water + 1), inserts
    the face row, and creates a smart_person album with the user's
    chosen name — all in one transaction so partial state is
    impossible (e.g. a face with no matching album, or an empty named
    cluster if YuNet refused the region)."""

    def test_creates_face_and_named_album(self, client, bp_app, monkeypatch):
        ctx = _ctx(bp_app)
        ph = _path_hash_for(bp_app, photo_idx=0)
        # Seed an unrelated cluster so the new-person id is allocated
        # ABOVE existing ones (not just at 0).
        _seed_cluster(bp_app, 7, 1)

        _stub_pipeline(monkeypatch, np.ones(128, dtype=np.float32) * 0.4)
        resp = client.post(
            "/api/v1/faces/create",
            json={
                "path_hash": ph,
                "new_person_name": "Tiny Human",
                "bbox_pct": {"x": 10, "y": 10, "w": 20, "h": 20},
            },
        )
        body = resp.get_json()
        assert resp.status_code == 200, body
        assert body["cluster_id"] == 8, (
            "new cluster_id should be max(existing) + 1 — existing max was 7"
        )
        assert body["person_name"] == "Tiny Human"

        with bp_app.app_context():
            conn = ctx.get_conn()
            face_row = conn.execute(
                "SELECT cluster_id FROM face_embeddings WHERE id=?",
                (body["face_id"],),
            ).fetchone()
            album_row = conn.execute(
                "SELECT name FROM albums WHERE album_type='smart_person'"
                " AND rule_json LIKE '%\"cluster_id\": 8%'"
            ).fetchone()
        assert face_row["cluster_id"] == 8
        assert album_row is not None, "smart_person album should exist for new cluster"
        assert album_row["name"] == "Tiny Human"

    def test_first_person_gets_cluster_zero(self, client, bp_app, monkeypatch):
        """When the library has zero face_embeddings, the first new-person
        Add Face should allocate cluster_id=0 (not throw on the MAX query)."""
        ph = _path_hash_for(bp_app, photo_idx=0)
        _stub_pipeline(monkeypatch, np.ones(128, dtype=np.float32) * 0.4)
        resp = client.post(
            "/api/v1/faces/create",
            json={
                "path_hash": ph,
                "new_person_name": "Lone Face",
                "bbox_pct": {"x": 10, "y": 10, "w": 20, "h": 20},
            },
        )
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()["cluster_id"] == 0

    def test_yunet_rejection_leaves_no_orphan_cluster(self, client, bp_app, monkeypatch):
        """If YuNet refuses the region (422), we must NOT have minted a
        new cluster or created an album — the create has to be fully
        atomic, not partial."""
        ctx = _ctx(bp_app)
        ph = _path_hash_for(bp_app, photo_idx=0)
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
                "new_person_name": "Ghost",
                "bbox_pct": {"x": 10, "y": 10, "w": 20, "h": 20},
            },
        )
        assert resp.status_code == 422

        with bp_app.app_context():
            conn = ctx.get_conn()
            face_count = conn.execute("SELECT COUNT(*) AS n FROM face_embeddings").fetchone()
            album = conn.execute(
                "SELECT 1 FROM albums WHERE album_type='smart_person' AND name='Ghost'"
            ).fetchone()
        assert face_count["n"] == 0, "no face row should be created on YuNet rejection"
        assert album is None, "no orphan 'Ghost' album should exist"

    def test_rejects_both_cluster_id_and_new_person_name(self, client, bp_app):
        ph = _path_hash_for(bp_app)
        resp = client.post(
            "/api/v1/faces/create",
            json={
                "path_hash": ph,
                "cluster_id": 0,
                "new_person_name": "Whoever",
                "bbox_pct": {"x": 10, "y": 10, "w": 20, "h": 20},
            },
        )
        assert resp.status_code == 400
        assert "not both" in resp.get_json()["error"].lower()

    def test_creating_over_existing_face_returns_409(self, client, bp_app, monkeypatch):
        """Same patch + same person embedding = duplicate. Reject 409
        with a message naming the existing person, so the user knows
        to use Reassign instead of Add Face."""
        ctx = _ctx(bp_app)
        ph = _path_hash_for(bp_app, photo_idx=0)
        # Seed an existing face on photo 0 with a known embedding.
        existing_emb = np.ones(128, dtype=np.float32) * 0.05
        with bp_app.app_context():
            conn = ctx.get_conn()
            photo_id = conn.execute("SELECT id FROM photos ORDER BY id LIMIT 1").fetchone()["id"]
            conn.execute(
                "INSERT INTO face_embeddings (photo_id, face_index, embedding,"
                " cluster_id, bbox_x, bbox_y, bbox_w, bbox_h, quality)"
                " VALUES (?, 0, ?, 0, 200, 200, 300, 300, 0.7)",
                (photo_id, existing_emb.tobytes()),
            )
            # Name the cluster so the error message includes it.
            # v36: include the shadow column the lookup now indexes on.
            conn.execute(
                "INSERT INTO albums (name, album_type, rule_json,"
                " smart_person_cluster_id)"
                " VALUES ('Noah', 'smart_person', '{\"cluster_id\": 0}', 0)"
            )
            conn.commit()

        # Stub YuNet to "find" a face overlapping the existing one with
        # a near-identical embedding.
        _stub_pipeline(monkeypatch, existing_emb, bbox=(210, 210, 290, 290))
        resp = client.post(
            "/api/v1/faces/create",
            json={
                "path_hash": ph,
                "cluster_id": 0,
                "bbox_pct": {"x": 20, "y": 20, "w": 30, "h": 30},
            },
        )
        assert resp.status_code == 409, resp.get_json()
        body = resp.get_json()
        assert "Noah" in body["error"], body
        # P7: the duplicate_face_id kwarg now rides inside the BppError
        # envelope's structured ``context`` block, not at the top level.
        assert body.get("context", {}).get("duplicate_face_id"), body

    def test_overlap_but_different_embedding_passes(self, client, bp_app, monkeypatch):
        """High IoU alone is NOT enough — two faces in close proximity
        (kids hugging, profile next to frontal) routinely show 0.5+ IoU
        despite being different people. We must NOT reject those, so
        the embedding-distance check has to gate the rejection."""
        ctx = _ctx(bp_app)
        ph = _path_hash_for(bp_app, photo_idx=0)
        existing_emb = np.ones(128, dtype=np.float32) * 0.05
        with bp_app.app_context():
            conn = ctx.get_conn()
            photo_id = conn.execute("SELECT id FROM photos ORDER BY id LIMIT 1").fetchone()["id"]
            conn.execute(
                "INSERT INTO face_embeddings (photo_id, face_index, embedding,"
                " cluster_id, bbox_x, bbox_y, bbox_w, bbox_h, quality)"
                " VALUES (?, 0, ?, 0, 200, 200, 300, 300, 0.7)",
                (photo_id, existing_emb.tobytes()),
            )
            conn.commit()

        # Same patch, but a totally different embedding — should NOT
        # be flagged as duplicate.
        far_emb = np.ones(128, dtype=np.float32) * 5.0
        _stub_pipeline(monkeypatch, far_emb, bbox=(210, 210, 290, 290))
        resp = client.post(
            "/api/v1/faces/create",
            json={
                "path_hash": ph,
                "cluster_id": 0,
                "bbox_pct": {"x": 20, "y": 20, "w": 30, "h": 30},
            },
        )
        assert resp.status_code == 200, (
            "high IoU alone must not reject — embeddings diverge so it's a different face"
        )

    def test_same_person_far_apart_passes(self, client, bp_app, monkeypatch):
        """The same person can legitimately appear twice on one photo
        (e.g. a mirror reflection). Same embedding but disjoint bboxes
        must NOT be rejected — IoU has to gate the rejection too."""
        ctx = _ctx(bp_app)
        ph = _path_hash_for(bp_app, photo_idx=0)
        existing_emb = np.ones(128, dtype=np.float32) * 0.05
        with bp_app.app_context():
            conn = ctx.get_conn()
            photo_id = conn.execute("SELECT id FROM photos ORDER BY id LIMIT 1").fetchone()["id"]
            conn.execute(
                "INSERT INTO face_embeddings (photo_id, face_index, embedding,"
                " cluster_id, bbox_x, bbox_y, bbox_w, bbox_h, quality)"
                " VALUES (?, 0, ?, 0, 50, 50, 100, 100, 0.7)",
                (photo_id, existing_emb.tobytes()),
            )
            conn.commit()

        # Same embedding but bbox is in a totally different part of the
        # photo — no overlap, must not be flagged.
        _stub_pipeline(monkeypatch, existing_emb, bbox=(700, 700, 200, 200))
        resp = client.post(
            "/api/v1/faces/create",
            json={
                "path_hash": ph,
                "cluster_id": 0,
                "bbox_pct": {"x": 70, "y": 70, "w": 20, "h": 20},
            },
        )
        assert resp.status_code == 200, "matching embedding without spatial overlap must not reject"

    def test_whitespace_only_name_treated_as_missing(self, client, bp_app):
        """``new_person_name: '   '`` should fall through to the existing-
        person validation (and fail there since no cluster_id either)."""
        ph = _path_hash_for(bp_app)
        resp = client.post(
            "/api/v1/faces/create",
            json={
                "path_hash": ph,
                "new_person_name": "   ",
                "bbox_pct": {"x": 10, "y": 10, "w": 20, "h": 20},
            },
        )
        assert resp.status_code == 400


# ──────────────────────────────────────────────────────────────────
# Unit tests for the helpers extracted from api_faces_create
# (review followup 2026-05-31 — handler decomposed from 261 LOC into
# four named helpers; per-helper coverage below).
# ──────────────────────────────────────────────────────────────────


class TestParseBboxPct:
    def test_valid_input_returns_floats(self):
        from bpp.web.face_create_helpers import parse_bbox_pct as _parse_bbox_pct

        assert _parse_bbox_pct({"x": 10, "y": 20, "w": 30, "h": 40}) == (
            10.0,
            20.0,
            30.0,
            40.0,
        )

    def test_missing_field_raises_validation_error(self):
        from bpp.errors import ValidationError
        from bpp.web.face_create_helpers import parse_bbox_pct as _parse_bbox_pct

        with pytest.raises(ValidationError, match="must be numbers"):
            _parse_bbox_pct({"x": 10, "y": 20, "w": 30})  # no h

    def test_non_numeric_field_raises(self):
        from bpp.errors import ValidationError
        from bpp.web.face_create_helpers import parse_bbox_pct as _parse_bbox_pct

        with pytest.raises(ValidationError, match="must be numbers"):
            _parse_bbox_pct({"x": "ten", "y": 20, "w": 30, "h": 40})

    def test_negative_dimensions_raise(self):
        from bpp.errors import ValidationError
        from bpp.web.face_create_helpers import parse_bbox_pct as _parse_bbox_pct

        with pytest.raises(ValidationError, match="out of"):
            _parse_bbox_pct({"x": 10, "y": 20, "w": -5, "h": 40})

    def test_out_of_range_raises(self):
        from bpp.errors import ValidationError
        from bpp.web.face_create_helpers import parse_bbox_pct as _parse_bbox_pct

        # x + w > 100 → out of bounds
        with pytest.raises(ValidationError, match="out of"):
            _parse_bbox_pct({"x": 80, "y": 20, "w": 30, "h": 40})


class TestParseCreateInputs:
    def test_existing_person_branch(self):
        from bpp.web.face_create_helpers import parse_create_inputs as _parse_create_inputs

        path_hash, cluster_id, name, bbox = _parse_create_inputs(
            {
                "path_hash": "abc",
                "cluster_id": 5,
                "bbox_pct": {"x": 10, "y": 10, "w": 10, "h": 10},
            }
        )
        assert path_hash == "abc"
        assert cluster_id == 5
        assert name is None
        assert bbox == (10.0, 10.0, 10.0, 10.0)

    def test_new_person_branch(self):
        from bpp.web.face_create_helpers import parse_create_inputs as _parse_create_inputs

        path_hash, cluster_id, name, bbox = _parse_create_inputs(
            {
                "path_hash": "abc",
                "new_person_name": "  Alice  ",
                "bbox_pct": {"x": 0, "y": 0, "w": 50, "h": 50},
            }
        )
        assert path_hash == "abc"
        assert cluster_id is None
        assert name == "Alice"  # stripped
        assert bbox == (0.0, 0.0, 50.0, 50.0)

    def test_missing_path_hash_raises(self):
        from bpp.errors import ValidationError
        from bpp.web.face_create_helpers import parse_create_inputs as _parse_create_inputs

        with pytest.raises(ValidationError, match="path_hash"):
            _parse_create_inputs({"cluster_id": 5, "bbox_pct": {"x": 0, "y": 0, "w": 10, "h": 10}})

    def test_neither_cluster_nor_name_raises(self):
        from bpp.errors import ValidationError
        from bpp.web.face_create_helpers import parse_create_inputs as _parse_create_inputs

        with pytest.raises(ValidationError, match="non-negative int"):
            _parse_create_inputs(
                {"path_hash": "abc", "bbox_pct": {"x": 0, "y": 0, "w": 10, "h": 10}}
            )

    def test_both_cluster_and_name_raises(self):
        from bpp.errors import ValidationError
        from bpp.web.face_create_helpers import parse_create_inputs as _parse_create_inputs

        with pytest.raises(ValidationError, match="not both"):
            _parse_create_inputs(
                {
                    "path_hash": "abc",
                    "cluster_id": 5,
                    "new_person_name": "Alice",
                    "bbox_pct": {"x": 0, "y": 0, "w": 10, "h": 10},
                }
            )

    def test_empty_name_falls_through_to_cluster_requirement(self):
        """Whitespace-only new_person_name is treated as missing; without
        a cluster_id the parser raises."""
        from bpp.errors import ValidationError
        from bpp.web.face_create_helpers import parse_create_inputs as _parse_create_inputs

        with pytest.raises(ValidationError, match="non-negative int"):
            _parse_create_inputs(
                {
                    "path_hash": "abc",
                    "new_person_name": "   ",
                    "bbox_pct": {"x": 0, "y": 0, "w": 10, "h": 10},
                }
            )

    def test_negative_cluster_id_rejected(self):
        from bpp.errors import ValidationError
        from bpp.web.face_create_helpers import parse_create_inputs as _parse_create_inputs

        with pytest.raises(ValidationError, match="non-negative int"):
            _parse_create_inputs(
                {
                    "path_hash": "abc",
                    "cluster_id": -1,
                    "bbox_pct": {"x": 0, "y": 0, "w": 10, "h": 10},
                }
            )

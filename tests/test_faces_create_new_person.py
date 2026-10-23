"""Tests for POST /api/v1/faces/create — create a face_embeddings row — part 2 of 2: TestNewPerson,
TestParseBboxPct, TestParseCreateInputs."""

# Shared fixtures are imported by name and re-bound as test parameters.
# ruff: noqa: F811

from __future__ import annotations

import numpy as np
import pytest

from tests._faces_create_shared import (  # noqa: F401
    _ctx,
    _make_analysis,
    _path_hash_for,
    _seed_cluster,
    _stub_pipeline,
    bp_app,
    client,
)

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
        # The duplicate_face_id kwarg now rides inside the BppError
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
# (handler decomposed from 261 LOC into
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

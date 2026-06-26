"""Face embedding load cap.

Mirrors `tests/test_clip.py::TestClipCapOverride` for the parallel
`FACE_EMBEDDINGS_MAX_ROWS` knob in `bpp/db/face_queries.py`. Without
this cap, a multi-million-face library would OOM the server on
`api_faces_restore` when the centroid + reassignment pass tries to
`np.stack(all_active_embeddings)`.
"""

from __future__ import annotations

import sqlite3

import numpy as np
import pytest

from bpp.constants import CLUSTER_DISMISSED, CLUSTER_UNASSIGNED
from bpp.db.connection import close_all_connections, get_db, init_db
from bpp.db.face_queries import (
    FACE_MAX_OVERRIDE_BYPASS,
    FACE_MAX_OVERRIDE_KEY,
    FaceEmbeddingsTooLarge,
    assert_face_load_cap,
)
from bpp.db.photos import upsert_photo
from bpp.db.settings import set_setting


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "face_cap.db")
    init_db(db_path)
    conn = get_db(db_path)
    conn.row_factory = sqlite3.Row
    yield conn
    close_all_connections()


def _seed_face(conn, tmp_path, name, *, cluster_id=CLUSTER_UNASSIGNED, face_index=0):
    """Insert a (photo, face_embedding) pair the cap counter will see."""
    f = tmp_path / name
    f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 32)
    pid = upsert_photo(conn, {"filepath": str(f)})
    emb = np.zeros(128, dtype=np.float32)
    conn.execute(
        "INSERT INTO face_embeddings (photo_id, face_index, embedding, cluster_id) "
        "VALUES (?, ?, ?, ?)",
        (pid, face_index, emb.tobytes(), cluster_id),
    )
    conn.commit()


class TestFaceLoadCap:
    """The cap itself: count > cap raises; count <= cap passes through."""

    def test_under_cap_passes(self, db, tmp_path, monkeypatch):
        from bpp.db import face_queries as fq

        monkeypatch.setattr(fq, "FACE_EMBEDDINGS_MAX_ROWS", 10)

        _seed_face(db, tmp_path, "a.jpg")
        _seed_face(db, tmp_path, "b.jpg")
        # Under cap — no raise.
        assert_face_load_cap(db, count=2)

    def test_at_cap_passes(self, db, monkeypatch):
        from bpp.db import face_queries as fq

        monkeypatch.setattr(fq, "FACE_EMBEDDINGS_MAX_ROWS", 5)
        # Exactly at the cap — must NOT raise.
        assert_face_load_cap(db, count=5)

    def test_above_cap_raises(self, db, monkeypatch):
        from bpp.db import face_queries as fq

        monkeypatch.setattr(fq, "FACE_EMBEDDINGS_MAX_ROWS", 2)

        with pytest.raises(FaceEmbeddingsTooLarge) as exc:
            assert_face_load_cap(db, count=3)
        # Exception carries the diagnostic numbers callers can surface.
        assert exc.value.count == 3
        assert exc.value.cap == 2
        # Message includes peak-memory math the user can act on.
        msg = str(exc.value)
        assert "3" in msg and "MB" in msg


class TestFaceCapOverride:
    """Per-library override (Settings → Faces banner flow).

    Verifies the override is the SINGLE knob that flips the cap check:
    same library + same row count + same env var, but with the override
    setting present, the load is allowed instead of raising.
    """

    def test_override_bypasses_cap(self, db, tmp_path, monkeypatch):
        from bpp.db import face_queries as fq

        monkeypatch.setattr(fq, "FACE_EMBEDDINGS_MAX_ROWS", 1)
        _seed_face(db, tmp_path, "a.jpg")
        _seed_face(db, tmp_path, "b.jpg")
        _seed_face(db, tmp_path, "c.jpg")

        # Control: without override, the cap fires.
        with pytest.raises(FaceEmbeddingsTooLarge):
            assert_face_load_cap(db, count=3)

        # With override, the same DB + same cap loads cleanly.
        set_setting(db, FACE_MAX_OVERRIDE_KEY, FACE_MAX_OVERRIDE_BYPASS)
        assert_face_load_cap(db, count=3)

    def test_override_with_non_bypass_value_does_not_unlock(self, db, monkeypatch):
        """Only the literal 'bypass' sentinel unlocks the cap.

        Any other value (truthy / falsy / typo) leaves the cap enforced.
        Locks the constant so a future contributor can't silently change
        the sentinel string and break the override path.
        """
        from bpp.db import face_queries as fq

        monkeypatch.setattr(fq, "FACE_EMBEDDINGS_MAX_ROWS", 1)

        for stray_value in ("true", "1", "yes", "enabled", ""):
            set_setting(db, FACE_MAX_OVERRIDE_KEY, stray_value)
            with pytest.raises(FaceEmbeddingsTooLarge):
                assert_face_load_cap(db, count=3)


class TestApiFacesRestore503OnCap:
    """End-to-end: api_faces_restore returns 503 when the active face
    count exceeds the cap, with structured payload the UI banner can
    render. The user's restored rows persist — we just refuse the
    follow-on reassignment pass (the OOM-prone step)."""

    def test_returns_503_with_structured_payload(self, db, tmp_path, monkeypatch):
        from bpp.db import face_queries as fq
        from bpp.web.app import create_app

        # Seed: 2 dismissed + 4 active. Cap = 3 → restoring the 2 dismissed
        # pushes active count to 6, above cap. Should refuse reassignment.
        _seed_face(db, tmp_path, "act1.jpg", cluster_id=0)
        _seed_face(db, tmp_path, "act2.jpg", cluster_id=0)
        _seed_face(db, tmp_path, "act3.jpg", cluster_id=1)
        _seed_face(db, tmp_path, "act4.jpg", cluster_id=1)
        d1 = tmp_path / "dis1.jpg"
        d1.write_bytes(b"\xff\xd8\xff" + b"\x00" * 32)
        d2 = tmp_path / "dis2.jpg"
        d2.write_bytes(b"\xff\xd8\xff" + b"\x00" * 32)
        for path in (d1, d2):
            pid = upsert_photo(db, {"filepath": str(path)})
            db.execute(
                "INSERT INTO face_embeddings (photo_id, face_index, embedding, cluster_id) "
                "VALUES (?, ?, ?, ?)",
                (pid, 0, np.zeros(128, dtype=np.float32).tobytes(), CLUSTER_DISMISSED),
            )
        db.commit()

        monkeypatch.setattr(fq, "FACE_EMBEDDINGS_MAX_ROWS", 3)

        app = create_app(workdir=str(tmp_path), library_path=str(tmp_path))
        # Force the test app to read the same DB our seed wrote to.
        ctx = app.extensions["bpp"]
        monkeypatch.setattr(ctx, "get_conn", lambda: db)
        client = app.test_client()
        token = app.extensions["bpp"].auth_token

        resp = client.post(
            "/api/v1/faces/restore",
            json={"all": True},
            headers={"X-Auth-Token": token},
        )
        assert resp.status_code == 503
        body = resp.get_json()
        assert body["code"] == "face_embeddings_too_large"
        # Structured fields the UI banner reads.
        assert body["count"] == 6
        assert body["cap"] == 3
        assert body["restored"] == 2


class TestApiFacesMerge503OnCap:
    """End-to-end: api_faces_merge returns 503 when the faces in the clusters
    being merged exceed the cap, instead of OOM-killing on the np.stack
    centroid pass. Mirrors the restore guard."""

    def test_returns_503_with_structured_payload(self, db, tmp_path, monkeypatch):
        from bpp.db import face_queries as fq
        from bpp.web.app import create_app

        # 4 faces across two clusters; cap = 3 → merging them (count 4) refuses.
        _seed_face(db, tmp_path, "m1.jpg", cluster_id=0)
        _seed_face(db, tmp_path, "m2.jpg", cluster_id=0)
        _seed_face(db, tmp_path, "m3.jpg", cluster_id=1)
        _seed_face(db, tmp_path, "m4.jpg", cluster_id=1)
        db.commit()

        monkeypatch.setattr(fq, "FACE_EMBEDDINGS_MAX_ROWS", 3)

        app = create_app(workdir=str(tmp_path), library_path=str(tmp_path))
        ctx = app.extensions["bpp"]
        monkeypatch.setattr(ctx, "get_conn", lambda: db)
        client = app.test_client()
        token = ctx.auth_token

        resp = client.post(
            "/api/v1/faces/merge",
            json={"primary_cluster_id": 0, "merge_cluster_ids": [1]},
            headers={"X-Auth-Token": token},
        )
        assert resp.status_code == 503, resp.get_json()
        body = resp.get_json()
        assert body["code"] == "face_embeddings_too_large"
        assert body["count"] == 4
        assert body["cap"] == 3

    def test_under_cap_merge_proceeds(self, db, tmp_path, monkeypatch):
        """A small merge under the cap is not blocked by the new guard."""
        from bpp.db import face_queries as fq
        from bpp.web.app import create_app

        _seed_face(db, tmp_path, "s1.jpg", cluster_id=0)
        _seed_face(db, tmp_path, "s2.jpg", cluster_id=1)
        db.commit()

        monkeypatch.setattr(fq, "FACE_EMBEDDINGS_MAX_ROWS", 100)

        app = create_app(workdir=str(tmp_path), library_path=str(tmp_path))
        ctx = app.extensions["bpp"]
        monkeypatch.setattr(ctx, "get_conn", lambda: db)
        client = app.test_client()
        token = ctx.auth_token

        resp = client.post(
            "/api/v1/faces/merge",
            json={"primary_cluster_id": 0, "merge_cluster_ids": [1]},
            headers={"X-Auth-Token": token},
        )
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()["status"] == "merged"

"""M5 regression: face cluster map must be cached on WebAppState.

Before the fix, every call to /api/v1/photos/select with selected_faces
rebuilt the filepath→[cluster_ids] map from scratch via a JOIN query.
After the fix, WebAppState.get_face_cluster_map() caches the result and
invalidate_face_cluster_map() clears it so the next call re-fetches.

Tests verify:
- get_face_cluster_map returns correct data from DB
- Second call returns the same object (cache hit — no re-query)
- invalidate_face_cluster_map clears the cache (next call re-fetches)
- FaceWorker calls invalidate after clustering completes
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture()
def ctx_with_faces(tmp_path):
    """WebAppState with 3 face clusters seeded in the DB."""
    from bpp.web.app import create_app

    wd = str(tmp_path / "wd")
    os.makedirs(wd)
    app = create_app(workdir=wd)
    app.config["TESTING"] = True

    with app.app_context():
        import numpy as np

        from bpp.db.photos import upsert_photo
        from bpp.web.state import get_ctx

        ctx = get_ctx()
        conn = ctx.get_conn()

        # Seed 6 photos, 3 clusters
        photo_ids = []
        for i in range(6):
            upsert_photo(conn, {"filepath": f"/tmp/fc_{i}.jpg", "aggregate_score": 0.5})
            row = conn.execute(
                "SELECT id FROM photos WHERE filepath=?", (f"/tmp/fc_{i}.jpg",)
            ).fetchone()
            photo_ids.append(row[0])

        emb = np.zeros(512, dtype=np.float32).tobytes()
        for i, pid in enumerate(photo_ids):
            cluster_id = i % 3  # clusters 0, 1, 2
            conn.execute(
                "INSERT INTO face_embeddings (photo_id, face_index, embedding, cluster_id)"
                " VALUES (?, 0, ?, ?)",
                (pid, emb, cluster_id),
            )
        conn.commit()

        yield ctx


class TestGetFaceClusterMap:
    def test_returns_correct_clusters(self, ctx_with_faces):
        ctx = ctx_with_faces
        m = ctx.get_face_cluster_map()
        # 6 photos, each in exactly one cluster
        assert len(m) == 6
        # Each filepath maps to a list with one cluster_id
        for _fp, cids in m.items():
            assert isinstance(cids, list)
            assert len(cids) == 1
            assert cids[0] in (0, 1, 2)

    def test_cache_hit_returns_same_object(self, ctx_with_faces):
        ctx = ctx_with_faces
        first = ctx.get_face_cluster_map()
        second = ctx.get_face_cluster_map()
        assert first is second, "Second call must return cached object, not re-fetch"

    def test_invalidate_clears_cache(self, ctx_with_faces):
        ctx = ctx_with_faces
        first = ctx.get_face_cluster_map()
        ctx.invalidate_face_cluster_map()
        second = ctx.get_face_cluster_map()
        # After invalidation, a fresh object is built
        assert first is not second

    def test_invalidate_then_fetch_reflects_db_changes(self, ctx_with_faces):
        ctx = ctx_with_faces
        _ = ctx.get_face_cluster_map()  # prime cache

        # Add a new face embedding to the DB
        conn = ctx.get_conn()
        import numpy as np

        from bpp.db.photos import upsert_photo

        upsert_photo(conn, {"filepath": "/tmp/fc_new.jpg", "aggregate_score": 0.5})
        row = conn.execute("SELECT id FROM photos WHERE filepath='/tmp/fc_new.jpg'").fetchone()
        emb = np.zeros(512, dtype=np.float32).tobytes()
        conn.execute(
            "INSERT INTO face_embeddings (photo_id, face_index, embedding, cluster_id)"
            " VALUES (?, 0, ?, 0)",
            (row[0], emb),
        )
        conn.commit()

        # Stale cache doesn't see the new photo
        stale = ctx.get_face_cluster_map()
        assert "/tmp/fc_new.jpg" not in stale

        # After invalidation, fresh fetch sees it
        ctx.invalidate_face_cluster_map()
        fresh = ctx.get_face_cluster_map()
        assert "/tmp/fc_new.jpg" in fresh


class TestFaceWorkerInvalidatesCache:
    """Source scan: face_worker must call invalidate_face_cluster_map after clustering."""

    def test_face_worker_calls_invalidate(self):
        # P3 — the invalidation call moved into the phase-7
        # (reconstruct_identities) function of face_extraction_phases.
        # Scan both files so the contract stays load-bearing.
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent / "bpp" / "web"
        worker_src = (root / "face_worker.py").read_text()
        phases_src = (root / "face_extraction_phases.py").read_text()

        assert (
            "invalidate_face_cluster_map" in worker_src
            or "invalidate_face_cluster_map" in phases_src
        ), (
            "The face-extraction pipeline must call ctx.invalidate_face_cluster_map() "
            "after clustering so the cache is cleared. Otherwise photo selection "
            "continues using stale cluster assignments."
        )

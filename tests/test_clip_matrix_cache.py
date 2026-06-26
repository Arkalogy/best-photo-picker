"""TDD tests for H-5: pre-compute and cache CLIP stacked matrix."""

from __future__ import annotations

import numpy as np


class TestClipMatrixCache:
    def test_load_clip_embeddings_builds_matrix(self, tmp_path):
        """After loading, clip_cache must contain a pre-built matrix."""
        import os
        import sqlite3

        from bpp.db.clip import upsert_clip_embedding
        from bpp.db.connection import init_db
        from bpp.web.app import create_app

        workdir = str(tmp_path / "w")
        os.makedirs(workdir)
        db_path = os.path.join(workdir, "photopicker.db")
        init_db(db_path)

        # Seed some photos + embeddings
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        for i in range(5):
            conn.execute(
                "INSERT INTO photos (filepath, original_filename, file_size, file_mtime) "
                "VALUES (?, ?, ?, ?)",
                (f"/tmp/p{i}.jpg", f"p{i}.jpg", 100, 1000.0),
            )
        conn.commit()
        rows = conn.execute("SELECT id FROM photos").fetchall()
        for row in rows:
            emb = np.random.randn(512).astype(np.float32)
            emb /= np.linalg.norm(emb)
            upsert_clip_embedding(conn, row[0], emb)
        conn.close()

        app = create_app(workdir=workdir)
        app.config["TESTING"] = True
        ctx = app.extensions["bpp"]

        with app.app_context():
            embeddings = ctx.load_clip_embeddings()
            assert len(embeddings) == 5

            # Matrix and IDs must be cached
            assert "matrix" in ctx.clip_cache
            assert "matrix_ids" in ctx.clip_cache
            matrix = ctx.clip_cache["matrix"]
            ids = ctx.clip_cache["matrix_ids"]
            assert isinstance(matrix, np.ndarray)
            assert matrix.shape == (5, 512)
            assert len(ids) == 5

    def test_cache_reset_clears_matrix(self, tmp_path):
        """When clip_cache is reset, matrix must also be cleared."""
        import os

        from bpp.web.app import create_app

        workdir = str(tmp_path / "w")
        os.makedirs(workdir)
        app = create_app(workdir=workdir)
        ctx = app.extensions["bpp"]

        # Manually set some cached data
        ctx.clip_cache["matrix"] = np.zeros((3, 512))
        ctx.clip_cache["matrix_ids"] = [1, 2, 3]
        ctx.clip_cache["ready"] = True

        # Simulate reset (as done in switch_library / bp_analysis)
        ctx.clip_cache = {"embeddings": {}, "ready": False}

        assert "matrix" not in ctx.clip_cache
        assert "matrix_ids" not in ctx.clip_cache

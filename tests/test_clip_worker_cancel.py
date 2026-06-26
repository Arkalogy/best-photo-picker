"""TDD tests for H-4: ClipWorker must pass cancellation_check."""

from __future__ import annotations

import ast
import os


def _read_clip_worker():
    path = os.path.join(os.path.dirname(__file__), "..", "bpp", "web", "clip_worker.py")
    with open(path) as f:
        return f.read()


class TestClipWorkerCancellation:
    def test_run_passes_cancellation_check(self):
        """ClipWorker._run must pass cancellation_check to compute_clip_embeddings."""
        source = _read_clip_worker()
        # Find the call to compute_clip_embeddings inside _run
        tree = ast.parse(source)
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                # Match compute_clip_embeddings(...)
                if isinstance(func, ast.Name) and func.id == "compute_clip_embeddings":
                    kwarg_names = [kw.arg for kw in node.keywords]
                    if "cancellation_check" in kwarg_names:
                        found = True
                        break
        assert found, (
            "compute_clip_embeddings() call in ClipWorker._run must include "
            "cancellation_check keyword argument"
        )


class TestClipWorkerNoLockDuringInference:
    def test_inference_runs_with_no_open_write_txn(self, tmp_path, monkeypatch):
        """Regression: CLIP inference (the slow part) must run with NO open
        write transaction. The old version did the INSERT inline and committed
        only every 50, so the write lock was held across ~49 slow inferences —
        long enough to exceed the 30s busy_timeout and fail concurrent
        foreground writes with 'database is locked' (same class as the SHA-256
        backfill bug)."""
        import sqlite3

        import numpy as np

        import bpp.web.clip_worker as cw
        from bpp.db.connection import close_all_connections, get_db, init_db
        from bpp.db.photos import upsert_photo

        db_path = str(tmp_path / "clip.db")
        init_db(db_path)
        conn = get_db(db_path)
        conn.row_factory = sqlite3.Row

        missing = []
        for i in range(5):
            fp = tmp_path / f"img_{i}.jpg"
            fp.write_bytes(b"\xff\xd8\xff\xe0" + bytes([i]) * 100)
            pid = upsert_photo(conn, {"filepath": str(fp)})
            missing.append((str(fp), pid))
        conn.commit()

        txn_states: list[bool] = []

        def _fake_embed(path):
            # Capture whether a write transaction is open at inference time.
            txn_states.append(conn.in_transaction)
            return np.ones(512, dtype=np.float32)

        monkeypatch.setattr(cw, "compute_clip_embedding_from_file", _fake_embed)

        computed = cw.compute_clip_embeddings(conn, missing)

        assert computed == 5
        assert txn_states, "inference should have run for the seeded photos"
        assert not any(txn_states), (
            "every CLIP inference must run with NO open write transaction so the "
            f"worker never holds the write lock across slow work; states = {txn_states}"
        )
        n = conn.execute("SELECT COUNT(*) FROM clip_embeddings").fetchone()[0]
        assert n == 5
        close_all_connections()

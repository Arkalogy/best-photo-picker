"""The hash backfill submits work in bounded chunks and writes hashes in
batches, not one future per photo up front and one commit per row."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace


class _Conn:
    def __init__(self):
        self.commits = 0
        self.batches: list[list] = []

    def executemany(self, sql, rows):
        self.batches.append(list(rows))

    def execute(self, *a):
        raise AssertionError("per-row execute")

    def commit(self):
        self.commits += 1


def test_hashes_are_written_in_batches_and_work_is_submitted_in_chunks(monkeypatch):
    from bpp.web import derived_recovery as dr
    from bpp.web.analysis_store import AnalysisStore

    peak, live, lock = [0], [0], threading.Lock()

    class _Pool(ThreadPoolExecutor):
        def submit(self, fn, *a, **k):
            with lock:
                live[0] += 1
                peak[0] = max(peak[0], live[0])

            def _done(_f):
                with lock:
                    live[0] -= 1

            f = super().submit(fn, *a, **k)
            f.add_done_callback(_done)
            return f

    monkeypatch.setattr(dr, "ThreadPoolExecutor", _Pool)
    monkeypatch.setattr(dr, "compute_hashes_from_file", lambda fp: (1, 2))
    conn = _Conn()
    ctx = SimpleNamespace(analysis_store=AnalysisStore())
    files = [f"/x/{i}.jpg" for i in range(1000)]

    out = dr._backfill_hashes(ctx, conn, files)
    assert out is not None
    computed, errors = out
    assert len(computed) == 1000 and errors == 0
    chunk = dr._PHASH_WRITE_BATCH
    assert all(len(b) <= chunk for b in conn.batches)
    assert sum(len(b) for b in conn.batches) == 1000
    assert conn.commits <= 1000 // chunk + 1
    assert peak[0] <= chunk, f"{peak[0]} futures in flight at once"
    assert ctx.analysis_store.phash_done == 1000

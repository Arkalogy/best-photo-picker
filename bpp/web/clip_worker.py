"""Background CLIP embedding extraction with progress queue for SSE streaming."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from typing import Any

import numpy as np

from bpp.constants import CLIP_MODEL_NAME
from bpp.db.connection import init_db
from bpp.scoring.clip_embed import (
    compute_clip_embedding_from_file,
    ensure_model,
)
from bpp.utils.logging import get_logger
from bpp.web.base_worker import BackgroundWorker

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Shared CLIP embedding loop — used by both ClipWorker and AnalyzeWorker
# ---------------------------------------------------------------------------


def compute_clip_embeddings(
    conn: sqlite3.Connection,
    missing: list[tuple[str, int]],
    progress_callback: Callable[[int, int, str], None] | None = None,
    cancellation_check: Callable[[], bool] | None = None,
) -> int:
    """Compute CLIP embeddings for a list of (filepath, photo_id) pairs.

    Writes embeddings to DB in batches of 50.  Returns count computed.

    Journaled: a 'clip_extraction' entry is written before the loop and
    completed after. The per-batch commits + the next-run "missing"
    query make this loop idempotent on its own, but the journal gives
    operators a visible breadcrumb of in-flight work and lets startup
    recovery confirm any partial run is unfinished (the recovery
    handler is a no-op apart from clearing the entry, since the next
    analyze run already repopulates).
    """
    total = len(missing)
    if total == 0:
        return 0

    from bpp.db.journal import journal_complete, journal_start
    from bpp.utils.retry import retry_io

    journal_id = journal_start(
        conn,
        "clip_extraction",
        {"total": total, "model": CLIP_MODEL_NAME},
    )

    computed = 0
    # Embedding inference is the slow part (100-500ms/photo) and must run with
    # NO open write transaction. The previous version did the INSERT inline and
    # committed only every 50, so the write lock was held across ~49 slow CLIP
    # inferences — long enough to blow the 30s busy_timeout and fail concurrent
    # foreground writes with "database is locked" (same class as the SHA-256
    # backfill bug). Accumulate (photo_id, blob) and flush in a tight
    # executemany+commit so the lock is held for milliseconds, not seconds.
    pending: list[tuple[int, str, bytes]] = []

    def _flush() -> None:
        if not pending:
            return
        conn.executemany(
            "INSERT OR REPLACE INTO clip_embeddings (photo_id, model_name, embedding)"
            " VALUES (?, ?, ?)",
            pending,
        )
        conn.commit()
        pending.clear()

    for i, (fp, photo_id) in enumerate(missing):
        if cancellation_check and cancellation_check():
            break

        # Check file exists (retry for NAS flakiness)
        try:
            file_exists = retry_io(os.path.exists, fp, label="clip_exists")
        except OSError:
            file_exists = False
        if not file_exists:
            if progress_callback:
                progress_callback(i + 1, total, os.path.basename(fp))
            continue

        emb = compute_clip_embedding_from_file(fp)  # slow inference — NO open txn
        if emb is not None:
            blob = emb.astype(np.float32).tobytes()
            pending.append((photo_id, CLIP_MODEL_NAME, blob))
            computed += 1
            if len(pending) >= 50:
                _flush()

        if progress_callback:
            progress_callback(i + 1, total, os.path.basename(fp))

    _flush()

    conn.commit()
    journal_complete(conn, journal_id)
    return computed


def register_clip_extraction_recovery() -> None:
    """Bind the 'clip_extraction' journal recovery handler.

    No-op recovery: each photo's INSERT-OR-REPLACE commits in batches
    of 50 (idempotent), and the next ClipWorker run queries for
    photos missing embeddings — partial work picks up automatically.
    The journal entry exists for operator visibility; recovery just
    clears it.
    """
    from bpp.db.journal import register_recovery_handler

    def _recover(_conn: sqlite3.Connection, _payload: dict) -> bool:
        log.info(
            "Found pending clip_extraction journal — clearing breadcrumb. "
            "Any missing embeddings will repopulate on the next analyze run."
        )
        return True

    register_recovery_handler("clip_extraction", _recover, replace=True)


class ClipWorker(BackgroundWorker):
    """Computes CLIP embeddings in a background thread."""

    _worker_name = "CLIP extraction"

    def start(
        self,
        analysis: list[dict[str, Any]],
        db_path: str,
    ) -> bool:
        """Start background CLIP extraction. Returns False if already running."""
        return self._start_thread(analysis, db_path)

    def _run(
        self,
        analysis: list[dict[str, Any]],
        db_path: str,
    ) -> None:
        if not analysis:
            self._emit({"type": "done", "total": 0, "computed": 0})
            return

        # Fail fast if model can't be downloaded/loaded
        try:
            ensure_model()
        except Exception as e:
            log.error("CLIP model download failed: %s", e)
            self._emit(
                {
                    "type": "error",
                    "message": "CLIP model download failed. Check server logs for details.",
                }
            )
            return

        conn = init_db(db_path)

        # Build photo_id lookup limited to the analysis batch. Loading the
        # full photos table is wasteful — analysis is typically a recent
        # slice (newly imported photos) while the library may have 50K+
        # rows. Chunked IN-clause keeps each query under SQLite's
        # ~999-parameter limit.
        analysis_paths = [item["filepath"] for item in analysis]
        filepath_to_id: dict[str, int] = {}
        _CHUNK = 500
        for i in range(0, len(analysis_paths), _CHUNK):
            chunk = analysis_paths[i : i + _CHUNK]
            placeholders = ",".join("?" for _ in chunk)
            rows = conn.execute(
                f"SELECT id, filepath FROM photos WHERE filepath IN ({placeholders})",
                chunk,
            ).fetchall()
            for row in rows:
                filepath_to_id[row[1]] = row[0]

        # Find photos that need embeddings
        existing: set[int] = set()
        try:
            cached = conn.execute(
                "SELECT photo_id FROM clip_embeddings WHERE model_name = ?",
                (CLIP_MODEL_NAME,),
            ).fetchall()
            existing = {r[0] for r in cached}
        except sqlite3.OperationalError:
            log.debug("clip_embeddings table not yet created, will process all")

        missing = []
        for item in analysis:
            fp = item["filepath"]
            photo_id = filepath_to_id.get(fp)
            if photo_id is not None and photo_id not in existing:
                missing.append((fp, photo_id))

        total = len(missing)
        self._emit({"type": "start", "total": total})

        if total == 0:
            self._emit({"type": "done", "total": 0, "computed": 0})
            return

        def _progress(current: int, total: int, basename: str) -> None:
            self._emit(
                {"type": "progress", "current": current, "total": total, "filepath": basename}
            )

        computed = compute_clip_embeddings(
            conn,
            missing,
            progress_callback=_progress,
            cancellation_check=lambda: self._cancelled.is_set(),
        )

        self._emit({"type": "done", "total": total, "computed": computed})
        log.info("CLIP extraction done: %d/%d embeddings computed", computed, total)

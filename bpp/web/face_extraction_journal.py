"""P3.5 — per-phase journal for face extraction with SIGKILL resume.

Pre-v37 a crash during ``extract_and_cluster_faces`` forced a full re-run
on next startup. v37 adds the ``face_extraction_journal`` table that
records (run_id, phases_complete bitmask, serialized PreExtractSnapshot)
for each in-flight invocation so the orchestrator can resume at the
next incomplete phase instead of repeating phases 1..N-1.

Phase numbering (matches the seven phases in
:mod:`bpp.web.face_extraction_phases`):

* Bit 0 = phase 1 (reconcile_method)
* Bit 1 = phase 2 (preload_cached_embeddings)
* Bit 2 = phase 3 (partition_cached_vs_extract)  — pure, not journaled
* Bit 3 = phase 4 (delete_stale_embeddings)
* Bit 4 = phase 5 (extract_new_embeddings)
* Bit 5 = phase 6 (cluster_faces)
* Bit 6 = phase 7 (reconstruct_identities)

Phase 3 is pure (in-memory only) so its bit gets set together with
phase 4's. Phase 4.5 (capture_dismissed_slots) is read-only and folds
into phase 5's commit boundary — there's nothing to roll back.

Resume contract:

* :func:`start_run` creates a journal row with phases_complete=0 and
  snapshot_json=NULL. Returns the run_id.
* :func:`mark_phase_complete` flips the bit + commits.
* :func:`store_snapshot` serializes the :class:`PreExtractSnapshot`
  as JSON in the row so phases 6/7 can rehydrate it after a crash.
* :func:`complete_run` sets completed_at = now and clears the row's
  pending state.
* :func:`pending_runs` is what the recovery handler scans on startup.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import TYPE_CHECKING, Any

from bpp.utils.logging import get_logger

if TYPE_CHECKING:
    from bpp.web.face_extraction_phases import PreExtractSnapshot

log = get_logger(__name__)


# Phase bit positions. Public so tests can assert against them.
PHASE_BIT_METHOD_RECONCILE = 0
PHASE_BIT_PRELOAD = 1
PHASE_BIT_PARTITION = 2
PHASE_BIT_STALE_DELETE = 3
PHASE_BIT_EXTRACT = 4
PHASE_BIT_CLUSTER = 5
PHASE_BIT_IDENTITY = 6
ALL_PHASE_BITS = 0b1111111

# T0.4 — bounded recovery retries. The recovery handler increments
# ``retry_count`` before each attempt; once it reaches MAX_RECOVERY_RETRIES
# the row is force-completed with ``completed_at = GAVE_UP_SENTINEL`` so
# subsequent startups skip it. The number is conservative — 3 attempts
# is plenty to cross a transient FS / network blip; anything failing 3
# times is deterministic and won't fix itself.
MAX_RECOVERY_RETRIES = 3
GAVE_UP_SENTINEL = -1


def _reset_table_cache() -> None:
    """Compatibility shim — earlier P3.5 drafts memoized the table
    probe by id(conn) and exposed this hook for tests. The cache was
    removed (positive AND negative caching both have stale-state
    failure modes under id recycling, observed in the full suite at
    random seeds), so this function is now a no-op kept for the
    callers in tests/test_face_extraction_journal.py until they
    migrate."""


def _has_journal_table(conn: sqlite3.Connection) -> bool:
    """Probe for the face_extraction_journal table.

    Returns False on databases that haven't been migrated to v37 (test
    fixtures hand-roll the schema; rare in production but the
    orchestrator must not crash when invoked against such DBs). The
    journal layer degrades to no-resume mode in that case — the
    orchestrator still runs every phase, just without crash recovery.

    Intentionally NOT memoized. We've tried both axes and neither
    holds up:

    * ``id(conn)`` in a module dict — breaks on id recycling: a closed
      conn's id is reused by a fresh conn that the cache still claims
      has the table. Caused intermittent test flakes (the original
      reason this comment exists).
    * Per-instance attribute on the conn — ``sqlite3.Connection``
      doesn't support arbitrary Python attributes (verified:
      ``AttributeError: object has no attribute 'foo'``) and doesn't
      support weak references either, so neither ``conn._cache`` nor
      ``WeakKeyDictionary(conn)`` works. Subclassing Connection at
      the pool layer would work but the cost/benefit doesn't justify
      the architectural change.

    Production cost: ~7 sqlite_master probes per face extraction,
    each sub-millisecond on an in-memory SQLite catalog. Invisible
    against the per-photo inference cost (~50ms+ each).
    """
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='face_extraction_journal'"
        ).fetchone()
        return row is not None
    except sqlite3.OperationalError:
        # Non-sqlite dialect (postgres) hits a different probe path; if
        # the catalog query itself fails, assume the table is absent.
        return False


def start_run(conn: sqlite3.Connection, run_id: str | None = None) -> str:
    """Open a journal row for a fresh extraction run.

    ``run_id`` is auto-generated when not provided. Callers (the
    orchestrator) typically let this default; recovery passes the
    existing run_id back in via :func:`mark_phase_complete` so a
    resumed run amends its own row instead of starting fresh.

    When the journal table is missing (pre-v37 DB or test fixture), a
    fresh run_id is still returned but no row is inserted. Downstream
    journal calls become no-ops; the orchestrator runs every phase
    without crash recovery.
    """
    if run_id is None:
        run_id = uuid.uuid4().hex
    if not _has_journal_table(conn):
        return run_id
    conn.execute(
        "INSERT INTO face_extraction_journal (run_id, phases_complete, started_at)"
        " VALUES (?, 0, ?)",
        (run_id, int(time.time())),
    )
    conn.commit()
    return run_id


def mark_phase_complete(conn: sqlite3.Connection, run_id: str, phase_bit: int) -> None:
    """Set the bit for *phase_bit* in the row's phases_complete bitmask.

    No-op when the journal table is missing.
    """
    if not _has_journal_table(conn):
        return
    conn.execute(
        "UPDATE face_extraction_journal SET phases_complete = phases_complete | ? WHERE run_id = ?",
        (1 << phase_bit, run_id),
    )
    conn.commit()


def store_snapshot(
    conn: sqlite3.Connection,
    run_id: str,
    snapshot: PreExtractSnapshot,
) -> None:
    """Serialize the :class:`PreExtractSnapshot` into the row.

    No-op when the journal table is missing.
    """
    if not _has_journal_table(conn):
        return
    conn.execute(
        "UPDATE face_extraction_journal SET snapshot_json = ? WHERE run_id = ?",
        (_serialize_snapshot(snapshot), run_id),
    )
    conn.commit()


def load_snapshot(conn: sqlite3.Connection, run_id: str) -> PreExtractSnapshot | None:
    """Rehydrate the snapshot stored in the journal row.

    Returns ``None`` when the row has no snapshot OR the table is
    missing — both cases mean "no resume payload available."
    """
    if not _has_journal_table(conn):
        return None
    row = conn.execute(
        "SELECT snapshot_json FROM face_extraction_journal WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    if not row or not row[0]:
        return None
    return _deserialize_snapshot(row[0])


def complete_run(conn: sqlite3.Connection, run_id: str) -> None:
    """Mark the journal row complete after phase 7 succeeds.

    No-op when the journal table is missing.
    """
    if not _has_journal_table(conn):
        return
    conn.execute(
        "UPDATE face_extraction_journal SET completed_at = ? WHERE run_id = ?",
        (int(time.time()), run_id),
    )
    conn.commit()


def get_retry_count(conn: sqlite3.Connection, run_id: str) -> int:
    """Return the retry_count for *run_id*, or 0 if missing/no table.

    T0.4. ``retry_count`` was added in schema v39; databases that
    haven't run that migration return 0.
    """
    if not _has_journal_table(conn):
        return 0
    try:
        row = conn.execute(
            "SELECT retry_count FROM face_extraction_journal WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        # Column doesn't exist (pre-v39 DB) — treat as 0.
        return 0
    return int(row[0]) if row else 0


def increment_retry_count(conn: sqlite3.Connection, run_id: str) -> int:
    """Atomically bump the retry counter on the journal row.

    Returns the new retry_count value. No-op when the journal table is
    missing OR the column doesn't exist (pre-v39 DB) — recovery still
    runs in those cases, just without the bounded-retry guard. The
    caller should check the returned value against
    :data:`MAX_RECOVERY_RETRIES` to decide whether to attempt recovery
    this time.
    """
    if not _has_journal_table(conn):
        return 0
    try:
        conn.execute(
            "UPDATE face_extraction_journal SET retry_count = retry_count + 1 WHERE run_id = ?",
            (run_id,),
        )
        conn.commit()
    except sqlite3.OperationalError:
        # Pre-v39 column missing — degrade gracefully.
        return 0
    return get_retry_count(conn, run_id)


def force_complete_after_retries(conn: sqlite3.Connection, run_id: str) -> None:
    """Mark a run as "gave up" by setting completed_at = GAVE_UP_SENTINEL.

    T0.4: called by the recovery handler when ``retry_count`` exceeds
    ``MAX_RECOVERY_RETRIES``. The sentinel value lets future startups
    skip the row via the standard ``WHERE completed_at IS NULL`` filter
    without losing the diagnostic that this run never finished.
    """
    if not _has_journal_table(conn):
        return
    conn.execute(
        "UPDATE face_extraction_journal SET completed_at = ? WHERE run_id = ?",
        (GAVE_UP_SENTINEL, run_id),
    )
    conn.commit()
    log.warning(
        "Face extraction run_id=%s force-completed after %d failed recovery "
        "attempts. Manual intervention required: run Settings → Faces → Retry "
        "to re-run face extraction from scratch.",
        run_id,
        MAX_RECOVERY_RETRIES,
    )


def get_phases_complete(conn: sqlite3.Connection, run_id: str) -> int:
    """Return the phases_complete bitmask for *run_id*, or 0 if missing.

    Also 0 when the table itself is missing — every phase will re-run.
    """
    if not _has_journal_table(conn):
        return 0
    row = conn.execute(
        "SELECT phases_complete FROM face_extraction_journal WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    return int(row[0]) if row else 0


def pending_runs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """List every face-extraction run that didn't finish.

    Returns ``[]`` when the journal table is missing (pre-v37 DB).
    """
    if not _has_journal_table(conn):
        return []
    rows = conn.execute(
        "SELECT run_id, phases_complete, snapshot_json, started_at "
        "FROM face_extraction_journal "
        "WHERE completed_at IS NULL ORDER BY started_at"
    ).fetchall()
    return [
        {
            "run_id": r[0],
            "phases_complete": int(r[1]),
            "snapshot_json": r[2],
            "started_at": int(r[3]),
        }
        for r in rows
    ]


def is_phase_complete(phases_complete: int, phase_bit: int) -> bool:
    """Helper: is bit *phase_bit* set in the bitmask?"""
    return bool(phases_complete & (1 << phase_bit))


# ── Serialization helpers ──


def _serialize_snapshot(snapshot: PreExtractSnapshot) -> str:
    """JSON-serialize the snapshot.

    ``frozenset`` / ``set`` instances are converted to lists; tuples
    are preserved as 2-element lists (phase 5's restoration logic
    converts them back to tuples on load).

    ``old_cluster_photos`` (dict[int, set[int]]) is encoded as
    [[cluster_id, [photo_ids...]], ...] because JSON object keys are
    strings — preserving the int identity through a list-of-tuples
    is simpler than parsing string-int keys back on load.
    """
    return json.dumps(
        {
            "stored_method": snapshot.stored_method,
            "method_changed": snapshot.method_changed,
            "old_cluster_photos": (
                [[cid, sorted(pids)] for cid, pids in snapshot.old_cluster_photos.items()]
                if snapshot.old_cluster_photos is not None
                else None
            ),
            "stale_photo_ids": sorted(snapshot.stale_photo_ids),
            "dismissed_slots": [list(s) for s in sorted(snapshot.dismissed_slots)],
        },
        sort_keys=True,
    )


def _deserialize_snapshot(blob: str) -> PreExtractSnapshot:
    """Inverse of :func:`_serialize_snapshot`. Tolerant of corrupted
    JSON or partial shapes — defaults missing/malformed keys to
    empty/None so a corrupted DB row doesn't crash recovery.

    Uses :func:`bpp.utils.json_utils.safe_json_loads` (project rule:
    "JSON from DB/network/disk: always safe_json_loads()"). Each
    sub-value goes through a defensive coerce that catches per-row
    type errors so a single bad slot doesn't sink the whole snapshot.
    """
    from bpp.utils.json_utils import safe_json_loads
    from bpp.web.face_extraction_phases import PreExtractSnapshot

    payload = safe_json_loads(blob, {}, context="face_extraction_journal snapshot")
    if not isinstance(payload, dict):
        # Corrupted blob (somehow got a non-dict JSON value). Treat as
        # absent — the orchestrator re-runs phase 1.
        return PreExtractSnapshot(
            stored_method=None,
            method_changed=False,
            old_cluster_photos=None,
            stale_photo_ids=frozenset(),
            dismissed_slots=frozenset(),
        )

    raw_ocp = payload.get("old_cluster_photos")
    old_cluster_photos: dict[int, set[int]] | None = None
    if raw_ocp is not None:
        old_cluster_photos = {}
        try:
            for cid, pids in raw_ocp:
                try:
                    int_cid = int(cid)
                    int_pids = {int(p) for p in pids if p is not None}
                    if int_pids:
                        old_cluster_photos[int_cid] = int_pids
                except (TypeError, ValueError):
                    # Drop the bad row, keep the rest.
                    log.warning(
                        "Snapshot old_cluster_photos: dropping bad row %r",
                        (cid, pids),
                    )
        except (TypeError, ValueError):
            # raw_ocp isn't iterable as expected — treat as absent.
            old_cluster_photos = None

    stale_ids: frozenset[int]
    try:
        stale_ids = frozenset(int(p) for p in (payload.get("stale_photo_ids") or []))
    except (TypeError, ValueError):
        log.warning("Snapshot stale_photo_ids malformed; defaulting to empty")
        stale_ids = frozenset()

    dismissed: frozenset[tuple[int, int]]
    try:
        dismissed = frozenset(
            (int(pid), int(fi)) for pid, fi in (payload.get("dismissed_slots") or [])
        )
    except (TypeError, ValueError):
        log.warning("Snapshot dismissed_slots malformed; defaulting to empty")
        dismissed = frozenset()

    return PreExtractSnapshot(
        stored_method=payload.get("stored_method"),
        method_changed=bool(payload.get("method_changed")),
        old_cluster_photos=old_cluster_photos,
        stale_photo_ids=stale_ids,
        dismissed_slots=dismissed,
    )

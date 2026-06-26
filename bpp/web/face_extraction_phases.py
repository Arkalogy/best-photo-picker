"""P3 — face extraction decomposed into 7 explicit phases.

Until P3, ``extract_and_cluster_faces`` was one 530-line function with
implicit cross-phase state: ``dismissed_slots`` captured halfway through
and consumed near the end; ``old_cluster_photos`` captured in one of
two branches depending on whether stale embeddings existed; the
clustering branch reaching for state set by the extraction branch.

The audit flagged this as the module's main correctness risk — the
"either path" branches were the bug door for the next maintainer.

This module makes the snapshot typed and explicit
(:class:`PreExtractSnapshot`) and splits the function into seven
focused phases. Each phase takes a connection, the snapshot, and
phase-specific inputs; returns its own output type; commits to DB on
success.

* :func:`reconcile_method` — sface ↔ dlib switch detection. Wipes
  old embeddings if the method changed. Returns the partial snapshot
  with ``stored_method``, ``method_changed``, ``old_cluster_photos``.
* :func:`preload_cached_embeddings` — bulk-loads existing embeddings
  with one batched IN-clause per 500 photos. Also finds stale
  (NULL-quality) photo ids that need re-extraction.
* :func:`partition_cached_vs_extract` — pure in-memory split of
  cached emissions vs. the to-extract list.
* :func:`delete_stale_embeddings` — wipes NULL-quality rows that
  are about to be re-extracted, so the quality gate's prunes don't
  linger as orphan rows.
* :func:`extract_new_embeddings` — drives the per-photo
  Process/Thread pool, INSERT-OR-REPLACEs new embeddings, restores
  dismissed slots that the INSERT would have overwritten.
* :func:`cluster_faces` — adaptive threshold + hard negatives +
  ``_assign_new_faces`` + cluster_id UPDATE + post-cluster dedup +
  cluster count.
* :func:`reconstruct_identities` — ``_reconstruct_identities`` plus
  the legacy photo-overlap remap plus the smart-album refresh +
  ctx cluster-map invalidation.

The orchestrator
(``extract_and_cluster_faces`` in :mod:`bpp.web.face_worker`) now
threads ``conn``, the snapshot, and the photo records through these
seven phases in order, polling cancel between phases. Each phase is
independently unit-testable; the previous monolith was only
testable as a whole.

See :mod:`bpp.web.face_phase_pipeline` for the Phase protocol +
canonical :data:`FACE_PIPELINE` ordering; recovery semantics live
in :mod:`bpp.web.face_extraction_journal`.
"""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from bpp.constants import (
    CLUSTER_DISMISSED,
)
from bpp.db.face_embedding_safety import decode_embedding
from bpp.utils.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class PreExtractSnapshot:
    """State captured before face extraction begins, consumed by later phases.

    Frozen so a phase can't mutate it under another's nose. Stored
    method + change flag drive whether downstream phases need to do
    cluster-name remap. ``old_cluster_photos`` is the
    ``{cluster_id: {photo_id, ...}}`` map at start of run — used by
    the legacy fallback remap when identity labels aren't available
    on every cluster.

    Why a snapshot and not direct DB reads in each phase: the
    extraction phase mutates ``face_embeddings`` in flight, so a
    later phase reading "what was the dismissed status of slot
    (photo_id, face_index)" via SELECT would see the *new* state,
    not the pre-extract state. The audit's main bug door.
    """

    stored_method: str | None
    method_changed: bool
    old_cluster_photos: dict[int, set[int]] | None
    stale_photo_ids: frozenset[int]
    dismissed_slots: frozenset[tuple[int, int]] = field(default_factory=frozenset)


@dataclass
class ExtractionPartition:
    """Result of :func:`partition_cached_vs_extract`.

    Holds both the cached emissions (already added to ``all_records``)
    and the to-extract triples consumed by the next phase. Carried
    separately from :class:`PreExtractSnapshot` because it depends on
    runtime input (``with_faces``) rather than pre-extract DB state.
    """

    #: ``(filepath, photo_id, face_index, embedding)`` for every cached face.
    cached_records: list[tuple[str, int, int, np.ndarray]]
    #: ``(index_in_with_faces, filepath, photo_id)`` triples still needing extraction.
    need_extract: list[tuple[int, str, int]]


# ── Phase 1: method reconciler ──


def _snapshot_cluster_photos(conn: sqlite3.Connection) -> dict[int, set[int]]:
    """Capture ``{cluster_id: {photo_id, ...}}`` mapping. Wraps the
    same logic the previous monolith used — kept private here so the
    function is reachable by both the method reconciler and the
    preloader without a circular import back to face_worker."""
    rows = conn.execute(
        "SELECT cluster_id, photo_id FROM face_embeddings WHERE cluster_id >= 0"
    ).fetchall()
    out: dict[int, set[int]] = {}
    for cid, pid in rows:
        out.setdefault(cid, set()).add(pid)
    return out


def reconcile_method(
    conn: sqlite3.Connection,
    current_method: str,
) -> tuple[str | None, bool, dict[int, set[int]] | None]:
    """Phase 1: detect sface ↔ dlib method change and wipe if needed.

    Returns ``(stored_method, method_changed, old_cluster_photos)``.
    ``old_cluster_photos`` is captured only when the method changed (the
    only case in which the wipe destroys the cluster→photo mapping that
    the identity-writer phase needs to remap names against). The
    preloader phase also captures it conditionally on stale-photo
    presence; the orchestrator merges the two.

    Side effects: ``DELETE FROM face_embeddings`` if method changed;
    ``INSERT OR REPLACE INTO settings`` for the current method.
    """
    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE key='face_embedding_method'"
        ).fetchone()
        stored_method = row[0] if row else None
    except Exception:
        log.warning("Failed to read stored face embedding method", exc_info=True)
        stored_method = None

    old_cluster_photos: dict[int, set[int]] | None = None
    method_changed = bool(stored_method) and stored_method != current_method
    if method_changed:
        log.info(
            "Embedding method changed (%s → %s) — clearing old embeddings",
            stored_method,
            current_method,
        )
        old_cluster_photos = _snapshot_cluster_photos(conn)
        conn.execute("DELETE FROM face_embeddings")
        conn.commit()

    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        ("face_embedding_method", current_method),
    )
    conn.commit()
    return stored_method, method_changed, old_cluster_photos


# ── Phase 2: preload cached embeddings ──


def preload_cached_embeddings(
    conn: sqlite3.Connection,
    with_faces: list[dict[str, Any]],
    photo_map: dict[str, int],
) -> tuple[dict[int, list[tuple[int, bytes]]], frozenset[int]]:
    """Phase 2: bulk-load cached embeddings + identify stale photos.

    Returns ``(cached_by_pid, stale_photo_ids)``. Cached emissions are
    keyed by photo_id → list of ``(face_index, embedding_blob)`` tuples.
    Stale photos are those with at least one ``quality IS NULL`` row;
    they will be re-extracted regardless of whether other rows for the
    same photo are intact.

    Bulk IN-clauses (chunks of 500) instead of N SELECTs — on a 10K-photo
    library this was the dominant cost when the function ran in-process.
    """
    candidate_pids: list[int] = []
    seen_pids: set[int] = set()
    for item in with_faces:
        pid = photo_map.get(item["filepath"])
        if pid is not None and pid not in seen_pids:
            candidate_pids.append(pid)
            seen_pids.add(pid)

    cached_by_pid: dict[int, list[tuple[int, bytes]]] = {}
    for start in range(0, len(candidate_pids), 500):
        chunk = candidate_pids[start : start + 500]
        placeholders = ",".join("?" * len(chunk))
        rows = conn.execute(
            "SELECT photo_id, face_index, embedding FROM face_embeddings "
            f"WHERE photo_id IN ({placeholders}) "
            f"AND cluster_id != {CLUSTER_DISMISSED}",
            chunk,
        ).fetchall()
        for pid, face_idx, emb_blob in rows:
            cached_by_pid.setdefault(pid, []).append((face_idx, emb_blob))

    stale_photo_ids: frozenset[int] = frozenset(
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT photo_id FROM face_embeddings WHERE quality IS NULL"
        ).fetchall()
    )
    if stale_photo_ids:
        log.info(
            "Found %d photos with legacy face data — will re-extract",
            len(stale_photo_ids),
        )

    return cached_by_pid, stale_photo_ids


# ── Phase 3: partition cached vs. need-extract ──


def partition_cached_vs_extract(
    with_faces: list[dict[str, Any]],
    photo_map: dict[str, int],
    cached_by_pid: dict[int, list[tuple[int, bytes]]],
    stale_photo_ids: frozenset[int],
) -> ExtractionPartition:
    """Phase 3: pure-function split between cached emissions and to-extract.

    No DB access; deterministic given inputs. Cached emissions are
    decoded from blob to ndarray once here so downstream phases work
    with typed arrays.
    """
    cached_records: list[tuple[str, int, int, np.ndarray]] = []
    need_extract: list[tuple[int, str, int]] = []
    for i, item in enumerate(with_faces):
        fp = item["filepath"]
        photo_id = photo_map.get(fp)
        if photo_id is None:
            continue
        cached = cached_by_pid.get(photo_id)
        if cached and photo_id not in stale_photo_ids:
            for face_idx, emb_blob in cached:
                # Protection A: skip corrupt BLOBs — propagating a
                # bad embedding into the phase pipeline crashes
                # downstream np.stack callers. A skipped row simply
                # gets re-extracted from source by phase 5.
                emb = decode_embedding(
                    emb_blob,
                    where="face_extraction_phases.cached_partition",
                )
                if emb is None:
                    continue
                cached_records.append((fp, photo_id, face_idx, emb))
        else:
            need_extract.append((i, fp, photo_id))
    return ExtractionPartition(cached_records=cached_records, need_extract=need_extract)


# ── Phase 4: stale embedding deleter ──


def delete_stale_embeddings(
    conn: sqlite3.Connection,
    need_extract: list[tuple[int, str, int]],
    stale_photo_ids: frozenset[int],
) -> int:
    """Phase 4: wipe NULL-quality rows for photos about to be re-extracted.

    Returns the number of photos whose rows were cleared. Without this,
    faces pruned by the quality gate on re-extract would linger as
    orphan rows (same (photo_id, face_index) as a non-pruned face but
    NULL quality), confusing the later cluster_id UPDATE join.
    """
    stale_to_extract = [pid for _i, _fp, pid in need_extract if pid in stale_photo_ids]
    if not stale_to_extract:
        return 0
    for start in range(0, len(stale_to_extract), 500):
        chunk = stale_to_extract[start : start + 500]
        placeholders = ",".join("?" * len(chunk))
        conn.execute(
            f"DELETE FROM face_embeddings WHERE photo_id IN ({placeholders}) "
            f"AND cluster_id != {CLUSTER_DISMISSED}",
            chunk,
        )
    conn.commit()
    log.info("Cleared %d stale face embedding records", len(stale_to_extract))
    return len(stale_to_extract)


# ── Phase 4.5: capture dismissed slots (read-only, part of snapshot) ──


def capture_dismissed_slots(
    conn: sqlite3.Connection,
    need_extract: list[tuple[int, str, int]],
) -> frozenset[tuple[int, int]]:
    """Snapshot ``(photo_id, face_index)`` slots currently marked dismissed.

    Done BEFORE extraction so the upcoming INSERT OR REPLACE doesn't
    silently erase the user's dismiss history. The orchestrator stamps
    this into :class:`PreExtractSnapshot` and the extraction phase
    restores them after its UPSERT loop.
    """
    extract_pids = {pid for _i, _fp, pid in need_extract}
    if not extract_pids:
        return frozenset()
    ph = ",".join("?" * len(extract_pids))
    slots = {
        (r[0], r[1])
        for r in conn.execute(
            f"SELECT photo_id, face_index FROM face_embeddings "
            f"WHERE photo_id IN ({ph}) AND cluster_id = {CLUSTER_DISMISSED}",
            list(extract_pids),
        ).fetchall()
    }
    if slots:
        log.info("Preserving %d dismissed face slots during re-extraction", len(slots))
    return frozenset(slots)


# ── Phase 5: extract new embeddings ──
#
# The dataclass + the per-photo extractor pool + the dismissed-slot
# restoration helper live in bpp.web.face_extraction_phase5. Re-
# exported here so the orchestrator and the dozen tests that touch
# them keep working via the face_extraction_phases module path.
from bpp.web.face_extraction_phase5 import (  # noqa: E402, F401
    ExtractionOutput,
    _restore_dismissed_and_filter,
    extract_new_embeddings,
)

# ── Phase 6: cluster faces ──
#
# The clustering pass (adaptive threshold + hard-negative filter +
# assign_new_faces_fn dispatch + cluster_id UPDATE) lives in
# bpp.web.face_extraction_phase6. Re-exported here for callers that
# import via the original phases module.
from bpp.web.face_extraction_phase6 import cluster_faces  # noqa: E402, F401

# ── Phase 7: reconstruct identities + refresh albums ──


def reconstruct_identities(
    conn: sqlite3.Connection,
    snapshot: PreExtractSnapshot,
    *,
    reconstruct_identities_fn: Callable[[sqlite3.Connection], None],
    remap_names_and_tags_fn: Callable[
        [sqlite3.Connection, dict[int, set[int]], dict[int, set[int]]], None
    ],
) -> None:
    """Phase 7: identity reconstruction + legacy remap + album refresh.

    Idempotent — recovery handler re-runs this phase verbatim. The
    smart-album refresh scope is intentionally narrow (only
    cluster-state-derived kinds) so the post-cluster refresh doesn't
    hold the WAL write lock for the full sweep cost on a 10k-photo
    library.

    Best-effort on the album refresh + ctx invalidation steps —
    surfacing these as exceptions would gate the entire phase return
    on background album-state correctness, which we'd rather log and
    recover from than crash.

    T1.4: ``reconstruct_identities_fn`` + ``remap_names_and_tags_fn``
    are wrapped in a SAVEPOINT so that a mid-loop failure rolls back
    any UPDATEs they issued before the exception. Pre-T1.4 the partial
    writes sat in the conn's implicit transaction — correct in the
    subprocess-worker path because process teardown discards them, but
    fragile for any future caller that runs phase 7 inline (test code,
    plugin hook, recovery retry on a long-lived conn). The SAVEPOINT
    makes the rollback contract local to the phase, independent of
    caller cleanup. The album refresh and ctx invalidation stay
    outside the SAVEPOINT because they're best-effort and already
    wrapped in their own try/except.
    """
    conn.execute("SAVEPOINT phase7_reconstruct")
    try:
        reconstruct_identities_fn(conn)

        # Legacy fallback: remap by photo-overlap if identity labels not available
        if snapshot.old_cluster_photos is not None:
            new_cluster_photos = _snapshot_cluster_photos(conn)
            remap_names_and_tags_fn(conn, snapshot.old_cluster_photos, new_cluster_photos)
    except Exception:
        # ROLLBACK TO SAVEPOINT undoes everything since SAVEPOINT but
        # leaves the savepoint on the stack; RELEASE pops it.
        # Either statement can raise OperationalError if the savepoint
        # is already gone — happens when the inner fn called conn.commit()
        # before raising (commit auto-releases all open savepoints).
        # In that case the partial work is already committed and we
        # can't roll it back; surface the original exception unchanged.
        with contextlib.suppress(sqlite3.OperationalError):
            conn.execute("ROLLBACK TO SAVEPOINT phase7_reconstruct")
            conn.execute("RELEASE SAVEPOINT phase7_reconstruct")
        raise
    else:
        # Same caveat as the except branch: if reconstruct_identities_fn
        # legitimately committed (the current _reconstruct_identities
        # does this), the savepoint is already gone. The RELEASE is a
        # no-op via the OperationalError suppress.
        with contextlib.suppress(sqlite3.OperationalError):
            conn.execute("RELEASE SAVEPOINT phase7_reconstruct")

    try:
        from bpp.db.smart_albums import get_affected_album_types, refresh_smart_albums

        refresh_smart_albums(
            conn,
            kinds=get_affected_album_types("face_cluster"),
        )
    except Exception:
        log.warning(
            "refresh_smart_albums after clustering failed",
            exc_info=True,
        )

    try:
        from bpp.web.state import get_ctx_or_none

        ctx = get_ctx_or_none()
        if ctx is not None:
            ctx.invalidate_face_cluster_map()
    except Exception:
        log.debug("face cluster map invalidation skipped", exc_info=True)

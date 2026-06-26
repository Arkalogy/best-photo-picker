"""Recovery handlers for the face-extraction journal kinds.

Extracted from :mod:`bpp.web.face_worker` as part of the LOC-cap
enforcement. Three concerns live here:

* :func:`recover_pending_face_extractions` — replays every
  ``face_extraction_journal`` row with ``completed_at IS NULL`` on
  startup. Each pending run_id flows back into
  :func:`bpp.web.face_worker.extract_and_cluster_faces` via
  ``resume_run_id`` so the orchestrator skips already-completed phases.
* :func:`register_face_clustering_recovery` — binds the recovery handler
  for the ``face_clustering`` operation-journal kind. Pending entry →
  re-run identity reconstruction + smart-album refresh.
* :func:`register_face_extraction_retry_recovery` — binds the recovery
  handler for the ``face_extraction_retry`` operation-journal kind.
  Pending entry → re-fire the face worker via the live ctx.

Both of the ``register_*`` functions are invoked once per process from
:mod:`bpp.web.state_lifecycle`. ``recover_pending_face_extractions``
runs from ``startup`` after the generic operation-journal recovery has
cleared.

The face_worker module keeps the orchestrator + ``FaceWorker`` class
and re-exports these three names for backward compatibility (a few
tests still import them via ``bpp.web.face_worker``).
"""

from __future__ import annotations

import sqlite3

from bpp.utils.logging import get_logger

log = get_logger(__name__)


def recover_pending_face_extractions(conn: sqlite3.Connection) -> int:
    """P3.5 — replay every pending row in face_extraction_journal.

    Called from ``state_lifecycle.startup`` after the operation_journal
    recovery has run. Each pending row's run_id is passed back into
    ``extract_and_cluster_faces`` via ``resume_run_id`` so the
    orchestrator skips already-completed phases and rehydrates the
    snapshot from the journal.

    Returns the count of runs resumed.

    Recovery is best-effort: if the live ctx has no analysis loaded
    (early startup before ``load_analysis_if_needed``), the row is
    left in place and recovery re-fires on the next startup. A
    persistent failure (corrupted journal, unloadable analysis) gets
    a WARNING log; the row stays pending until the user manually
    completes face extraction via Settings → Faces → Retry.
    """
    from bpp.web import face_extraction_journal as journal
    from bpp.web.face_worker import extract_and_cluster_faces
    from bpp.web.state import get_ctx_or_none

    pending = journal.pending_runs(conn)
    if not pending:
        return 0

    resumed = 0
    for entry in pending:
        run_id = entry["run_id"]
        # T0.4: bound retry attempts so a deterministic failure
        # (corrupt snapshot, unrecoverable phase-7 crash) doesn't loop
        # on every server restart forever. Pre-check before incrementing
        # so the count reflects "attempts so far," not "this attempt."
        prior_attempts = journal.get_retry_count(conn, run_id)
        if prior_attempts >= journal.MAX_RECOVERY_RETRIES:
            log.warning(
                "Recovery: run %s exceeded MAX_RECOVERY_RETRIES (%d). "
                "Force-completing with GAVE_UP_SENTINEL so future startups "
                "skip it. User can re-run via Settings → Faces → Retry.",
                run_id,
                journal.MAX_RECOVERY_RETRIES,
            )
            journal.force_complete_after_retries(conn, run_id)
            continue

        try:
            ctx = get_ctx_or_none()
            if ctx is None:
                log.warning(
                    "Recovery: no live ctx; leaving face_extraction_journal "
                    "row %s in place for next startup (attempts=%d)",
                    run_id,
                    prior_attempts,
                )
                continue
            analysis = ctx.load_analysis_if_needed()
            if not analysis:
                log.warning(
                    "Recovery: no analysis available; leaving "
                    "face_extraction_journal row %s pending (attempts=%d)",
                    run_id,
                    prior_attempts,
                )
                continue
            with_faces = [a for a in analysis if (a.get("face_count") or 0) > 0]
            if not with_faces:
                # Nothing to recover — close the row.
                journal.complete_run(conn, run_id)
                continue
            from bpp.db.photos import get_photo_id_map_by_paths

            photo_map = get_photo_id_map_by_paths(conn, [a["filepath"] for a in with_faces])
            max_long_side = ctx.config.get("max_long_side", 1024)
            face_conf = float(ctx.config.get("face_detection_confidence", 0.3))
            # Increment BEFORE the attempt so a crash mid-attempt still
            # counts. The next startup sees the incremented count and
            # gets one fewer retry.
            journal.increment_retry_count(conn, run_id)
            extract_and_cluster_faces(
                conn,
                with_faces,
                photo_map,
                max_long_side,
                face_conf,
                dict(ctx.config),
                resume_run_id=run_id,
            )
            resumed += 1
            log.info("Recovery: resumed face extraction run %s to completion", run_id)
        except Exception:
            log.warning(
                "Recovery: face extraction run %s failed mid-resume "
                "(attempts=%d of %d) — leaving journal row pending",
                run_id,
                journal.get_retry_count(conn, run_id),
                journal.MAX_RECOVERY_RETRIES,
                exc_info=True,
            )
    return resumed


def register_face_clustering_recovery() -> None:
    """Bind a recovery handler for the 'face_clustering' journal kind.

    Pending entry → re-run identity reconstruction + smart album
    refresh. Both are idempotent so it's safe to run unconditionally.
    Stateless: doesn't need a closure over ctx.
    """
    from bpp.db.journal import register_recovery_handler

    def _recover(conn: sqlite3.Connection, _payload: dict) -> bool:
        # Late import — face_worker re-exports _reconstruct_identities,
        # but importing it at module-load time would import everything
        # in face_worker (orchestrator, FaceWorker class, etc.) eagerly.
        from bpp.web.face_worker import _reconstruct_identities

        log.info(
            "Recovering interrupted face clustering — re-running identity + smart-album refresh"
        )
        try:
            _reconstruct_identities(conn)
        except Exception:
            log.warning("_reconstruct_identities during recovery failed", exc_info=True)
        try:
            from bpp.db.smart_albums import get_affected_album_types, refresh_smart_albums

            # L5: same scope as the post-clustering refresh in
            # _extract_faces — only cluster-state-derived kinds.
            refresh_smart_albums(
                conn,
                kinds=get_affected_album_types("face_cluster"),
            )
        except Exception:
            log.warning("refresh_smart_albums during recovery failed", exc_info=True)
        return True

    register_recovery_handler("face_clustering", _recover, replace=True)


def register_face_extraction_retry_recovery(library_path: str | None = None) -> None:
    """Recovery handler for `face_extraction_retry` journal kind.

    Triggered when a user hits POST /api/v1/faces/retry, which wipes
    `face_embeddings` + `photo_person_tags` and starts a fresh
    extraction. If the server SIGKILLs / SIGSEGVs / OOM's between the
    wipe and the worker completing, the user's face data is gone with
    no automatic recovery — they'd have to click "Retry" again and
    wait through the whole extraction (which last benchmarked at
    ~50 minutes for a 6k-photo library).

    On startup, the handler picks up the pending entry and re-fires
    the face-extraction worker via the live ctx. The worker is
    idempotent (cached embeddings get reused, missing ones get
    extracted) so it's safe to call unconditionally even if some
    photos already have embeddings from a partial completion.

    Returns False on best-effort failure to leave the journal entry in
    place — the user can hit Retry manually if recovery couldn't
    re-fire the worker (e.g. analysis data missing). False is the
    right answer there because deleting the breadcrumb would silently
    lose the "face data is in a half-state" signal.

    P1 — ``library_path`` (optional, but passed by the state lifecycle):
    when provided, the handler is wrapped with
    :func:`bpp.db.journal.library_bound_recovery` so a switch_library
    between registration and recovery refuses to fire the wrong
    library's worker.
    """
    from bpp.db.journal import library_bound_recovery, register_recovery_handler

    def _recover(_conn: sqlite3.Connection, payload: dict) -> bool:
        log.info(
            "Recovering interrupted face-extraction retry (started_via=%s) — re-firing face worker",
            payload.get("started_via", "?"),
        )
        # Late import to avoid circular state↔face_worker dependency.
        from bpp.web.state import get_ctx_or_none

        ctx = get_ctx_or_none()
        if ctx is None:
            log.warning(
                "Recovery: no live ctx; leaving face_extraction_retry "
                "entry in place. User can hit Retry manually."
            )
            return False
        analysis = ctx.load_analysis_if_needed()
        if analysis is None:
            log.warning(
                "Recovery: no analysis data; leaving face_extraction_retry "
                "entry in place. Run analyze first, then hit Retry."
            )
            return False
        if ctx.face_worker.is_alive:
            # Worker already running — let it finish. The journal entry
            # gets cleared by the worker's own success path.
            log.info("Recovery: face worker already running; skipping re-fire")
            return True
        started = ctx.face_worker.start(analysis, ctx.db_path(), ctx.config)
        if not started:
            log.warning("Recovery: face_worker.start returned False; leaving entry")
            return False
        return True

    if library_path is not None:

        def _live_library_path() -> str | None:
            from bpp.web.state import get_ctx_or_none

            _ctx = get_ctx_or_none()
            return _ctx.paths.library_path if _ctx is not None else None

        handler = library_bound_recovery(
            library_path, _recover, library_path_getter=_live_library_path
        )
    else:
        handler = _recover
    register_recovery_handler("face_extraction_retry", handler, replace=True)

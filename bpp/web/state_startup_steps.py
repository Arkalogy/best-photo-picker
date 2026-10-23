"""Start-up steps of a library that run beside :func:`bpp.web.state_lifecycle.startup`:
the face-extraction resume thread, the lightweight thumbnail map, the
pending-journal replay, the file-health scan and its refused-scan
callback. Split out of ``state_lifecycle.py`` for the 500-line cap.
"""

from __future__ import annotations

import contextlib
import threading
from typing import TYPE_CHECKING

from bpp.utils.logging import get_logger
from bpp.web.thumbnails import ThumbnailCache

if TYPE_CHECKING:
    from bpp.web.state import WebAppState

log = get_logger(__name__)


def _start_face_recovery(ctx: WebAppState) -> None:
    """Resume interrupted face extractions on a background thread. The
    resume can take minutes on a large library; startup and library
    switches must not wait for it. State is on ``ctx.analysis_store``
    (``face_recovery_running`` / ``face_recovery_failed``, shown by
    /api/v1/health); ``join_threads`` cancels and joins it. Nothing is
    spawned (and nothing reads as in flight) when no run is pending."""
    from bpp.web import face_extraction_journal as journal

    try:
        pending = journal.pending_runs(ctx.get_conn())
    except Exception:
        log.warning("Couldn't read the face-extraction journal", exc_info=True)
        pending = []
    if not pending:
        return
    if ctx.face_worker.is_alive:
        # A face extraction started by a journal replay is already
        # writing the face tables; it completes the same work. The run
        # stays pending and resumes at the next startup if it must.
        log.warning("Face extraction is already running; the resume waits for the next startup")
        return
    store = ctx.analysis_store
    store.face_recovery_cancel = threading.Event()
    store.face_recovery_failed = False
    store.face_recovery_running = True
    generation = store.phash_generation
    cancel = store.face_recovery_cancel

    def _run() -> None:
        # A thread that outlived the join on a library switch must not
        # write its outcome over the next library's flags.
        me = threading.current_thread()
        try:
            from bpp.web.face_worker import recover_pending_face_extractions

            # The resume owns the face tables while it runs: a mutation
            # already in flight finishes first, and every face-mutating
            # endpoint (``with_face_lock``), analyze and import refuse
            # with 409 until ``face_recovery_running`` clears.
            with ctx.face_op_lock:
                # Parked behind a long mutation across a library switch:
                # the app state now belongs to another library.
                if store.face_recovery_thread is not me or store.phash_generation != generation:
                    log.info("Face-extraction resume skipped: the library changed while it waited")
                    return
                n = recover_pending_face_extractions(ctx.get_conn(), ctx=ctx, cancel_event=cancel)
            if n:
                log.info("Recovered %d face-extraction journal row(s)", n)
        except Exception:
            if store.face_recovery_thread is me:
                store.face_recovery_failed = True
            log.warning("face_extraction_journal recovery failed", exc_info=True)
        finally:
            if store.face_recovery_thread is me:
                store.face_recovery_running = False

    log.info("Face-extraction resume starting: %d pending run(s)", len(pending))
    t = threading.Thread(target=_run, name="face-recovery", daemon=True)
    store.face_recovery_thread = t
    t.start()


def _init_thumbs_lightweight(ctx: WebAppState) -> None:
    """Initialize thumbnail cache from a lightweight filepath-only DB query.

    Much faster than load_analysis_if_needed() which loads all columns.
    Always initializes ctx.thumbs (even for empty libraries) so it's never None.
    """
    try:
        ctx.thumbs = ThumbnailCache(ctx.dirs["thumbs"])
        conn = ctx.get_conn()
        rows = conn.execute("SELECT filepath FROM photos WHERE missing=0").fetchall()
        filepaths = [r["filepath"] for r in rows]
        if filepaths:
            ctx.thumbs.build_map_from_paths(filepaths)
            log.info("Thumbnail map built for %d photos", len(filepaths))
            # Pre-generate thumbnails in background so album views load instantly.
            # Tracked + cancel-aware so switch_library can drain it cleanly.
            ctx.analysis_store.cancel_warm.clear()

            def _warm():
                # Cache the thumbs reference at thread-start time so
                # the warmer never writes into a *different* library's
                # ctx.thumbs after a switch (paranoia: switch_library
                # also signals cancel, but defense in depth).
                target = ctx.thumbs
                if target is None:
                    return
                try:
                    n = target.warm_cache(cancel_event=ctx.analysis_store.cancel_warm)
                except Exception:
                    log.warning("Thumbnail cache warming stopped", exc_info=True)
                    return
                if n:
                    log.info("Thumbnail cache warmed: %d generated", n)

            ctx.analysis_store.warm_thread = threading.Thread(target=_warm, daemon=True)
            ctx.analysis_store.warm_thread.start()
    except Exception:
        log.warning("Failed to init thumbnail cache", exc_info=True)
        if ctx.thumbs is None:
            with contextlib.suppress(Exception):
                ctx.thumbs = ThumbnailCache(ctx.dirs["thumbs"])


def _recover_pending_journals(ctx: WebAppState) -> None:
    """Run all registered recovery handlers against pending entries."""
    from bpp.db.journal import recover_pending

    try:
        recover_pending(ctx.get_conn())
    except Exception:
        # Recovery is best-effort; never block app startup on it.
        log.warning("Journal recovery failed", exc_info=True)


def _start_file_health_checks(ctx: WebAppState) -> None:
    """Launch background threads for missing-file detection and periodic sampling."""
    from bpp.web.health import start_health_checks

    ctx._health_handle = start_health_checks(
        ctx.get_conn,
        ctx.dirs,
        ctx.invalidate_analysis,
        on_scan_refused=make_scan_refused_callback(ctx),
    )


def make_scan_refused_callback(ctx: WebAppState):
    """The health thread's way to report a refused missing-file scan.
    Bound to the library open when the thread started: a scan that
    outlives a library switch must not stamp its verdict on the next
    library (same guard as the hash backfill's generation)."""
    store = ctx.analysis_store
    generation = store.phash_generation

    def _scan_refused(refused: tuple[int, int] | None) -> None:
        if store.phash_generation == generation:
            store.missing_scan_refused = refused

    return _scan_refused

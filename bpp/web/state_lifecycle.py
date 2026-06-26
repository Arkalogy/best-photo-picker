"""WebAppState lifecycle helpers — startup, shutdown, library switch,
thumbnail bootstrap, and journal-recovery wiring.

Extracted from state.py during the v0.1 cleanup so the host module
stays under the 500-LOC soft cap. Each helper takes the live
:class:`WebAppState` as its first argument and mutates it in place.

Not part of the public API — :class:`WebAppState` delegates from its
``startup() / shutdown() / switch_library()`` thin wrappers.
"""

from __future__ import annotations

import contextlib
import threading
from dataclasses import replace
from typing import TYPE_CHECKING

from bpp.constants import WORKER_JOIN_TIMEOUT_S
from bpp.db.connection import close_all_connections
from bpp.utils.logging import get_logger
from bpp.web.thumbnails import ThumbnailCache

if TYPE_CHECKING:
    from bpp.web.state import WebAppState

log = get_logger(__name__)


def switch_library(ctx: WebAppState, new_path: str) -> None:
    """Hot-swap to a different library, cancelling any running workers first.

    T1.3: serialized end-to-end by ``ctx._switch_library_lock``. The
    plugin close hooks, worker cancel-and-join, AnalysisStore drain,
    and DB pool close all happen OUTSIDE the (briefly-held) ``ctx.lock``,
    so without serialization two concurrent ``switch_library`` calls —
    e.g. one from the Tauri sidecar and one from a Flask endpoint —
    would interleave their drains and double-fire close hooks. The
    lock is per-ctx, not module-global, so test fixtures that build
    standalone ctx instances don't contend with the production one.
    """
    import os

    from bpp.db.library import ensure_library_dirs

    with ctx._switch_library_lock:
        new_path = os.path.abspath(new_path)
        new_dirs = ensure_library_dirs(new_path)
        _switch_library_locked(ctx, new_path, new_dirs)


def _switch_library_locked(ctx: WebAppState, new_path: str, new_dirs: dict[str, str]) -> None:
    """Body of :func:`switch_library`, called with
    ``ctx._switch_library_lock`` already held. Split out so the lock
    boundary is the only thing the public function does.
    """

    # P5b: notify lifecycle plugins BEFORE we touch any per-library
    # state so they can release resources tied to the outgoing library
    # (DB connections, side-caches, model handles).
    try:
        from bpp.plugin_protocol import fire_on_library_close

        fire_on_library_close(ctx)
    except Exception:
        log.warning("plugin_protocol.fire_on_library_close failed", exc_info=True)

    # Cancel all running workers and wait for them to finish
    # P4: single canonical cancel-all path lives on WorkerPool.
    ctx.workers.cancel_and_join_all(timeout=WORKER_JOIN_TIMEOUT_S)

    # Stop health-check threads and wait for them to release their
    # thread-local DB connections before switching databases.
    if hasattr(ctx, "_health_handle"):
        ctx._health_handle.stop_and_join(timeout=WORKER_JOIN_TIMEOUT_S)

    # P4b: signal + drain background daemon threads through the
    # AnalysisStore collaborator. The warmer polls cancel_warm
    # between iterations; the phash compute thread has no cancel hook
    # (worth adding later) but join_threads waits for its handle so
    # we know it's done before reusing the DB connection.
    ctx.analysis_store.join_threads(timeout=WORKER_JOIN_TIMEOUT_S)

    with ctx.lock:
        close_all_connections()
        ctx.state["analysis"] = None
        # update both the structured paths and the legacy
        # state dict so callers reading either form see consistent
        # values during and after the switch.
        # LibraryPaths is frozen, so replace
        # the whole instance under the lock.
        ctx.paths = replace(
            ctx.paths,
            workdir=new_dirs["data"],
            library_path=new_path,
            dirs=new_dirs,
        )
        ctx.state["workdir"] = new_dirs["data"]
        ctx.state["library_path"] = new_path
        # keep `ctx.dirs` consistent with the immutable
        # mapping `ctx.paths.dirs` now wraps. Pre-fix this
        # assigned the raw dict directly, so callers could
        # mutate `ctx.dirs` even when `ctx.paths.dirs` was
        # frozen — same desync hazard the freeze was meant to
        # close.
        ctx.dirs = ctx.paths.dirs
        ctx.thumbs = None
        ctx.caches.clip_cache = {"embeddings": {}, "ready": False}
        # P4b: atomic bump + reset on the AnalysisStore (replaces the
        # two-step ``phash_ready = Event()`` + generation+=1 dance).
        ctx.analysis_store.bump_generation_and_reset_phash()
        # Three-review M-S2: reset the Phase 5 health flag so a
        # late-finishing daemon from the OUTGOING library can't poison
        # the INCOMING library's /api/v1/health with a stale
        # 'smart-album counts may be stale' degraded status. The new
        # init_app_db about to fire below will spawn its own daemon
        # which sets this back to False on entry anyway, but resetting
        # here closes the race-window where the old daemon could
        # .set(True) AFTER the new daemon completed cleanly.
        ctx.phase5_failed = False
    ctx.startup()


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
                n = target.warm_cache(cancel_event=ctx.analysis_store.cancel_warm)
                if n:
                    log.info("Thumbnail cache warmed: %d generated", n)

            ctx.analysis_store.warm_thread = threading.Thread(target=_warm, daemon=True)
            ctx.analysis_store.warm_thread.start()
    except Exception:
        log.warning("Failed to init thumbnail cache", exc_info=True)
        if ctx.thumbs is None:
            with contextlib.suppress(Exception):
                ctx.thumbs = ThumbnailCache(ctx.dirs["thumbs"])


def startup(ctx: WebAppState) -> None:
    """Run initialization sequence: DB setup, purge, init thumbs.

    Calls back through the class methods (``ctx._init_thumbs_lightweight()``
    etc.) so tests that monkey-patch a method on the class keep working.
    """
    ctx.init_app_db()
    ctx.auto_purge()
    # Initialize thumbnail cache from lightweight filepath query (fast).
    # Full analysis loads lazily on first recompute/scoring request.
    ctx._init_thumbs_lightweight()
    # Crash recovery: replay any pending operation_journal entries
    # left behind by a SIGKILL/crash mid-flight (permanent_delete,
    # face clustering, CLIP extraction). Handlers are bound here
    # because they need closure over the live ctx.
    ctx._register_journal_recovery_handlers()
    ctx._recover_pending_journals()
    # P3.5: replay any face_extraction_journal rows that didn't
    # complete. Distinct from the operation_journal handlers above
    # because the per-phase resume needs the run_id, not just a
    # payload-driven re-fire. Best-effort: failures are logged but
    # don't block startup.
    try:
        from bpp.web.face_worker import recover_pending_face_extractions

        n = recover_pending_face_extractions(ctx.get_conn())
        if n:
            log.info("Recovered %d face-extraction journal row(s) on startup", n)
    except Exception:
        log.warning("face_extraction_journal recovery failed", exc_info=True)
    # Start background file-health checks (non-blocking, serve mode only).
    if ctx.serve_mode:
        ctx._start_file_health_checks()
    # P5b: notify lifecycle plugins that a library is open and ready.
    # Best-effort — a misbehaving plugin can't block startup.
    try:
        from bpp.plugin_protocol import fire_on_library_open

        fire_on_library_open(ctx)
    except Exception:
        log.warning("plugin_protocol.fire_on_library_open failed", exc_info=True)


def _register_journal_recovery_handlers(ctx: WebAppState) -> None:
    """Bind per-kind recovery handlers with closures over ctx.

    Called every startup() — after a library switch, the closures
    capture the new ctx, so we use replace=True to rebind.

    P1: each registration site now also captures the current library
    path. Handlers that close over ``ctx`` route through
    :func:`bpp.db.journal.library_bound_recovery` so they refuse to fire
    if ctx has been switched to a different library between registration
    and recovery. This closes the audit-found data-corruption hole where
    a pending recovery from library A could write into library B mid-
    ``switch_library``.
    """
    # Local imports to avoid circulars: these modules import state.
    from bpp.web.bp_photos_manage import register_permanent_delete_recovery
    from bpp.web.clip_worker import register_clip_extraction_recovery
    from bpp.web.face_worker import (
        register_face_clustering_recovery,
        register_face_extraction_retry_recovery,
    )

    library_path = ctx.paths.library_path

    register_permanent_delete_recovery(ctx, library_path=library_path)
    register_face_clustering_recovery()  # stateless; uses conn only
    register_face_extraction_retry_recovery(library_path=library_path)
    register_clip_extraction_recovery()  # no-op; safe to fire on either lib


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

    ctx._health_handle = start_health_checks(ctx.get_conn, ctx.dirs, ctx.invalidate_analysis)


def shutdown(ctx: WebAppState) -> None:
    """Cancel workers and close DB connections for clean shutdown."""
    # P5b: fire on_library_close + on_shutdown plugin hooks so any
    # per-library resources owned by plugins get released cleanly.
    # Each hook is wrapped separately so a failure in on_library_close
    # doesn't prevent on_shutdown from running — both are best-effort
    # cleanup paths.
    from bpp.plugin_protocol import (
        fire_on_library_close,
        fire_on_shutdown,
    )

    try:
        fire_on_library_close(ctx)
    except Exception:
        log.warning("plugin_protocol.fire_on_library_close failed", exc_info=True)
    try:
        fire_on_shutdown()
    except Exception:
        log.warning("plugin_protocol.fire_on_shutdown failed", exc_info=True)
    # P4: single canonical cancel-all path lives on WorkerPool.
    ctx.workers.cancel_and_join_all(timeout=WORKER_JOIN_TIMEOUT_S)
    if hasattr(ctx, "_health_handle"):
        ctx._health_handle.stop_and_join(timeout=WORKER_JOIN_TIMEOUT_S)
    # P4b: same drain dance as switch_library, now a single call.
    ctx.analysis_store.join_threads(timeout=WORKER_JOIN_TIMEOUT_S)
    close_all_connections()

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
import os
import secrets
from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING

from bpp.constants import WORKER_JOIN_TIMEOUT_S
from bpp.db.connection import close_all_connections
from bpp.utils.logging import get_logger
from bpp.web.state_startup_steps import (  # noqa: F401  (re-exported: tests and state.py import them from here)
    _init_thumbs_lightweight,
    _recover_pending_journals,
    _start_face_recovery,
    _start_file_health_checks,
    make_scan_refused_callback,
)
from bpp.web.thumbnails import ThumbnailCache

if TYPE_CHECKING:
    from bpp.web.state import WebAppState

log = get_logger(__name__)


def switch_library(ctx: WebAppState, new_path: str) -> None:
    """Hot-swap to a different library, cancelling any running workers first.

    Serialized end-to-end by ``ctx._switch_library_lock``. The
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
    _drain_current_library(ctx)
    _adopt_library(ctx, new_path, new_dirs, new_dirs["data"])


class RenameIncompleteError(RuntimeError):
    """The folder moved and the session follows it, but the stored photo
    paths or the caches could not be updated."""


def rename_open_library(ctx: WebAppState, new_path: str, move: Callable[[], None]) -> None:
    """Rename the open library's folder without leaving the running
    session behind: drain it as for a switch, run ``move`` (the
    ``os.rename``), rewrite the stored photo paths, move the cached
    derivatives to their new names, then adopt the new paths and start
    up again. If ``move`` raises, the old library is started again and
    the error propagates; once the folder has moved, the session always
    ends up on the new path. The photo rows hold absolute paths under the
    library root, so without the rewrite every file is unreachable after
    the rename and the health check marks the whole library missing."""
    with ctx._switch_library_lock:
        old_path = os.path.abspath(ctx.library_path)
        new_path = os.path.abspath(new_path)
        old_workdir = ctx.workdir
        old_dirs = dict(ctx.dirs or {})
        _drain_current_library(ctx)
        with ctx.lock:
            close_all_connections()  # this thread's handles; the pool is thread-local
        try:
            move()
        except Exception:
            _adopt_library(ctx, old_path, old_dirs, old_workdir)
            raise

        def _swap(path: str) -> str:
            return new_path + path[len(old_path) :] if path.startswith(old_path) else path

        new_workdir = _swap(old_workdir)
        new_dirs = {k: _swap(v) for k, v in old_dirs.items()}
        follow_error: Exception | None = None
        try:
            pairs = _rewrite_photo_paths(new_workdir, old_path, new_path)
            _move_cache_files(new_dirs, pairs)
        except Exception as exc:
            follow_error = exc
            log.error("Photo paths or caches could not follow the renamed library", exc_info=True)
        _adopt_library(ctx, new_path, new_dirs, new_workdir)
        if follow_error is not None:
            raise RenameIncompleteError(
                "The folder was renamed, but its photo paths or caches could not follow; "
                "restart the app and run the missing-file recheck"
            ) from follow_error


def _rewrite_photo_paths(workdir: str, old_root: str, new_root: str) -> list[tuple[str, str]]:
    """Point every photo row under ``old_root`` at ``new_root``; returns
    the ``(old_path, new_path)`` pairs so the caches can follow."""
    from bpp.db.connection import get_db

    db_path = os.path.join(workdir, "photopicker.db")
    if not os.path.exists(db_path):
        return []
    old_prefix = old_root.rstrip(os.sep) + os.sep
    new_prefix = new_root.rstrip(os.sep) + os.sep
    conn = get_db(db_path)
    rows = conn.execute(
        "SELECT filepath FROM photos WHERE substr(filepath, 1, ?) = ?",
        (len(old_prefix), old_prefix),
    ).fetchall()
    pairs = [(r[0], new_prefix + r[0][len(old_prefix) :]) for r in rows]
    conn.execute(
        "UPDATE photos SET filepath = ? || substr(filepath, ?) WHERE substr(filepath, 1, ?) = ?",
        (new_prefix, len(old_prefix) + 1, len(old_prefix), old_prefix),
    )
    conn.commit()
    log.info("Rewrote %d photo paths from %s to %s", len(pairs), old_root, new_root)
    return pairs


def _move_cache_files(dirs: dict[str, str], pairs: list[tuple[str, str]]) -> None:
    """Cached thumbnails, full-size variants and face / pet crops are
    named by a hash of the photo's path; after a rename they would all be
    orphaned and regenerated. Rename them to the new hashes instead."""

    if not pairs:
        return
    remap = {ThumbnailCache._path_hash(old): ThumbnailCache._path_hash(new) for old, new in pairs}
    width = len(next(iter(remap)))
    moved = 0
    for key, prefix in (("thumbs", ""), ("face_crops", ""), ("pet_crops", "pet_")):
        folder = dirs.get(key)
        if not folder or not os.path.isdir(folder):
            continue
        for name in os.listdir(folder):
            if not name.startswith(prefix):
                continue
            new_hash = remap.get(name[len(prefix) : len(prefix) + width])
            if new_hash is None:
                continue
            new_name = prefix + new_hash + name[len(prefix) + width :]
            try:
                os.replace(os.path.join(folder, name), os.path.join(folder, new_name))
                moved += 1
            except OSError as exc:
                log.warning("Couldn't move cache file %s: %s", name, exc)
    log.info("Moved %d cache files to the renamed library's hashes", moved)


def _drain_current_library(ctx: WebAppState) -> None:
    """Stop everything that holds the current library: plugin close
    hooks, workers, health threads, the analysis store's threads."""
    # Notify lifecycle plugins BEFORE we touch any per-library
    # state so they can release resources tied to the outgoing library
    # (DB connections, side-caches, model handles).
    try:
        from bpp.plugin_protocol import fire_on_library_close

        fire_on_library_close(ctx)
    except Exception:
        log.warning("plugin_protocol.fire_on_library_close failed", exc_info=True)
    # The recompute cache pins the outgoing library's candidate list and
    # its CLIP embeddings (hundreds of MB at the cap).
    from bpp.web.recompute import dedupe_cache_clear

    dedupe_cache_clear()

    # Cancel all running workers and wait for them to finish
    # Single canonical cancel-all path lives on WorkerPool.
    ctx.workers.cancel_and_join_all(timeout=WORKER_JOIN_TIMEOUT_S)

    # Stop health-check threads and wait for them to release their
    # thread-local DB connections before switching databases.
    if hasattr(ctx, "_health_handle"):
        ctx._health_handle.stop_and_join(timeout=WORKER_JOIN_TIMEOUT_S)

    # Signal + drain background daemon threads through the
    # AnalysisStore collaborator. The warmer polls cancel_warm
    # between iterations; the phash compute thread has no cancel hook
    # (worth adding later) but join_threads waits for its handle so
    # we know it's done before reusing the DB connection.
    ctx.analysis_store.join_threads(timeout=WORKER_JOIN_TIMEOUT_S)


def _invalidate_caches(ctx: WebAppState) -> None:
    """Drop every per-library cache (face map, edited / enhanced ids)."""
    invalidate = getattr(ctx.caches, "invalidate_all", None)
    if invalidate is not None:
        invalidate()


def _adopt_library(ctx: WebAppState, new_path: str, new_dirs: dict[str, str], workdir: str) -> None:
    """Point the drained ``ctx`` at ``new_path`` and start it up."""

    old = (
        ctx.paths,
        ctx.state.get("workdir"),
        ctx.state.get("library_path"),
        getattr(ctx, "library_id", None),
    )
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
            workdir=workdir,
            library_path=new_path,
            dirs=new_dirs,
        )
        ctx.state["workdir"] = workdir
        ctx.state["library_path"] = new_path
        ctx.library_id = secrets.token_hex(8)
        # keep `ctx.dirs` consistent with the immutable
        # mapping `ctx.paths.dirs` now wraps. Pre-fix this
        # assigned the raw dict directly, so callers could
        # mutate `ctx.dirs` even when `ctx.paths.dirs` was
        # frozen — same desync hazard the freeze was meant to
        # close.
        ctx.dirs = ctx.paths.dirs
        ctx.thumbs = None
        ctx.caches.clip_cache = {"embeddings": {}, "ready": False}
        # The face map and the edited / enhanced id sets are per library
        # too; photo ids overlap across libraries.
        _invalidate_caches(ctx)
        # Atomic bump + reset on the AnalysisStore (replaces the
        # two-step ``phash_ready = Event()`` + generation+=1 dance).
        ctx.analysis_store.bump_generation_and_reset_phash()
        # Reset the Phase 5 health flag so a
        # late-finishing daemon from the OUTGOING library can't poison
        # the INCOMING library's /api/v1/health with a stale
        # 'smart-album counts may be stale' degraded status. The new
        # init_app_db about to fire below will spawn its own daemon
        # which sets this back to False on entry anyway, but resetting
        # here closes the race-window where the old daemon could
        # .set(True) AFTER the new daemon completed cleanly.
        ctx.phase5_failed = False
    try:
        ctx.startup()
    except BaseException:
        # The target refused to open (another process serves it, a newer
        # schema, a corrupt file): go back to the library we had, fully
        # started, instead of leaving requests pointed at the refused one.
        log.warning("Switch to %s failed; restoring the previous library", new_path, exc_info=True)
        # Phase 1 may have taken the refused library's serving lock
        # before a later phase refused it: give it back, or the next
        # process to open that library is told we are serving it.
        from bpp.utils.serving_lock import clear_lock

        with contextlib.suppress(Exception):
            clear_lock(workdir)
        paths, workdir, library_path, library_id = old
        with ctx.lock:
            close_all_connections()
            ctx.paths = paths
            ctx.state["workdir"] = workdir
            ctx.state["library_path"] = library_path
            ctx.state["analysis"] = None
            ctx.dirs = ctx.paths.dirs
            ctx.thumbs = None
            ctx.caches.clip_cache = {"embeddings": {}, "ready": False}
            _invalidate_caches(ctx)
            ctx.library_id = library_id
        try:
            ctx.startup()
        except Exception:
            log.error("Could not restart the previous library either", exc_info=True)
        raise


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
    # Replay any face_extraction_journal rows that didn't
    # complete. Distinct from the operation_journal handlers above
    # because the per-phase resume needs the run_id, not just a
    # payload-driven re-fire. Best-effort: failures are logged but
    # don't block startup.
    _start_face_recovery(ctx)
    from bpp.web.install_status import log_interrupted_installs

    log_interrupted_installs()
    # Start background file-health checks (non-blocking, serve mode only).
    if ctx.serve_mode:
        ctx._start_file_health_checks()
    # Notify lifecycle plugins that a library is open and ready.
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

    Each registration site now also captures the current library
    path. Handlers that close over ``ctx`` route through
    :func:`bpp.db.journal.library_bound_recovery` so they refuse to fire
    if ctx has been switched to a different library between registration
    and recovery. This closes the data-corruption hole where
    a pending recovery from library A could write into library B mid-
    ``switch_library``.
    """
    # Local imports to avoid circulars: these modules import state.
    from bpp.web.clip_worker import register_clip_extraction_recovery
    from bpp.web.face_worker import (
        register_face_clustering_recovery,
        register_face_extraction_retry_recovery,
    )
    from bpp.web.permanent_delete import register_permanent_delete_recovery

    library_path = ctx.paths.library_path

    register_permanent_delete_recovery(ctx, library_path=library_path)
    register_face_clustering_recovery()  # stateless; uses conn only
    register_face_extraction_retry_recovery(library_path=library_path)
    register_clip_extraction_recovery()  # no-op; safe to fire on either lib


def shutdown(ctx: WebAppState) -> None:
    """Cancel workers and close DB connections for clean shutdown."""
    # Fire on_library_close + on_shutdown plugin hooks so any
    # per-library resources owned by plugins get released cleanly.
    # Each hook is wrapped separately so a failure in on_library_close
    # doesn't prevent on_shutdown from running — both are best-effort
    # cleanup paths.
    from bpp.plugin_protocol import (
        fire_on_library_close,
        fire_on_shutdown,
    )
    from bpp.web.pip_process import stop_running_pip

    stop_running_pip()  # it may be writing packages the next start loads

    try:
        fire_on_library_close(ctx)
    except Exception:
        log.warning("plugin_protocol.fire_on_library_close failed", exc_info=True)
    try:
        fire_on_shutdown()
    except Exception:
        log.warning("plugin_protocol.fire_on_shutdown failed", exc_info=True)
    # Single canonical cancel-all path lives on WorkerPool.
    ctx.workers.cancel_and_join_all(timeout=WORKER_JOIN_TIMEOUT_S)
    if hasattr(ctx, "_health_handle"):
        ctx._health_handle.stop_and_join(timeout=WORKER_JOIN_TIMEOUT_S)
    # Same drain dance as switch_library, now a single call.
    ctx.analysis_store.join_threads(timeout=WORKER_JOIN_TIMEOUT_S)
    close_all_connections()

"""Ordered derived-state recovery pipeline.

One background job, one order (each step logged start/end — Nothing
Silent rule): perceptual-hash backfill -> Live Photo sidecar tagging ->
all-photos sync -> near-duplicate clustering -> Moment clustering ->
smart-album refresh.

Extracted from state_init.py (LOC gate, 2026-06-12) — the same change
that turned the steps from independent racing triggers into this
pipeline (the wipe incident: clustering could fire against NULL hashes,
"recover" to garbage, and never re-run). ``precompute_phashes`` keeps
its historical name + re-export from state_init because it's the
canonical trigger every caller (analyze worker, startup, the
compute-hashes endpoint) already uses; it now runs the WHOLE pipeline,
with a re-entrancy guard that queues one re-run instead of racing.
"""

from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING, Any

from bpp.db.connection import get_db
from bpp.db.photos import update_hashes as db_update_hashes
from bpp.dedupe.phash import compute_hashes_from_file
from bpp.utils.logging import get_logger

if TYPE_CHECKING:
    from bpp.web.state import WebAppState

log = get_logger(__name__)

_PHASH_BACKFILL_WORKERS = min(2, os.cpu_count() or 1)


def precompute_phashes(ctx: WebAppState, data: list[dict[str, Any]]) -> None:
    """Run the derived-state recovery pipeline in a background thread.

    Ordered steps (each logged): hash backfill -> Live Photo sidecar
    tagging -> all-photos sync -> near-duplicate clustering -> Moment
    clustering -> smart-album refresh. One job, one order — the steps
    used to run as independent racing triggers (the wipe incident).
    """
    # Re-entrancy guard: a recovery is already running — queue ONE
    # re-run with the freshest data instead of racing a second thread
    # over the same rows. The current-thread check lets the running
    # pipeline respawn ITSELF for a queued re-run at its tail.
    t = ctx.analysis_store.compute_thread
    if t is not None and t.is_alive() and t is not threading.current_thread():
        log.info("Derived recovery already running — queueing a re-run")
        ctx.analysis_store.recovery_rerun = data
        return

    missing_fps = [
        item["filepath"] for item in data if item.get("phash") is None or item.get("ahash") is None
    ]
    if not missing_fps:
        ctx.analysis_store.phash_ready.set()
        return

    ctx.analysis_store.phash_ready.clear()
    # capture generation + Event identity at spawn time.
    # If `switch_library` increments the generation (and replaces
    # `ctx.analysis_store.phash_ready` with a fresh Event) while this thread is
    # still running, the closure will hold the OLD identifiers
    # and refuse to write into the new library's state.
    spawn_generation = ctx.analysis_store.phash_generation
    spawn_event = ctx.analysis_store.phash_ready

    def _compute():
        log.info("Pre-computing hashes for %d images...", len(missing_fps))
        from concurrent.futures import ThreadPoolExecutor, as_completed

        db_path = ctx.state.get("workdir", "")
        if db_path:
            db_path = os.path.join(db_path, "photopicker.db")
        # Use the pool — get_db already applies WAL + 30s busy_timeout
        # and is thread-local, so this thread gets its own connection.
        conn = get_db(db_path)

        # Parallel hash computation across a SMALL pool. Each worker
        # decodes a full-resolution image (decode releases the GIL, so
        # this is genuinely parallel CPU + holds N decoded images in RAM).
        # The pool is capped (see _PHASH_BACKFILL_WORKERS) so this stays a
        # gentle background pass; it also reports progress + honours
        # cancel so it isn't the silent machine-pegging op it once was.
        computed: dict[str, tuple[int | None, int | None]] = {}
        errors = 0
        done = 0
        total = len(missing_fps)
        store = ctx.analysis_store
        store.phash_total = total
        store.phash_done = 0
        store.phash_running = True

        with ThreadPoolExecutor(max_workers=_PHASH_BACKFILL_WORKERS) as pool:
            futures = {pool.submit(compute_hashes_from_file, fp): fp for fp in missing_fps}
            for future in as_completed(futures):
                if store.phash_cancel.is_set():
                    log.info("Hash backfill cancelled at %d/%d", done, total)
                    pool.shutdown(wait=False, cancel_futures=True)
                    store.phash_running = False
                    return
                fp = futures[future]
                try:
                    dhash, ahash = future.result()
                    computed[fp] = (dhash, ahash)
                    db_update_hashes(conn, fp, dhash, ahash)
                except Exception as exc:
                    errors += 1
                    if errors <= 5:
                        log.warning("Hash failed for %s: %s", fp, exc)
                    elif errors == 6:
                        # previously the per-error cap silently
                        # suppressed everything beyond the first 5.
                        # On a uniformly-failing library (SSD failure,
                        # permission flip on the photos dir, broken
                        # symlinks), an operator saw 5 lines + silence
                        # and had no signal that the failure was
                        # systematic. Emit one breadcrumb at the cap
                        # transition; the final summary line below
                        # carries the full count.
                        log.warning(
                            "Hash failed for %s: %s — further "
                            "per-file errors suppressed; see the "
                            "summary line at end of run for total "
                            "count",
                            fp,
                            exc,
                        )
                done += 1
                store.phash_done = done
                if done % 500 == 0:
                    log.info("Hash progress: %d/%d", done, total)
        store.phash_running = False
        # bail before touching shared state if the library
        # was switched out from under us. `switch_library` joins
        # this thread with a finite timeout — if the join times
        # out, this thread is still running but `ctx.analysis_store.phash_ready`
        # has already been replaced with the new library's Event.
        # Setting that Event here would falsely flag the new
        # library's hashes as ready and break dedupe on the very
        # next recompute. Compare against the captured generation
        # / Event identity; if either drifted, log and exit.
        if ctx.analysis_store.phash_generation != spawn_generation:
            log.warning(
                "phash compute completed for stale generation %d "
                "(current=%d) — discarding result to avoid "
                "polluting the new library's state",
                spawn_generation,
                ctx.analysis_store.phash_generation,
            )
            return
        # Batch-apply to analysis items under lock
        with ctx.lock:
            # Re-check inside the lock — switch_library() takes
            # `ctx.lock` to swap the analysis/dirs/etc atomically
            # so this is the right gate for the write.
            if ctx.analysis_store.phash_generation != spawn_generation:
                return
            analysis = ctx.state["analysis"]
            if analysis is not None:
                for item in analysis:
                    hashes = computed.get(item["filepath"])
                    if hashes:
                        item["phash"] = hashes[0]
                        item["ahash"] = hashes[1]
        spawn_event.set()
        log.info(
            "Hash pre-computation done: %d computed, %d errors.",
            len(computed),
            errors,
        )
        # Now that every photo has a perceptual hash, tag Live Photo
        # sidecars with phash confirmation. The analyze-from-folder path
        # imports through bulk_upsert (phash NULL at write time) and the
        # startup backfill is skipped when a library starts empty and is
        # then populated via analyze — so this is the one place that runs
        # AFTER hashes exist for an analyze-built library. require_phash_match
        # ensures a '_N' file is only hidden when it's provably the same
        # frame as its parent, never a genuinely distinct photo that merely
        # shares the naming convention. Idempotent + cheap (touches only
        # untagged rows).
        if computed:
            try:
                from bpp.db.live_photo import detect_and_link_live_photo_sidecars

                n_sidecars = detect_and_link_live_photo_sidecars(conn, require_phash_match=True)
                if n_sidecars:
                    # Sidecars were tagged after the analyze worker's
                    # sync_all_photos_album ran; re-sync so the Library count
                    # and smart albums reflect real photos only.
                    from bpp.db.albums import sync_all_photos_album

                    sync_all_photos_album(conn)
                    log.info(
                        "Tagged %d phash-confirmed Live Photo sidecar(s) after hashing",
                        n_sidecars,
                    )
            except Exception:
                log.warning(
                    "Live Photo sidecar tagging failed after hashing",
                    exc_info=True,
                )

        # Ordered derived-state recovery (the wipe-incident fix, S4
        # 2026-06-12): the cluster columns MUST rebuild after hashes +
        # sidecar tags exist and BEFORE the smart-album refresh reads
        # them. Previously clustering ran as independent racing
        # triggers — it could fire against NULL hashes, "recover" to
        # garbage, and never re-run.
        if computed:
            try:
                from bpp.db.dedupe import assign_near_duplicate_clusters

                log.info("Derived recovery: clustering near-duplicates...")
                n_dup = assign_near_duplicate_clusters(conn)
                log.info("Derived recovery: %d photos in dup clusters", n_dup)
            except Exception:
                log.warning("Near-duplicate clustering failed after hashing", exc_info=True)
            try:
                from bpp.db.moments import assign_moment_clusters

                log.info("Derived recovery: clustering Moments...")
                n_mom = assign_moment_clusters(conn)
                log.info("Derived recovery: %d photos in Moments", n_mom)
            except Exception:
                log.warning("Moment clustering failed after hashing", exc_info=True)

        # Refresh smart albums so Duplicates album appears
        if computed:
            try:
                from bpp.db.smart_albums import refresh_smart_albums

                refresh_smart_albums(conn)
                log.info("Smart albums refreshed after hash computation")
            except Exception:
                log.warning(
                    "Smart album refresh failed after hashing",
                    exc_info=True,
                )
        # Don't close — get_db() pool manages lifecycle via
        # close_all_connections() at shutdown.

        # Re-entrancy: an analyze that finished while this recovery ran
        # queued its data instead of racing a second thread — run it now.
        rerun = ctx.analysis_store.recovery_rerun
        ctx.analysis_store.recovery_rerun = None
        if rerun is not None and ctx.analysis_store.phash_generation == spawn_generation:
            log.info("Derived recovery: queued re-run starting (%d items)", len(rerun))
            precompute_phashes(ctx, rerun)

    ctx.analysis_store.compute_thread = threading.Thread(target=_compute, daemon=True)
    ctx.analysis_store.compute_thread.start()

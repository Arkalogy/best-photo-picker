"""WebAppState startup helpers — DB init and phash pre-compute.

These two operations are the heaviest single methods on ``WebAppState``
(``init_app_db`` runs migrations + import + smart-album backfill;
``precompute_phashes`` spawns a thread pool). Extracted to module-level
functions during the v0.1 cleanup so ``state.py`` stays under the
500-LOC soft cap. Both take the live :class:`WebAppState` as their
first argument and mutate it in place.

Not part of the public API — :class:`WebAppState` delegates from
``init_app_db()`` / ``precompute_phashes(data)`` thin wrappers.
"""

from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING, Any

import numpy as np

from bpp.db.albums import ensure_all_photos_album, sync_all_photos_album
from bpp.db.clip import get_all_clip_embeddings
from bpp.db.connection import get_db, init_db
from bpp.db.photos import bulk_upsert_photos, get_photo_count
from bpp.db.photos import get_all_photos as db_get_all_photos
from bpp.scoring.aggregate import load_analysis
from bpp.utils.logging import get_logger
from bpp.web.thumbnails import ThumbnailCache

if TYPE_CHECKING:
    from bpp.web.state import WebAppState

log = get_logger(__name__)


# The five init_app_db phase helpers live in state_init_phases.py since
# the post-review decomposition. Imported here under their previous
# leading-underscore names so tests + this module reference one stable
# surface. New callers should import directly from state_init_phases.
from bpp.web.state_init_phases import (  # noqa: E402
    acquire_serving_lock as _acquire_serving_lock,
)
from bpp.web.state_init_phases import (  # noqa: E402
    backfill_dup_clusters_and_refresh as _backfill_dup_clusters_and_refresh,
)
from bpp.web.state_init_phases import (  # noqa: E402
    backfill_live_photo_sidecars as _backfill_live_photo_sidecars,
)
from bpp.web.state_init_phases import (  # noqa: E402
    backup_or_refuse_corrupt as _backup_or_refuse_corrupt,
)
from bpp.web.state_init_phases import (  # noqa: E402
    import_from_legacy_caches as _import_from_legacy_caches,
)
from bpp.web.state_init_phases import (  # noqa: E402
    recover_interrupted_rename as _recover_interrupted_rename,
)


def init_app_db(ctx: WebAppState) -> None:
    """Initialize DB, run migrations, ensure 'All Photos' album exists.

    Phased orchestration (the body was 185 LOC pre-decomposition;
    each phase below is now a named helper for independent reading
    and per-phase unit testing):

    1. Acquire the serving lock — refuse to boot if a sibling owns it.
    2. Backup-or-refuse-corrupt — wraps backup_db with the actionable
       restore-command message on integrity failure.
    3. init_db + replay interrupted batch rename.
    4. Backfill Live Photo sidecars + legacy-cache import (analysis.json,
       face_embeddings, presets).
    5. Backfill near-duplicate clusters + final smart-album refresh.
    """
    wd = ctx.state.get("workdir")
    if not wd:
        return
    os.makedirs(wd, exist_ok=True)
    db_p = os.path.join(wd, "photopicker.db")

    # Phase 1 — serving lock.
    _acquire_serving_lock(wd)

    # Phase 2 — backup or refuse on corrupt DB.
    _backup_or_refuse_corrupt(db_p, wd)

    # Phase 3 — schema init + batch rename recovery.
    init_db(db_p)
    _recover_interrupted_rename(ctx)

    conn = ctx.get_conn()
    photo_count = get_photo_count(conn)
    lib_root = ctx.state.get("library_path", "")

    # Phase 4 — sidecar backfill + legacy-cache import. The "true"
    # return from 4a forces the smart-album refresh in 5 even when no
    # new dup clusters are assigned, because sidecar linking moves
    # rows in/out of every domain album.
    sidecars_linked = _backfill_live_photo_sidecars(conn) if photo_count > 0 else False
    _import_from_legacy_caches(conn, wd, lib_root, photo_count)

    ensure_all_photos_album(conn)
    sync_all_photos_album(conn)

    # Phase 5 — dup-cluster backfill + smart-album refresh, moved off
    # the startup-blocking path. At 200K-scale,
    # assign_near_duplicate_clusters is O(N log N) Union-Find and
    # refresh_smart_albums runs full-table scans for 16 album types —
    # together they were 5-10s of blocked /api/v1/photos. Daemon
    # thread frees the HTTP server immediately so the photo grid
    # paints fast; sidebar smart-album counts populate as the thread
    # completes. ctx.smart_album_backfill_done is the deterministic
    # wait point for switch_library / tests / any caller that needs
    # post-backfill consistency.
    #
    # The DB path is captured at SPAWN time, not resolved through
    # ctx.get_conn() at runtime. Otherwise a switch_library() between
    # spawn and execution would redirect this daemon to the NEW
    # library's DB (because ctx.paths.workdir flipped), running
    # Phase 5 against the wrong library. Capturing the path locks
    # the daemon to the library it was spawned for, so a mid-startup
    # switch leaves the old daemon to either complete cleanly against
    # the old DB or error out on a closed connection — never write
    # to the wrong place.
    ctx.smart_album_backfill_done.clear()
    db_path_at_spawn = db_p  # captured workdir/photopicker.db at this exact init

    def _phase5_background() -> None:
        # Server-log breadcrumbs — same pattern as the M10/M11 bookends
        # on face_worker._run and semantic_deduplicate. Phase 5 runs
        # silently in the background for 5-10s at large-library scale;
        # without these, a maintainer can't grep server.log to see
        # whether the backfill started, succeeded, or stalled. Project
        # convention: nothing should be silent — applies even to background work.
        import time as _time

        _t0 = _time.perf_counter()
        # Reset the health flag at spawn so a previous failed run
        # doesn't leak into the new attempt's reporting. The /api/v1/health
        # endpoint reads this to surface 'smart-album counts may be
        # stale' to the operator without them having to grep
        # server.log for the WARNING below.
        ctx.phase5_failed = False
        log.info("Phase 5 backfill starting (background, db=%s)", db_path_at_spawn)
        try:
            bg_conn = get_db(db_path_at_spawn)
            _backfill_dup_clusters_and_refresh(bg_conn, force_refresh=sidecars_linked)
        except Exception:
            # ERROR (not WARNING) — a failed background backfill leaves
            # the user with silently-stale smart-album counts until
            # they restart or trigger a manual refresh. That's a real
            # user-visible degradation, not a noisy warning.
            log.error(
                "Background dup-cluster + smart-album backfill failed — "
                "smart album counts will be stale until the next startup "
                "or manual refresh",
                exc_info=True,
            )
            ctx.phase5_failed = True
        finally:
            log.info(
                "Phase 5 backfill done in %.1fs (db=%s, failed=%s)",
                _time.perf_counter() - _t0,
                db_path_at_spawn,
                ctx.phase5_failed,
            )
            ctx.smart_album_backfill_done.set()

    threading.Thread(
        target=_phase5_background,
        daemon=True,
        name="bpp-phase5-backfill",
    ).start()


# Worker cap for the startup phash backfill. Each worker decodes a
# full-resolution image (the decode releases the GIL, so the pool is
# genuinely parallel CPU + holds N decoded images in memory at once).
# min(8, cpu_count) pegged a real machine — 254% CPU, load average 50+,
# memory climbing — while running silently. Keep the pool SMALL so the
# backfill is a gentle background citizen on large libraries. Do not
# raise this to "use all cores"; the backfill is best-effort warm-up,
# not a latency-critical path. Regression test:
# tests/test_phash_backfill_throttle.py.
# The derived-recovery pipeline (hash backfill -> sidecar tagging ->
# clustering -> album refresh) moved to bpp.web.derived_recovery when
# the LOC gate caught this file over the 500-line cap (2026-06-12).
# Re-exported so existing import paths keep working.
from bpp.web.derived_recovery import (  # noqa: E402, F401
    _PHASH_BACKFILL_WORKERS,
    precompute_phashes,
)


def load_clip_embeddings(ctx: WebAppState) -> dict[int, Any]:
    """Load CLIP embeddings from DB into cache. Returns {photo_id: embedding}.

    Lock discipline:
    - First "is ready?" check under lock — fast short-circuit.
    - DB read outside lock — slow, doesn't need to block readers.
    - Matrix build (np.stack of ~20MB on a 10k-photo library) is
      done outside lock, then swapped into the cache atomically
      under lock. Other threads that hit `load_clip_embeddings`
      during the build wait only for the dict-set, not the stack.
    """
    with ctx.lock:
        if ctx.caches.clip_cache["ready"]:
            return ctx.caches.clip_cache["embeddings"]
    # DB read outside lock to avoid blocking concurrent requests
    try:
        conn = ctx.get_conn()
        embs = get_all_clip_embeddings(conn)
    except Exception as e:
        # explicitly surface the size-cap path with a
        # clearer message so the user sees "library too big for
        # CLIP semantic dedupe" instead of a vague "failed".
        from bpp.db.clip import ClipEmbeddingsTooLarge

        if isinstance(e, ClipEmbeddingsTooLarge):
            log.warning(
                "Skipping CLIP embedding load: %s — semantic "
                "dedupe will be skipped for this library",
                e,
            )
        else:
            log.warning("Failed to load CLIP embeddings", exc_info=True)
        return {}

    # Build the stacked matrix OUTSIDE the lock — it's pure
    # numpy compute on a snapshot of the embeddings dict, doesn't
    # touch any shared state. A concurrent loader doing the same
    # work just produces a duplicate (same content); whichever
    # commit wins below is fine.
    ids: list[int] = []
    matrix = None
    if embs:
        try:
            ids = list(embs.keys())
            # np.stack(list(...)) briefly holds dict values + the list +
            # the matrix; drop the intermediate list immediately so peak
            # stays at the documented dict+matrix (2x), not 3x, at the
            # 200K-row cap (~400 MB per copy).
            values = list(embs.values())
            matrix = np.stack(values)
            del values
        except ValueError:
            log.warning("Failed to build CLIP embedding matrix", exc_info=True)
            embs = {}
            ids = []
            matrix = None

    # Atomic swap: every cache field is updated together so a
    # reader can't observe `ready=True` with a stale matrix or
    # vice versa.
    with ctx.lock:
        if not ctx.caches.clip_cache["ready"] and embs:
            ctx.caches.clip_cache["embeddings"] = embs
            ctx.caches.clip_cache["matrix"] = matrix
            ctx.caches.clip_cache["matrix_ids"] = ids
            ctx.caches.clip_cache["ready"] = True
            log.info("Loaded %d CLIP embeddings from DB", len(embs))
        return ctx.caches.clip_cache["embeddings"] if ctx.caches.clip_cache["ready"] else embs or {}


def load_analysis_if_needed(
    ctx: WebAppState, *, kick_recovery: bool = True
) -> list[dict[str, Any]] | None:
    """Load the analysis cache from DB on a cache miss.

    ``kick_recovery=False`` skips the derived-recovery kick on the
    cache-miss path — used by the analyze worker's finalize, whose OWN
    pipeline is already running (kicking again just queues a redundant
    re-run).
    """
    with ctx.lock:
        if ctx.state["analysis"] is not None:
            return ctx.state["analysis"]
        conn = ctx.get_conn()
        data = db_get_all_photos(conn, include_deleted=True)
        if not data:
            # Try loading from workdir (data/) then library root (legacy)
            json_data = None
            for search_dir in (ctx.state["workdir"], ctx.state.get("library_path")):
                if search_dir and os.path.isdir(search_dir):
                    json_data = load_analysis(search_dir)
                    if json_data:
                        break
            if json_data:
                bulk_upsert_photos(conn, json_data)
                sync_all_photos_album(conn)
                data = db_get_all_photos(conn, include_deleted=True)
        if data:
            # Enrich each photo dict with live_photo_sidecar_count so
            # build_photo_dict can show the ⊙ badge without N+1 queries.
            try:
                counts = {
                    row[0]: row[1]
                    for row in conn.execute(
                        "SELECT live_photo_parent_id, COUNT(*) "
                        "FROM photos WHERE is_live_photo_sidecar=1 "
                        "GROUP BY live_photo_parent_id"
                    ).fetchall()
                    if row[0] is not None
                }
                if counts:
                    for item in data:
                        item["live_photo_sidecar_count"] = counts.get(item.get("id"), 0)
            except Exception:
                pass  # sidecar count is cosmetic — never block startup
            ctx.state["analysis"] = data
            ctx.thumbs = ThumbnailCache(ctx.dirs["thumbs"])
            ctx.thumbs.build_map(data)
            if kick_recovery:
                ctx.precompute_phashes(data)
            ctx.load_clip_embeddings()
            return data
        return None

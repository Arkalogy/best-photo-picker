"""Phase helpers for :func:`bpp.web.state_init.init_app_db`.

Extracted from state_init.py after the post-review (2026-05-31) decomp
moved the 185-LOC init_app_db body into five named phases. Each helper
runs at a documented boundary in the startup sequence:

1. :func:`acquire_serving_lock` — refuse to boot if a sibling owns it.
2. :func:`backup_or_refuse_corrupt` — backup_db wrapper with the
   actionable restore-command message on integrity failure.
3. :func:`recover_interrupted_rename` — replay any batch rename
   interrupted by a prior crash.
4a. :func:`backfill_live_photo_sidecars` — link sidecars on libraries
    imported pre-v33.
4b. :func:`import_from_legacy_caches` — import analysis.json + legacy
    face embeddings + presets.
5. :func:`backfill_dup_clusters_and_refresh` — near-duplicate cluster
   backfill + final smart-album refresh.

Each helper is unit-testable in isolation; see
``tests/test_init_app_db_helpers.py`` for per-phase coverage.
"""

from __future__ import annotations

import atexit
import os
from typing import TYPE_CHECKING, Any

from bpp.db.albums import sync_all_photos_album
from bpp.db.connection import backup_db
from bpp.db.migrate import (
    import_face_embeddings,
    import_from_analysis_json,
    import_presets_from_json,
)
from bpp.scoring.aggregate import DB_NAME
from bpp.utils.logging import get_logger
from bpp.web.state_helpers import consume_restore_sentinel as _consume_restore_sentinel

if TYPE_CHECKING:
    from bpp.web.state import WebAppState

log = get_logger(__name__)


def acquire_serving_lock(wd: str) -> None:
    """Phase 1 — refuse to boot if a sibling bpp server is using this
    library, or if we can't write a lock file.

    The lock prevents two ``bpp serve`` processes from colliding on
    the same DB. Single-process libraries are the only safe shape;
    a silent dual-process configuration corrupts the WAL.

    Raises :class:`RuntimeError` with an actionable message in both
    failure modes (sibling alive, lock-file write failure).
    """
    from bpp.utils.serving_lock import ServingLockError, acquire_lock, clear_lock

    try:
        held_by = acquire_lock(wd)
    except ServingLockError as e:
        raise RuntimeError(
            f"Cannot acquire serving lock for {wd}: {e}. "
            "Refusing to start — running without a serving lock would "
            "let a second bpp serve corrupt the DB."
        ) from e
    if held_by is not None:
        raise RuntimeError(
            f"Another bpp server appears to be running (pid={held_by}) "
            f"using {wd}. Stop it first, or remove {wd}/.serving.lock "
            "if you're sure no server is up."
        )
    atexit.register(clear_lock, wd)


def backup_or_refuse_corrupt(db_p: str, wd: str) -> None:
    """Phase 2 — back up the existing DB (with integrity check) or
    refuse to boot when the DB is corrupt.

    A corrupt DB with an existing ``.backup`` produces a RuntimeError
    that names the recovery command verbatim — far better than the
    opaque sqlite3 stack trace the user would otherwise see.

    The ``_consume_restore_sentinel`` branch skips backup_db when we
    just restored from .backup; otherwise per-step migration backups
    would clobber the good copy with the upgrade's intermediate state.
    """
    if _consume_restore_sentinel(db_p):
        from bpp.db.connection import set_post_restore_skip_backup

        set_post_restore_skip_backup(True)
        return

    backup_result = backup_db(db_p)
    if backup_result is None and os.path.isfile(db_p) and os.path.getsize(db_p) > 0:
        backup_path = db_p + ".backup"
        if os.path.isfile(backup_path):
            raise RuntimeError(
                f"Database at {db_p} appears to be corrupt and is "
                f"NOT openable. A backup is preserved at "
                f"{backup_path}. To restore, quit this app and run:\n"
                f"  bpp db restore-backup --library {wd}\n"
                "If that backup is also bad, try --previous to use "
                "the rotated copy."
            )
        log.error(
            "Database may be corrupt and no .backup exists at %s.backup. "
            "Aborting startup — manual recovery required.",
            db_p,
        )
        raise RuntimeError(
            f"Database at {db_p} is corrupt and no backup exists. "
            "Manual recovery required: restore from your own backup "
            "or move the .db file aside and let bpp create a new "
            "empty library on next start."
        )


def recover_interrupted_rename(ctx: WebAppState) -> None:
    """Phase 3 — replay any batch rename interrupted by a prior crash.

    No-op when the library_path is empty (test fixtures, some CLI
    contexts) — recovery needs a writable path to act against.
    """
    lib_path = ctx.state.get("library_path", "")
    if not lib_path:
        return
    from bpp.db.batch_rename import recover_interrupted_rename as _impl

    conn_tmp = ctx.get_conn()
    recovered = _impl(conn_tmp, lib_path)
    if recovered:
        log.info("Recovered %d interrupted renames on startup", len(recovered))


def backfill_live_photo_sidecars(conn: Any) -> bool:
    """Phase 4a — link Live Photo sidecars on libraries imported pre-v33.

    Returns True when sidecars were linked and the smart-album refresh
    must run; False otherwise. The cheap probe ``WHERE
    is_live_photo_sidecar = 0 AND … instr(original_filename, '_')``
    skips libraries with no candidate filenames in one indexed query.
    """
    try:
        from bpp.db.live_photo import detect_and_link_live_photo_sidecars

        unlinked = conn.execute(
            "SELECT 1 FROM photos "
            "WHERE is_live_photo_sidecar = 0 AND deleted_at IS NULL "
            "AND instr(original_filename, '_') > 0 LIMIT 1"
        ).fetchone()
        if not unlinked:
            return False
        # require_phash_match: only hide a '_N' file once its phash confirms
        # it's the same frame as its parent. On an established library the
        # hashes already exist so this tags immediately; on a freshly
        # imported one they may still be NULL here, in which case the phash
        # backfill thread (precompute_phashes) does the tagging once hashes
        # land. Either way a genuinely distinct photo is never dropped on
        # the strength of its filename alone.
        linked = detect_and_link_live_photo_sidecars(conn, require_phash_match=True)
        if not linked:
            return False
        # Sidecars were linked after sync_all_photos_album ran;
        # re-sync so the Library count and smart albums (Recently Added,
        # Duplicates, etc.) reflect real photos only.
        sync_all_photos_album(conn)
        from bpp.db.smart_albums import refresh_smart_albums

        refresh_smart_albums(conn)
        return True
    except Exception:
        log.warning("Live Photo sidecar backfill failed", exc_info=True)
        return False


def import_from_legacy_caches(conn: Any, wd: str, lib_root: str, photo_count: int) -> None:
    """Phase 4b — import analysis.json + legacy face-embedding cache +
    presets into the live DB.

    Two-pronged trigger:

    * ``photo_count == 0`` (new library) → full import.
    * Some photos exist but lack aggregate_score → ``bpp analyze`` ran
      after ``bpp serve``, leaving score columns unpopulated. The
      analysis.json upsert fills them in without clobbering user state
      (selected, override, deleted_at).

    For a fresh library only, also imports the v0 face_embeddings
    sidecar DB and the presets JSON, if present.
    """
    _unscored = (
        photo_count > 0
        and conn.execute("SELECT 1 FROM photos WHERE aggregate_score IS NULL LIMIT 1").fetchone()
        is not None
    )
    if photo_count == 0 or _unscored:
        for search_dir in (wd, lib_root):
            json_path = os.path.join(search_dir, "analysis.json")
            if search_dir and os.path.exists(json_path):
                import_from_analysis_json(conn, json_path)
                break

    if photo_count == 0:
        for search_dir in (wd, lib_root):
            old_cache = os.path.join(search_dir, DB_NAME)
            if search_dir and os.path.exists(old_cache):
                import_face_embeddings(conn, old_cache)
                break

        config_home = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
        presets_json = os.path.join(config_home, "bpp", "presets.json")
        if os.path.exists(presets_json):
            import_presets_from_json(conn, presets_json)


def backfill_dup_clusters_and_refresh(conn: Any, force_refresh: bool) -> None:
    """Phase 5 — near-duplicate cluster backfill + final smart-album refresh.

    Libraries with phash data but no ``dup_cluster_id`` assignment
    have never run the clustering pass; do it now so the Duplicates
    smart album is non-empty on first paint.

    The ``force_refresh`` flag flows in from Phase 4a — when sidecars
    were linked, the smart-album refresh must run regardless of whether
    new clusters were assigned.
    """
    _needs_smart_refresh = force_refresh
    try:
        unclust = conn.execute(
            "SELECT 1 FROM photos "
            "WHERE phash IS NOT NULL AND dup_cluster_id = 0 "
            "AND missing=0 AND deleted_at IS NULL AND is_live_photo_sidecar=0 LIMIT 1"
        ).fetchone()
        if unclust:
            from bpp.db.dedupe import assign_near_duplicate_clusters

            assign_near_duplicate_clusters(conn)
            _needs_smart_refresh = True
    except Exception:
        log.warning("Near-duplicate cluster backfill failed", exc_info=True)

    if _needs_smart_refresh:
        try:
            from bpp.db.smart_albums import refresh_smart_albums

            refresh_smart_albums(conn)
            log.info("Smart albums refreshed after startup backfill")
        except Exception:
            log.warning("Smart album refresh after backfill failed", exc_info=True)

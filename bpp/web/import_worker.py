"""Background import thread with progress queue for SSE streaming."""

from __future__ import annotations

import os
from typing import Any

from bpp.config import cfg_get
from bpp.db.albums import sync_all_photos_album
from bpp.db.connection import init_db
from bpp.db.library import import_folder
from bpp.scoring.aggregate import (
    DB_NAME,
    compute_aggregate,
    init_analysis_db,
    normalize_blur_scores,
    process_one,
)
from bpp.utils.logging import get_logger
from bpp.web.base_worker import BackgroundWorker

log = get_logger(__name__)


def _store_scored(conn: Any, valid: list[dict[str, Any]], config: dict[str, Any]) -> None:
    """Finish the import's analysis: aggregate, write the rows, tell the
    plugins, sync the All album and recluster near-duplicates."""
    from bpp.db.dedupe import assign_near_duplicate_clusters
    from bpp.db.event_hooks import dispatch_post_analyze
    from bpp.db.photos import bulk_upsert_photos

    normalize_blur_scores(valid)
    compute_aggregate(valid, config)
    bulk_upsert_photos(conn, valid)
    # Imported photos are scored here, not by the analyze worker:
    # plugins subscribed to post_analyze must hear about them too.
    dispatch_post_analyze(conn, valid)
    sync_all_photos_album(conn)
    # Recompute near-duplicate clusters after each import+analyze so
    # the Duplicates album and review flow are always up to date.
    assign_near_duplicate_clusters(conn)


def _store_pet_detections(conn: Any, valid: list[dict[str, Any]]) -> int:
    """Write every scored photo's pet detections: one id lookup and one
    bulk upsert for the batch, not a statement per photo. Returns the
    number of detections stored."""
    from bpp.db.pets import bulk_upsert_pet_detections
    from bpp.db.photos import get_photo_id_map_by_paths

    with_pets = [item for item in valid if item.get("pet_detections")]
    if not with_pets:
        return 0
    id_map = get_photo_id_map_by_paths(conn, [item["filepath"] for item in with_pets])
    items = [
        (id_map[item["filepath"]], item["pet_detections"])
        for item in with_pets
        if item["filepath"] in id_map
    ]
    return bulk_upsert_pet_detections(conn, items)


class ImportWorker(BackgroundWorker):
    """Runs photo import + analysis in a background thread, reporting progress via a queue."""

    # Imports, then analyzes: the scoring models (faces, nudity, pets, CLIP).
    loads_installable_packages = True

    _worker_name = "Import"

    def start(
        self,
        source_dir: str,
        library_path: str,
        workdir: str,
        config: dict[str, Any],
        extensions: list[str],
        batch_name: str | None = None,
        import_live_photo_sidecars: bool = False,
    ) -> bool:
        """Start background import. Returns False if already running."""
        return self._start_thread(
            source_dir,
            library_path,
            workdir,
            config,
            extensions,
            batch_name,
            import_live_photo_sidecars,
        )

    def _run(
        self,
        source_dir: str,
        library_path: str,
        workdir: str,
        config: dict[str, Any],
        extensions: list[str],
        batch_name: str | None,
        import_live_photo_sidecars: bool,
    ) -> None:
        # Server-log breadcrumbs — same pattern as the face-worker + Phase 5
        # daemon bookends. Import is a 5-10 minute run on large
        # libraries; without these, an operator debugging a stuck or
        # slow run sees no anchor in server.log tying the attempt to a
        # wall-clock window. Project convention: nothing should be silent.
        import time as _time

        _t0 = _time.perf_counter()
        log.info(
            "Import starting: src=%s, extensions=%s, sidecars=%s",
            source_dir,
            extensions,
            import_live_photo_sidecars,
        )
        try:
            db_path = os.path.join(workdir, "photopicker.db")
            conn = init_db(db_path)
            self._do_import(
                conn,
                source_dir,
                library_path,
                workdir,
                config,
                extensions,
                batch_name,
                import_live_photo_sidecars,
            )
        finally:
            log.info(
                "Import done in %.1fs (src=%s)",
                _time.perf_counter() - _t0,
                source_dir,
            )

    def _do_import(
        self,
        conn: Any,
        source_dir: str,
        library_path: str,
        workdir: str,
        config: dict[str, Any],
        extensions: list[str],
        batch_name: str | None,
        import_live_photo_sidecars: bool = False,
    ) -> None:
        # Phase 1: Import (copy files)
        self._emit({"type": "phase", "phase": "importing"})

        def on_import_progress(current: int, total: int, filename: str, status: str) -> None:
            self._emit(
                {
                    "type": "import_progress",
                    "current": current,
                    "total": total,
                    "filepath": filename,
                    "status": status,
                }
            )

        result = import_folder(
            conn=conn,
            source_dir=source_dir,
            library_path=library_path,
            extensions=extensions,
            batch_name=batch_name,
            on_progress=on_import_progress,
            import_live_photo_sidecars=import_live_photo_sidecars,
        )

        self._emit(
            {
                "type": "import_done",
                "imported": result.imported,
                "skipped": result.skipped,
                "errors": result.errors,
                "batch_name": result.batch_name,
            }
        )

        if not result.imported_paths:
            self._emit(
                {
                    "type": "done",
                    "imported": result.imported,
                    "skipped": result.skipped,
                    "analyzed": 0,
                }
            )
            return

        # Phase 2: Analyze imported photos
        self._emit({"type": "phase", "phase": "analyzing"})

        cache_db_path = os.path.join(workdir, DB_NAME)
        init_analysis_db(cache_db_path)
        max_long_side = cfg_get(config, "max_long_side")

        valid: list[dict[str, Any]] = []
        total_analyze = len(result.imported_paths)
        for i, filepath in enumerate(result.imported_paths):
            if self._cancelled.is_set():
                self._emit({"type": "cancelled"})
                return
            analyzed = process_one((filepath, max_long_side, cache_db_path))
            if analyzed is not None:
                valid.append(analyzed)
            self._emit(
                {
                    "type": "analyze_progress",
                    "current": i + 1,
                    "total": total_analyze,
                    "filepath": os.path.basename(filepath),
                }
            )

        if valid:
            _store_scored(conn, valid, config)

            from bpp.db.pets import assign_pet_clusters

            pet_count = _store_pet_detections(conn, valid)
            if pet_count > 0:
                assign_pet_clusters(conn)
                # refresh the pet smart album immediately so
                # the UI's left sidebar reflects newly-clustered
                # pets. Without this, the pet album stayed stale
                # until the next full smart-album sweep (typically
                # only triggered by other state changes).
                from bpp.db.smart_albums import get_affected_album_types, refresh_smart_albums

                # … and the Duplicates / Moments albums the clustering above
                # feeds (the dedup domain), in the same pass.
                refresh_smart_albums(
                    conn,
                    kinds={
                        *get_affected_album_types("import"),
                        *get_affected_album_types("dedup"),
                    },
                )

        from bpp.web.analyze_finalize import finalize_import_in_worker

        finalize_import_in_worker(self.ctx)
        self._emit(
            {
                "type": "done",
                "finalized": self.ctx is not None,  # the stream's backstop can skip
                "imported": result.imported,
                "skipped": result.skipped,
                "analyzed": len(valid),
            }
        )

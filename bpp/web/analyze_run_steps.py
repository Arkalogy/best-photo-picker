"""Two steps of ``AnalyzeWorker._run`` that don't need the worker itself:
the Live Photo sidecar pre-scan and the post-scoring DB write.

Both take ``emit`` (the worker's progress callback) rather than the
worker, so they can be read and tested on their own.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from bpp.db.photos import get_photo_id_map_by_paths
from bpp.utils.logging import get_logger

log = get_logger(__name__)

Emit = Callable[[dict[str, Any]], None]


def prescan_live_photo_sidecars(
    images: list[str], workdir: str, emit: Emit
) -> tuple[list[str], list[dict[str, Any]]]:
    """Pull phash-confirmed Live Photo sidecars out of the to-score list.

    The expensive scoring/face/CLIP passes never run on them (they're
    near-identical motion frames nobody composed — roughly half of an
    iCloud export). Confirmation is by perceptual hash, never filename
    alone, so a genuinely distinct beach_2.jpg is never dropped. The
    skipped sidecars are returned as minimal rows carrying the hashes
    computed here; the phash backfill thread then tags + links them via
    detect_and_link.

    Returns ``(images_to_score, sidecar_records)``. The pre-scan is an
    optimization, never a gate: on any failure every image is scored.
    """
    try:
        from bpp.db.live_photo import split_scan_for_confirmed_sidecars

        def _on_sidecar_progress(done: int, of: int) -> None:
            emit(
                {
                    "type": "status",
                    "message": f"Checking Live Photo duplicates {done}/{of}…",
                }
            )

        dead_parents = _dead_parent_paths(workdir)
        to_score, sidecar_records = split_scan_for_confirmed_sidecars(
            images,
            on_progress=_on_sidecar_progress,
            parent_alive=(lambda p: p not in dead_parents) if dead_parents else None,
        )
        if sidecar_records:
            log.info(
                "Pre-scan skipped %d phash-confirmed Live Photo sidecar(s) from scoring",
                len(sidecar_records),
            )
            emit(
                {
                    "type": "status",
                    "message": (
                        f"Skipping {len(sidecar_records)} Live Photo duplicate(s) from scoring"
                    ),
                }
            )
        return to_score, sidecar_records
    except Exception:
        # Pre-scan is an optimization, never a gate. On any failure fall
        # back to scoring everything — the phash thread still tags sidecars
        # afterward, so correctness holds; we just don't save the compute.
        log.warning("Live Photo sidecar pre-scan failed; scoring all images", exc_info=True)
        return images, []


def _dead_parent_paths(workdir: str) -> set[str]:
    """Parents the DB says are gone (soft-deleted / hidden / sidecars
    themselves) can never anchor a hidden child — detect_and_link refuses
    to tag against them, so a candidate skipped from scoring here would
    linger as a visible 0% ghost. (Real case: a duplicate review keeps
    the `_1` copy and trashes the parent — the kept copy is the user's
    photo now and must be scored.)"""
    pp_db = os.path.join(workdir, "photopicker.db")
    if not os.path.exists(pp_db):
        return set()
    try:
        from bpp.db.connection import init_db

        conn = init_db(pp_db)
        return {
            r[0]
            for r in conn.execute(
                "SELECT filepath FROM photos WHERE deleted_at IS NOT NULL "
                "OR hidden_at IS NOT NULL OR is_live_photo_sidecar = 1"
            )
        }
    except Exception:
        log.warning(
            "Could not load dead-parent set for sidecar pre-scan; "
            "pre-scan will use filename+phash only",
            exc_info=True,
        )
        return set()


def store_scoring_results(
    conn: Any, valid: list[dict[str, Any]], sidecar_records: list[dict[str, Any]], emit: Emit
) -> None:
    """Write the normalized scoring results, the pre-scan sidecar rows and
    the pet detections, then fire the post-analyze plugin event."""
    from bpp.db.albums import sync_all_photos_album
    from bpp.db.photos import bulk_upsert_photos

    bulk_upsert_photos(conn, valid)

    # Store the phash-confirmed Live Photo sidecars the pre-scan
    # pulled out of scoring. They land as minimal rows WITH the phash
    # the pre-scan computed (is_live_photo_sidecar still 0 here). They
    # do NOT get tagged here: bulk_upsert_photos NULLs the PARENT
    # phash, so a require_phash_match tag would fail until the phash
    # thread recomputes the parent hashes. That thread runs
    # detect_and_link(require_phash_match=True) once both sides have
    # hashes — see precompute_phashes in state_init.py — which is the
    # single place sidecar linking lives.
    #
    # LOAD-BEARING INVARIANT (do not break): every stored sidecar row
    # is un-tagged here and is hidden ONLY by that phash thread. The
    # thread early-exits when nothing needs hashing. So a stored
    # sidecar MUST be accompanied by a parent that needs a hash, or the
    # row never gets tagged and lingers as a visible, score-0 "ghost"
    # photo. This holds because `split_scan_for_confirmed_sidecars`
    # only confirms a sidecar when its parent is in the SAME scan batch
    # — that parent is in `valid`, bulk_upsert NULLs its phash, so the
    # phash thread always runs and always tags. The guarantee is
    # enforced by test_live_photo.py::...every_record_has_parent_in_to_score;
    # if you change the pre-scan to emit a sidecar whose parent isn't
    # scored, restore the tagging on this path too.
    if sidecar_records:
        try:
            bulk_upsert_photos(conn, sidecar_records)
            log.info(
                "Stored %d pre-scan Live Photo sidecar row(s) (unscored)",
                len(sidecar_records),
            )
        except Exception:
            log.warning("Failed to store pre-scan sidecar rows", exc_info=True)

    sync_all_photos_album(conn)
    _store_pet_detections(conn, valid, emit)
    log.info("Wrote %d photos to DB", len(valid))

    # Plugin event bus: post-analyze fires after the DB write
    # committed but before downstream phases (faces / CLIP) start.
    # Plugin failures are swallowed and logged — see event_hooks.
    from bpp.db.event_hooks import dispatch_post_analyze

    dispatch_post_analyze(conn, valid)


def _store_pet_detections(conn: Any, valid: list[dict[str, Any]], emit: Emit) -> None:
    """Save pet detections (batched) and cluster them."""
    from bpp.db.pets import assign_pet_clusters, bulk_upsert_pet_detections

    photo_map = get_photo_id_map_by_paths(conn, [i["filepath"] for i in valid])
    pet_items: list[tuple[int, list]] = []
    for item in valid:
        dets = item.get("pet_detections", [])
        pid = photo_map.get(item["filepath"])
        if dets and pid is not None:
            pet_items.append((pid, dets))
    pet_count = bulk_upsert_pet_detections(conn, pet_items)
    if pet_count > 0:
        try:
            assign_pet_clusters(conn)
        except Exception:
            log.warning("Pet clustering failed", exc_info=True)
            emit(
                {
                    "type": "warning",
                    "message": "Pet clustering failed — detections saved but may be mismatched",
                }
            )
        log.info("Saved %d pet detections to DB", pet_count)

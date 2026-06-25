"""Face-extraction orchestrator.

Thin shim over :mod:`bpp.web.face_phase_pipeline` — builds the
:class:`FacePhaseContext` from the caller's arguments, hands it to
:func:`run_face_pipeline`, returns the ``(faces_found, n_clusters)``
tuple the public API has always returned.

P3 finish — every phase-specific control flow (journal skips,
snapshot rehydration, mid-phase cancellation) lives on the phase
classes in :mod:`face_phase_pipeline`. This module owns only the
context wiring + the resume-vs-fresh journal id.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from bpp.utils.logging import get_logger
from bpp.web import face_extraction_journal as journal
from bpp.web.face_phase_pipeline import FacePhaseContext, run_face_pipeline

log = get_logger(__name__)


def extract_and_cluster_faces(
    conn: sqlite3.Connection,
    with_faces: list[dict[str, Any]],
    photo_map: dict[str, int],
    max_long_side: int,
    face_confidence: float,
    config: dict[str, Any],
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    cancellation_check: Callable[[], bool] | None = None,
    post_cluster_dedup: bool = False,
    cancel_event: Any = None,
    resume_run_id: str | None = None,
) -> tuple[int, int]:
    """Extract face embeddings and cluster them.

    Shared logic used by both AnalyzeWorker (face phase) and FaceWorker.
    P3 — the seven-phase pipeline lives in
    :data:`bpp.web.face_phase_pipeline.FACE_PIPELINE`; this function
    just builds the context and drives the runner.
    """
    # Batch 3 / item 18 — fail closed if the download chokepoint is
    # not installed before any model work begins. Catches the case
    # where bpp.registry has not been imported by this code path,
    # which would let a transitively-imported third-party package
    # (insightface and friends) silently auto-download a restricted
    # model. enforce_chokepoint() is cheap (a set lookup per known
    # entry) and raises BlockedAutoDownloadError on the first missing
    # patch with the rationale showing how to fix the wiring.
    from bpp.registry import (
        check_model_load_allowed,
        enforce_chokepoint,
        get_default_for_kind,
        get_use_context,
        raise_if_blocked,
    )

    enforce_chokepoint()

    # Batch 5 / item 16 — hard-block restricted models at the
    # orchestrator chokepoint when the user's declared use context
    # forbids them. The face-embedder default is the typical case
    # (SFace, permissive, ALLOW). A future restricted entry that
    # somehow became the default, or any future restricted entry
    # loaded explicitly, is refused here with a structured
    # ModelLoadBlockedError carrying the policy decision and the
    # remedy path. Permissive entries fall straight through.
    default_entry = get_default_for_kind("face_embedder")
    if default_entry is not None:
        raise_if_blocked(
            check_model_load_allowed(
                default_entry,
                use_context=get_use_context(),
            )
        )

    # Late import: tests monkey-patch face_worker._extract_one etc.
    # Reading from face_worker at call time means patches take effect
    # even though the implementation lives in this module.
    from bpp.web import face_worker

    # Synthesize cancel_event into cancellation_check (P1 contract).
    if cancel_event is not None:
        from bpp.utils.cancel import as_token

        _tok = as_token(cancel_event)
        if _tok is not None:
            _existing = cancellation_check
            cancellation_check = (
                _tok.is_set if _existing is None else (lambda: _tok.is_set() or _existing())
            )

    # Open / rehydrate the journal row.
    if resume_run_id is not None:
        run_id = resume_run_id
        phases_done = journal.get_phases_complete(conn, run_id)
        log.info(
            "Resuming face extraction run %s — phases_done bitmask=%s",
            run_id,
            f"0b{phases_done:07b}",
        )
    else:
        run_id = journal.start_run(conn)
        phases_done = 0
        log.info("Started face extraction run %s (%d photos)", run_id, len(with_faces))

    ctx = FacePhaseContext(
        conn=conn,
        with_faces=with_faces,
        photo_map=photo_map,
        max_long_side=max_long_side,
        face_confidence=face_confidence,
        config=config,
        run_id=run_id,
        phases_done=phases_done,
        extract_one_fn=face_worker._extract_one,
        validate_bbox_fn=face_worker._validate_bbox,
        validate_embedding_fn=face_worker._validate_embedding,
        assign_new_faces_fn=face_worker._assign_new_faces,
        reconstruct_identities_fn=face_worker._reconstruct_identities,
        remap_names_and_tags_fn=face_worker._remap_names_and_tags,
        progress_callback=progress_callback,
        cancellation_check=cancellation_check,
        post_cluster_dedup=post_cluster_dedup,
    )

    run_face_pipeline(ctx)

    log.info(
        "Face extraction run %s: %d faces, %d clusters%s",
        run_id,
        ctx.faces_found,
        ctx.n_clusters,
        " (cancelled)" if ctx.cancelled_early else "",
    )
    return ctx.faces_found, ctx.n_clusters

"""Analyze worker Phase 2 (faces) + Phase 3 (CLIP).

Extracted from ``analyze_worker.py`` to keep that file under the 500-LOC
cap (project convention). Both are plain functions that take the worker's ``emit``
callback and ``cancel_event`` instead of ``self``.

THREAD-POOL PINNING: every heavy ML import here stays lazy (inside the
function bodies), exactly as it was when these lived as methods. The
native-thread-pool env-var pin in ``analyze_worker.py`` runs at that
module's import time; this module imports only stdlib + logging at the
top, so importing it never drags cv2 / onnxruntime in ahead of the pin.
See the ARCHITECTURE INVARIANT banner in analyze_worker.py.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from bpp.utils.logging import get_logger

log = get_logger(__name__)

EmitFn = Callable[[dict[str, Any]], None]


def run_face_phase(
    conn,
    valid: list[dict[str, Any]],
    config: dict[str, Any],
    *,
    emit: EmitFn,
    cancel_event,
) -> tuple[int, int]:
    """Phase 2: extract face embeddings and cluster in a subprocess.

    Like Phase 1, face extraction loads multiple ML models (SFace, dlib,
    YuNet, BlazeFace, HandLandmarker). Running in a subprocess guarantees
    memory recovery when the child exits.
    """
    from bpp.db.dialect import dialect
    from bpp.web.analyze_face_extract import run_face_extraction_subprocess

    with_faces = [a for a in valid if (a.get("face_count") or 0) > 0]
    face_total = len(with_faces)
    emit({"type": "face_start", "total": face_total})

    if face_total == 0:
        return 0, 0

    db_path = dialect.database_path(conn)
    # P1: thread the worker's threading-side cancel signal into the
    # subprocess runner. The runner builds a ProcessCancellation + bridge
    # under the hood (see analyze_face_extract docstring). Before P1 this
    # argument didn't exist — face extraction kept running until completion
    # regardless of the Cancel button.
    faces_found, face_clusters, _pid = run_face_extraction_subprocess(
        with_faces,
        config,
        db_path,
        progress_callback=emit,
        cancel_event=cancel_event,
    )
    return faces_found, face_clusters


def run_clip_phase(
    conn,
    valid: list[dict[str, Any]],
    *,
    emit: EmitFn,
    cancel_event,
) -> int:
    """Phase 3: compute CLIP embeddings for analyzed photos. Returns count computed."""
    from bpp.constants import CLIP_MODEL_NAME
    from bpp.db.photos import get_photo_id_map_by_paths
    from bpp.scoring.clip_embed import ensure_model
    from bpp.scoring.clip_embed import is_available as clip_is_available
    from bpp.scoring.model_base import ModelIntegrityError
    from bpp.web.clip_worker import compute_clip_embeddings

    if not clip_is_available():
        log.info("CLIP model not available, skipping Phase 3")
        return 0

    emit({"type": "status", "message": "Checking CLIP model…"})
    try:
        ensure_model()
    except ModelIntegrityError:
        # CLIP integrity failure must NOT silently degrade to "skip phase 3".
        # A tampered cached model or MITM'd download is a loud event —
        # surface it as an error and abort the worker so the operator sees it.
        # Without this the worker reports "analyze complete" while CLIP search
        # quietly stops working library-wide.
        log.error("CLIP model integrity failure during analyze", exc_info=True)
        emit(
            {
                "type": "error",
                "message": (
                    "CLIP model integrity verification failed. The cached "
                    "or downloaded ONNX bytes do not match the pinned "
                    "SHA-256. Refusing to proceed. Reinstall or re-download "
                    "via Settings → Advanced → ML Models, then re-analyze."
                ),
            }
        )
        raise
    except Exception:
        log.warning("CLIP model download failed, skipping", exc_info=True)
        # Name the consequence + the recovery path: unlike SCRFD (which falls
        # back to dlib), CLIP has no fallback — semantic search is off until
        # the user retries the download. A bare "skipping" left them unaware.
        emit(
            {
                "type": "warning",
                "message": (
                    "CLIP model unavailable — semantic search disabled. "
                    "Retry the download via Settings → Advanced → ML Models."
                ),
            }
        )
        return 0

    # Build filepath→photo_id map
    photo_map = get_photo_id_map_by_paths(conn, [item["filepath"] for item in valid])

    # Find photos that already have CLIP embeddings
    existing: set[int] = set()
    try:
        rows = conn.execute(
            "SELECT photo_id FROM clip_embeddings WHERE model_name = ?",
            (CLIP_MODEL_NAME,),
        ).fetchall()
        existing = {r[0] for r in rows}
    except Exception:
        log.warning("Failed to check existing CLIP embeddings", exc_info=True)

    missing = []
    for item in valid:
        pid = photo_map.get(item["filepath"])
        if pid is not None and pid not in existing:
            missing.append((item["filepath"], pid))

    clip_total = len(missing)
    emit({"type": "clip_start", "total": clip_total})

    if clip_total == 0:
        return 0

    def _progress(current: int, total: int, basename: str) -> None:
        emit({"type": "clip_progress", "current": current, "total": total, "filepath": basename})

    computed = compute_clip_embeddings(
        conn,
        missing,
        progress_callback=_progress,
        cancellation_check=lambda: cancel_event.is_set(),
    )
    log.info("CLIP Phase 3: %d/%d embeddings computed", computed, clip_total)
    return computed

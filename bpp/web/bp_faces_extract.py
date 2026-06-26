"""Face extraction worker endpoints.

Extracted from bp_faces.py during the v0.1 cleanup. Three endpoints
drive the face-extraction background worker (SCRFD/SFace pipeline):

* ``/api/v1/faces/extract`` (POST) — kick off extraction over the
  current analysis set
* ``/api/v1/faces/retry`` (POST) — re-run only on photos whose
  prior extraction failed
* ``/api/v1/faces/extract/progress`` (GET) — SSE stream of worker
  progress + completion

These three are the "worker control" surface — distinct from cluster
reads, cluster mutations (bp_faces_manage), pair-review
(bp_faces_review), and per-face bbox edits (bp_faces_bbox).
"""

from __future__ import annotations

import os
import shutil

from flask import Blueprint, Response, jsonify

from bpp.errors import BppError, ConflictError, FeatureUnavailableError, NotFoundError
from bpp.scoring.face_embed import is_available as face_recognition_available
from bpp.utils.logging import get_logger
from bpp.web.share import requires_local_app
from bpp.web.sse import stream_worker_progress
from bpp.web.state import get_ctx, with_face_lock

log = get_logger(__name__)

bp = Blueprint("faces_extract", __name__)


@bp.post("/api/v1/faces/extract")
@requires_local_app
@with_face_lock
def api_faces_extract() -> tuple[Response, int]:
    """Start the face-extraction worker over the current analysis set.

    Requires face_recognition to be installed and analysis data to
    exist. Returns 409 when analysis or another extraction is
    already running."""
    ctx = get_ctx()
    analysis = ctx.load_analysis_if_needed()
    if analysis is None:
        raise NotFoundError("No analysis data")
    if not face_recognition_available():
        raise FeatureUnavailableError(
            "Face recognition unavailable \u2014 the SFace model couldn't be loaded "
            "and the dlib fallback isn't installed. Check Settings \u2192 Models "
            "(SFace auto-downloads), or install the dlib extra: pip install "
            "bppicker[faces].",
            extra="faces",
        )
    if ctx.worker.is_alive:
        raise ConflictError(
            "Analysis in progress — wait for it to finish",
            blocker="analysis",
        )
    db_p = ctx.db_path()
    started = ctx.face_worker.start(analysis, db_p, ctx.config)
    if not started:
        raise ConflictError("Face extraction already in progress")
    return jsonify({"status": "started"}), 202


@bp.post("/api/v1/faces/retry")
@requires_local_app
@with_face_lock
def api_faces_retry() -> tuple[Response, int]:
    """Wipe existing face embeddings and crop cache, then restart the
    face-extraction worker from scratch. Used when detection settings
    change or initial extraction produced bad clusters."""
    ctx = get_ctx()
    analysis = ctx.load_analysis_if_needed()
    if analysis is None:
        raise NotFoundError("No analysis data")
    if not face_recognition_available():
        raise FeatureUnavailableError(
            "Face recognition unavailable \u2014 the SFace model couldn't be loaded "
            "and the dlib fallback isn't installed. Check Settings \u2192 Models "
            "(SFace auto-downloads), or install the dlib extra: pip install "
            "bppicker[faces].",
            extra="faces",
        )
    if ctx.face_worker.is_alive:
        raise ConflictError("Face extraction already in progress")
    if ctx.worker.is_alive:
        raise ConflictError(
            "Analysis in progress — wait for it to finish",
            blocker="analysis",
        )

    # Clear existing face embeddings + manual person tags from main DB.
    # photo_person_tags references cluster IDs that the next extraction
    # will renumber. Without wiping them, _create_tag_only_clusters
    # recreates "Person N" albums for stale cluster IDs that no longer
    # have any embeddings — ghost albums in the sidebar after retry.
    # Identity names (e.g. "Alice") survive because they're stored in
    # albums.name, and _transfer_orphan_names re-binds them to the
    # best-overlapping new cluster via photo_id intersection.
    #
    # Journal-wrap so a SIGKILL/SIGSEGV between the wipe and the
    # extraction-completes step leaves a breadcrumb. The recovery
    # handler at bpp/web/face_worker.py::register_face_extraction_retry_recovery
    # picks up the pending entry on the next startup and re-fires the
    # extraction worker. Without this, a crash mid-flight strands the
    # user with empty face data and no automatic recovery — they'd
    # have to click "Retry" again and wait.
    from bpp.db.journal import journal_start

    conn = ctx.get_conn()
    # Best-effort: if the journal write itself fails (corrupt schema,
    # disk full), don't block the retry — log and continue. Worst case
    # is "no recovery if a crash happens during this specific retry,"
    # which is the baseline before the fix. The entry is cleared by the
    # FaceWorker's success path or the recovery handler on next
    # startup, so we don't need to track the returned id here.
    try:
        journal_start(
            conn,
            "face_extraction_retry",
            {"started_via": "api_faces_retry", "version": 1},
        )
    except Exception:
        log.warning(
            "Failed to open retry journal — proceeding without recovery breadcrumb",
            exc_info=True,
        )

    try:
        conn.execute("DELETE FROM face_embeddings")
        conn.execute("DELETE FROM photo_person_tags")
        conn.commit()
    except Exception as e:
        raise BppError(
            "Failed to clear existing face data",
            user_message="Failed to clear existing face data",
            diagnostic_message=f"face_embeddings DELETE failed for retry: {e!s}",
        ) from e

    # Clear face crop cache
    crop_dir = ctx.dirs["face_crops"]
    if os.path.isdir(crop_dir):
        shutil.rmtree(crop_dir, ignore_errors=True)

    db_p = ctx.db_path()
    started = ctx.face_worker.start(analysis, db_p, ctx.config)
    if not started:
        raise BppError(
            "Could not start face extraction",
            user_message="Could not start face extraction",
            diagnostic_message="face_worker.start returned False on retry path",
        )
    return jsonify({"status": "started"}), 202


@bp.get("/api/v1/faces/extract/progress")
def api_faces_progress() -> Response:
    """Stream face-extraction worker progress as Server-Sent Events.

    Yields ``progress``, ``done``, ``error``, and ``keepalive`` messages
    until the worker exits. Falls through to a worker-stopped error
    if the queue starves while the worker is no longer alive."""
    ctx = get_ctx()
    return Response(
        stream_worker_progress(ctx.face_worker),
        mimetype="text/event-stream",
    )

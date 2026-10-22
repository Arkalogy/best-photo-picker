"""CLIP routes: extraction kickoff + progress stream + dedup feedback stats.

Extracted from bp_faces_manage.py during the v0.1 cleanup — CLIP
extraction was a separate concern (semantic-search vectors) that had
no shared state with the face-mutation endpoints. Isolating it
shrinks bp_faces_manage and gives CLIP a logical home for future
related routes (re-extract, ablation, etc.) to land in.

Also hosts the CLIP-dedup adaptive-threshold stats endpoint, which is
about CLIP similarity feedback, not face clusters.
"""

from __future__ import annotations

from flask import Blueprint, Response, jsonify

from bpp.config import cfg_get
from bpp.db.clip import compute_adaptive_threshold, get_dedup_feedback
from bpp.errors import ConflictError, FeatureUnavailableError, NotFoundError
from bpp.scoring.clip_embed import is_available as clip_is_available
from bpp.scoring.install_hold import refuse_if_held
from bpp.utils.logging import get_logger
from bpp.web.base_worker import raise_if_blocked
from bpp.web.bp_install import no_install_during
from bpp.web.share import requires_local_app
from bpp.web.sse import captured_device_fingerprint, stream_worker_progress
from bpp.web.state import get_ctx

log = get_logger(__name__)

bp = Blueprint("clip", __name__)


@bp.post("/api/v1/clip/extract")
@requires_local_app
def api_clip_extract() -> tuple[Response, int]:
    """Start the CLIP embedding worker over the current analysis set.

    Lazily downloads the CLIP model on first call. Returns 409 if a
    CLIP extraction is already in progress."""
    ctx = get_ctx()
    analysis = ctx.load_analysis_if_needed()
    if analysis is None:
        raise NotFoundError("No analysis data")
    # Marked busy through the (maybe long) model download and the start, so
    # an install can't begin in between.
    with no_install_during("the smart search setup (its model download)"):
        # Installed from the app this session: say so, not "unavailable".
        refuse_if_held("onnxruntime")
        # Would the indexing be allowed to start (no install waiting for a
        # restart)? Asked before the (maybe long) model download.
        raise_if_blocked(ctx.clip_worker)
        if not clip_is_available():
            try:
                from bpp.scoring.clip_embed import ensure_model

                ensure_model()
            except Exception as e:
                # FeatureUnavailableError = 501; the CLIP model couldn't be
                # installed/loaded, which is an environment issue, not a
                # user error or transient failure.
                raise FeatureUnavailableError(
                    "CLIP model unavailable",
                    user_message="CLIP model unavailable",
                    diagnostic_message=f"ensure_model failed: {e!s}",
                ) from e

        db_p = ctx.db_path()
        started = ctx.clip_worker.start(analysis, db_p)
    if not started:
        raise ConflictError("CLIP extraction already in progress")
    return jsonify({"status": "started"}), 202


@bp.get("/api/v1/clip/progress")
def api_clip_progress() -> Response:
    """Stream CLIP-extraction progress as Server-Sent Events.

    On a successful ``done`` message, invalidates and reloads the CLIP
    embedding cache so search and dedup pick up the new vectors
    without a server restart."""
    ctx = get_ctx()

    def _on_done(_msg):
        # Invalidate + reload the CLIP cache so search/dedup pick up the
        # new vectors without a server restart.
        with ctx.lock:
            from bpp.web.recompute import dedupe_cache_clear

            ctx.caches.clip_cache["ready"] = False
            dedupe_cache_clear()
        ctx.load_clip_embeddings()

    return Response(
        stream_worker_progress(
            ctx.clip_worker, on_done=_on_done, ctx=ctx, device_fp=captured_device_fingerprint()
        ),
        mimetype="text/event-stream",
    )


@bp.get("/api/v1/dedup/feedback/stats")
def api_dedup_feedback_stats() -> tuple[Response, int]:
    """Return the current adaptive CLIP-similarity threshold for dedup
    along with the configured default, the diagnostic ``info`` dict
    explaining how the threshold was computed, and the number of
    feedback samples observed so far."""
    ctx = get_ctx()
    conn = ctx.get_conn()
    default_threshold = cfg_get(ctx.config, "clip_similarity_threshold")
    threshold, info = compute_adaptive_threshold(conn, default=default_threshold)
    feedback = get_dedup_feedback(conn)
    return jsonify(
        {
            "threshold": threshold,
            "default_threshold": default_threshold,
            "info": info,
            "feedback_count": len(feedback),
        }
    ), 200

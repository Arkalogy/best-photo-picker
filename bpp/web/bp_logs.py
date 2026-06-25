"""Activity log API — exposes recent log entries and log management."""

from __future__ import annotations

import gc
import glob
import os
import resource
import sys
import threading

from flask import Blueprint, Response, jsonify, request

from bpp.utils.logging import get_logger, get_memory_handler
from bpp.web.share import requires_local_app
from bpp.web.state import get_ctx

bp = Blueprint("logs", __name__)
log = get_logger(__name__)

# Dedicated logger so client-side errors are unmistakable in the Activity
# log (source reads "bpp.web.client"), distinct from server-side warnings.
client_log = get_logger("bpp.web.client")

# Length caps — a client error must never be able to flood the ring buffer /
# server.log with a giant stack. Generous enough to keep the useful frames.
_CLIENT_ERR_MSG_MAX = 500
_CLIENT_ERR_SRC_MAX = 300
_CLIENT_ERR_STACK_MAX = 2000


@bp.post("/api/v1/client-error")
@requires_local_app
def api_client_error() -> tuple[Response, int]:
    """Ingest an uncaught CLIENT-side JS error into the server log so it
    shows in the Activity log (Settings -> Activity).

    Without this, window.onerror / unhandledrejection errors lived only in
    the browser console — invisible to the operator and impossible to
    diagnose from server.log. The frontend beacons here (throttled,
    fire-and-forget) from its global error boundary.

    LOCAL_APP-only and length-capped: the payload is owner-supplied JS
    diagnostics (message, source URL, stack), never photo data. The fields
    are clamped so a render-loop can't flood the log; RedactingFormatter
    still scrubs any token-shaped substring.
    """
    data = request.get_json(silent=True) or {}

    def _clip(val: object, n: int) -> str:
        s = str(val) if val is not None else ""
        return s[:n]

    message = _clip(data.get("message"), _CLIENT_ERR_MSG_MAX) or "(no message)"
    source = _clip(data.get("source"), _CLIENT_ERR_SRC_MAX)
    stack = _clip(data.get("stack"), _CLIENT_ERR_STACK_MAX)
    try:
        lineno = int(data.get("lineno") or 0)
        colno = int(data.get("colno") or 0)
    except (TypeError, ValueError):
        lineno = colno = 0

    loc = f" ({source}:{lineno}:{colno})" if source else ""
    # Collapse the stack to one line so the Activity-log row stays readable;
    # the frames are still there for grep.
    stack_one = " | ".join(s.strip() for s in stack.splitlines() if s.strip())
    client_log.warning(
        "Client-side error: %s%s%s",
        message,
        loc,
        f" :: {stack_one}" if stack_one else "",
    )
    return jsonify({"status": "logged"}), 200


@bp.get("/api/v1/logs")
@requires_local_app
def api_logs() -> tuple[Response, int]:
    """Return recent log entries from the in-memory ring buffer.

    Query params:
        since  — unix timestamp; only entries after this time
        level  — minimum level filter (info, warning, error)
        limit  — max entries to return (default 200)

    LOCAL_APP-only — log entries can contain filesystem paths,
    error stack traces with internal state, and operational
    detail that's owner/operator territory. RedactingFormatter
    scrubs known token shapes, but the path-shaped content (DB
    paths, library paths, thread names) shouldn't go to LAN.
    """
    handler = get_memory_handler()
    if handler is None:
        return jsonify({"entries": [], "count": 0}), 200

    since = request.args.get("since", type=float)
    level = request.args.get("level")
    limit = request.args.get("limit", 200, type=int)
    limit = max(1, min(limit, 1000))

    entries = handler.get_entries(since=since, level=level, limit=limit)
    return jsonify({"entries": entries, "count": len(entries)}), 200


@bp.post("/api/v1/logs/clear")
@requires_local_app
def api_logs_clear() -> tuple[Response, int]:
    """Clear the in-memory buffer and truncate server.log files.

    LOCAL_APP-only — wipes audit/diagnostic state. A LAN device
    must not be able to erase the operator's evidence trail."""
    handler = get_memory_handler()
    if handler is not None:
        handler.clear()

    # Truncate log files on disk
    ctx = get_ctx()
    logs_dir = ctx.dirs.get("logs", "")
    cleared = 0
    if logs_dir and os.path.isdir(logs_dir):
        for path in glob.glob(os.path.join(logs_dir, "server.log*")):
            try:
                with open(path, "w"):
                    pass  # truncate
                cleared += 1
            except OSError:
                log.warning("Failed to clear log file: %s", path)

    log.info("Activity log cleared (%d file(s) truncated)", cleared)
    return jsonify({"status": "cleared", "files": cleared}), 200


@bp.get("/api/v1/debug/memory")
@requires_local_app
def api_debug_memory() -> tuple[Response, int]:
    """Snapshot of major in-memory structures for memory-leak diagnostics.

    Reports process RSS, sizes of all significant caches, GC generation
    counts and uncollectable garbage, and active thread count. Use this
    to identify which structure is growing across a long-running session.

    LOCAL_APP-only — exposes internal state not suitable for LAN devices.
    """
    ctx = get_ctx()

    # ── Process RSS ──────────────────────────────────────────────────────────
    # resource.ru_maxrss is bytes on macOS, kilobytes on Linux.
    ru = resource.getrusage(resource.RUSAGE_SELF)
    rss_bytes = ru.ru_maxrss
    if sys.platform != "darwin":
        rss_bytes *= 1024
    rss_mb = round(rss_bytes / (1024 * 1024), 1)

    # ── Cache sizes ──────────────────────────────────────────────────────────
    clip_emb = ctx.caches.clip_cache.get("embeddings") or {}
    clip_count = len(clip_emb)
    # ViT-B/32: 512 float32 values per embedding
    clip_est_mb = round(clip_count * 512 * 4 / (1024 * 1024), 2)

    thumbs = ctx.thumbs
    thumb_hash_count = len(thumbs._hash_to_path) if thumbs else 0
    thumb_verified_count = len(thumbs._verified) if thumbs else 0

    with ctx._face_cluster_map_lock:
        fcm = ctx._face_cluster_map
    face_cluster_entries = len(fcm) if fcm is not None else -1  # -1 = not loaded

    edited_ids = ctx.caches.enhanced_ids.edited
    auto_ids = ctx.caches.enhanced_ids.auto_enhanced

    handler = get_memory_handler()
    log_ring = len(handler.buffer) if handler else 0

    from bpp.scoring.clip_tokenizer import _bpe

    bpe_cache_info = _bpe.cache_info()
    bpe_cache_size = bpe_cache_info.currsize

    # ── GC stats ─────────────────────────────────────────────────────────────
    gc.collect()
    gc_counts = gc.get_count()
    garbage_count = len(gc.garbage)

    return jsonify(
        {
            "process": {
                "rss_mb": rss_mb,
                "platform": sys.platform,
            },
            "caches": {
                "clip_embeddings_count": clip_count,
                "clip_embeddings_est_mb": clip_est_mb,
                "thumb_hash_count": thumb_hash_count,
                "thumb_verified_count": thumb_verified_count,
                "face_cluster_map_entries": face_cluster_entries,
                "edited_ids_count": len(edited_ids) if edited_ids is not None else -1,
                "auto_enhanced_ids_count": len(auto_ids) if auto_ids is not None else -1,
                "log_ring_buffer": log_ring,
                "bpe_cache_size": bpe_cache_size,
            },
            "gc": {
                "garbage_objects": garbage_count,
                "collections": {"gen0": gc_counts[0], "gen1": gc_counts[1], "gen2": gc_counts[2]},
            },
            "threads": {
                "active": threading.active_count(),
                "names": [t.name for t in threading.enumerate()],
            },
        }
    ), 200

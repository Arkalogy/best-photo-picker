"""Heavy WebAppState method bodies — refresh_thumb_map, auto_purge,
build_photo_dict, check_dedup_feedback.

Extracted from :mod:`bpp.web.state` as part of the 500-LOC cap split.
WebAppState retains thin delegate methods that call into the
module-level functions here; the dispatch shape lets tests and plugins
keep monkey-patching ``ctx.<method>`` if needed while the implementation
stays out of ``state.py``.

This module is intentionally state-free: every function takes the
``WebAppState`` (typed as ``Any`` to avoid the circular import at
module load) as its first argument. The collaborators (``ctx.caches``,
``ctx.lock``, ``ctx.thumbs``, ``ctx.config``, ``ctx.state``) are
accessed on the instance.
"""

from __future__ import annotations

import os
import sqlite3
from typing import TYPE_CHECKING, Any

from bpp.db.clip import record_dedup_feedback
from bpp.db.photos import purge_old_deleted
from bpp.scoring.clip_embed import cosine_similarity as clip_cosine_similarity
from bpp.utils.logging import get_logger
from bpp.web.photo_dict import build_photo_dict as _build_photo_dict_impl
from bpp.web.thumbnails import ThumbnailCache

if TYPE_CHECKING:
    from bpp.web.state import WebAppState

log = get_logger(__name__)


def refresh_thumb_map(ctx: WebAppState) -> None:
    """Rebuild thumbnail path→hash map from DB (lightweight)."""
    try:
        conn = ctx.get_conn()
        rows = conn.execute("SELECT filepath FROM photos WHERE missing=0").fetchall()
        filepaths = [r["filepath"] for r in rows]
        if ctx.thumbs is None:
            ctx.thumbs = ThumbnailCache(ctx.dirs["thumbs"])
        ctx.thumbs.build_map_from_paths(filepaths)
    except Exception:
        log.warning("Failed to refresh thumbnail map", exc_info=True)


def auto_purge(ctx: WebAppState) -> None:
    """Purge photos deleted more than 30 days ago."""
    # shared allowlist helper.
    from bpp.utils.path_validation import build_library_allowlist, is_path_under_any

    try:
        conn = ctx.get_conn()
        filepaths = purge_old_deleted(conn, days=30)
        allowed = build_library_allowlist(library_path=ctx.state["library_path"])
        for fp in filepaths:
            try:
                if not is_path_under_any(fp, allowed):
                    log.warning("Skipping purge of file outside library: %s", fp)
                    continue
                if os.path.isfile(fp):
                    os.remove(fp)
            except OSError:
                log.warning("Failed to remove file during auto-purge: %s", fp)
        if filepaths:
            log.info("Auto-purged %d photos deleted >30 days ago", len(filepaths))
    except Exception:
        # include the traceback so a
        # silent auto-purge failure isn't an unsolvable mystery.
        # The previous shape only printed the exception's value
        # which omits the failing frame's file/line.
        log.warning("Auto-purge failed", exc_info=True)


def build_photo_dict(
    ctx: WebAppState,
    item: dict[str, Any],
    selected: bool | None = None,
) -> dict[str, Any]:
    from bpp.constants import SENSITIVE_NUDITY_THRESHOLD

    threshold = ctx.config.get("sensitive_nudity_threshold", SENSITIVE_NUDITY_THRESHOLD)
    d = _build_photo_dict_impl(item, ctx.thumbs, selected, sensitive_threshold=threshold)
    # Lazy-fill the enhanced/auto-enhanced caches under ctx.lock so a
    # concurrent invalidate_enhanced_cache() can't null the field
    # between the `is None` check and the membership test below
    # (which would raise TypeError on `<x> in None`).
    with ctx.lock:
        cache = ctx.caches.enhanced_ids
        if not cache.both_loaded():
            cache.load(ctx.get_conn())
        edited = cache.edited
        auto = cache.auto_enhanced
    # Both fields are guaranteed non-None inside the lock; assign
    # locally so the type checker tracks them through the membership
    # test.
    assert edited is not None and auto is not None
    d["_enhanced"] = item.get("id") in edited
    d["_auto_enhanced"] = item.get("id") in auto
    return d


def check_dedup_feedback(
    ctx: WebAppState,
    conn: sqlite3.Connection,
    photo_id: int,
    filepath: str,
    mode: str | None,
    selected_paths: set[str] | None,
    album_id: int | None = None,
) -> bool:
    """Check if an override constitutes dedup feedback and record it."""
    if mode not in ("include", "exclude"):
        return False
    if not selected_paths:
        return False

    with ctx.lock:
        if not ctx.caches.clip_cache["ready"] or not ctx.caches.clip_cache["embeddings"]:
            return False
        embs = ctx.caches.clip_cache["embeddings"]
        analysis = ctx.state["analysis"]
    photo_emb = embs.get(photo_id)
    if photo_emb is None:
        return False

    threshold = ctx.config.get("clip_similarity_threshold", 0.92)
    verdict = "same" if mode == "exclude" else "different"
    recorded = False

    fp_to_id: dict[str, int] = {}
    if analysis:
        for item in analysis:
            pid = item.get("id")
            if pid is not None:
                fp_to_id[item["filepath"]] = pid

    for sel_fp in selected_paths:
        if sel_fp == filepath:
            continue
        sel_id = fp_to_id.get(sel_fp)
        if sel_id is None:
            continue
        sel_emb = embs.get(sel_id)
        if sel_emb is None:
            continue
        sim = clip_cosine_similarity(photo_emb, sel_emb)
        if sim >= threshold * 0.9:
            record_dedup_feedback(conn, photo_id, sel_id, sim, verdict, album_id)
            recorded = True
            log.info(
                "Dedup feedback: %s vs %s sim=%.3f verdict=%s",
                os.path.basename(filepath),
                os.path.basename(sel_fp),
                sim,
                verdict,
            )
            break

    return recorded

"""End-of-analyze bookkeeping helpers for the AnalyzeWorker.

Extracted from analyze_worker.py when the LOC gate caught it over the
500-line cap (S4, 2026-06-12) — the same change that moved finalize
INTO the worker: an analyze with no SSE consumer on
/api/v1/analyze/progress previously never invalidated the analysis
cache or reloaded CLIP, leaving the app serving stale data until the
next restart.
"""

from __future__ import annotations

import sqlite3

from bpp.utils.logging import get_logger

log = get_logger(__name__)


def count_sensitive_alert(pp_conn: sqlite3.Connection) -> tuple[int, int]:
    """Return (flagged, new_since_last_alert) for sensitive photos.

    Count-delta (not an ID diff) so re-analyzing an unchanged library
    doesn't re-nag about the same photos; a simultaneous flag+clear
    cancelling out is an acceptable miss for an advisory toast.
    Persists the new count as the alert watermark.
    """
    sensitive_flagged = 0
    sensitive_new = 0
    try:
        from bpp.constants import ACTIVE_PHOTO_SQL, sensitive_photo_sql
        from bpp.db.settings import get_setting, resolve_sensitive_threshold, set_setting

        predicate = sensitive_photo_sql(resolve_sensitive_threshold(pp_conn))
        sensitive_flagged = pp_conn.execute(
            f"SELECT COUNT(*) FROM photos WHERE {predicate} AND {ACTIVE_PHOTO_SQL}"
        ).fetchone()[0]
        last = int(get_setting(pp_conn, "sensitive_last_alerted_count") or 0)
        sensitive_new = max(0, sensitive_flagged - last)
        set_setting(pp_conn, "sensitive_last_alerted_count", str(sensitive_flagged))
        if sensitive_new > 0:
            # Raw line for the activity feed; the dropdown's
            # humanizer rule rewrites it user-friendly.
            log.info(
                "Flagged %d new photo(s) as possibly sensitive (%d total)",
                sensitive_new,
                sensitive_flagged,
            )
    except Exception:
        log.warning("Sensitive-photo count failed", exc_info=True)
    return sensitive_flagged, sensitive_new


def finalize_in_worker(clip_computed: int) -> None:
    """Invalidate + reload the analysis cache (and CLIP when it changed).

    Runs in the worker thread itself so a headless analyze finalizes
    without anyone consuming the progress stream. The SSE on_done hook
    stays as an idempotent backstop.
    """
    try:
        from bpp.web.state import get_ctx_or_none
        from bpp.web.state_init import load_analysis_if_needed

        ctx = get_ctx_or_none()
        if ctx is None:
            return
        ctx.invalidate_analysis()
        # kick_recovery=False: the worker already kicked its own
        # pipeline — re-kicking here would queue a redundant re-run.
        load_analysis_if_needed(ctx, kick_recovery=False)
        if clip_computed > 0:
            with ctx.lock:
                ctx.caches.clip_cache["ready"] = False
            ctx.load_clip_embeddings()
    except Exception:
        log.warning("Post-analyze finalize failed", exc_info=True)

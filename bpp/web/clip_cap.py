"""The CLIP memory-cap surface ``/api/v1/status`` and the cap-override
endpoint both report."""

from __future__ import annotations

from typing import Any

from bpp.db.clip import (
    CLIP_EMBEDDING_BYTES,
    CLIP_EMBEDDING_MAX_ROWS,
    CLIP_MAX_OVERRIDE_BYPASS,
    CLIP_MAX_OVERRIDE_KEY,
)
from bpp.db.settings import get_setting
from bpp.utils.logging import get_logger

log = get_logger(__name__)


def clip_cap_status(conn: Any, clip_count: int) -> dict[str, Any]:
    """Always returns the same keys so the frontend doesn't have to
    branch on presence. Peak MB is the realistic ~3x footprint at
    the current row count, not the cap — that's what the user
    actually has to budget against when deciding whether to enable."""
    cap = CLIP_EMBEDDING_MAX_ROWS
    overridden = False
    if conn is not None:
        try:
            overridden = get_setting(conn, CLIP_MAX_OVERRIDE_KEY) == CLIP_MAX_OVERRIDE_BYPASS
        except Exception:
            log.debug("clip_max_override lookup failed", exc_info=True)
    over_cap = clip_count > cap
    if not over_cap:
        state = "enabled"
    elif overridden:
        state = "enabled_override"
    else:
        state = "disabled_too_large"
    # 3x one matrix: search matrix + dedup scratch + a concurrent
    # Moments/smart-album load (see ClipEmbeddingsTooLarge).
    peak_mb = (clip_count * CLIP_EMBEDDING_BYTES / (1024 * 1024)) * 3
    return {
        "clip_cap": cap,
        "clip_cap_status": state,
        "clip_cap_peak_mb": int(peak_mb),
    }

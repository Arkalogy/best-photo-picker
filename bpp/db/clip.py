"""CRUD operations for CLIP embeddings and dedup feedback."""

from __future__ import annotations

import os
import sqlite3
from typing import Any

import numpy as np

from bpp.constants import CLIP_MODEL_NAME
from bpp.errors import ResourceExhaustedError as _ResourceExhaustedError
from bpp.utils.logging import get_logger

log = get_logger(__name__)

CLIP_EMBEDDING_DIM = 512
CLIP_EMBEDDING_BYTES = CLIP_EMBEDDING_DIM * 4  # float32

# Hard ceiling on the CLIP embedding matrix RAM allocation.
# CLIP_EMBEDDING_BYTES * N = ~2 KB/photo. 200K photos is about
# 400 MB per dense matrix. Peak runtime footprint is ~3x that:
# the embedding dict, the semantic-search matrix, and one
# semantic-dedup scratch matrix can be in memory at once
# (~1.2 GB at the 200K cap). Above the cap the load is refused
# and CLIP semantic-dedupe is skipped rather than OOM-killing
# the server.
#
# Three knobs control the effective cap, checked in this order:
#  1. Per-library DB setting `clip_max_override = "bypass"` —
#     written when the user clicks "Enable anyway" in the Settings
#     banner. Disables the cap entirely for that library.
#  2. BPP_CLIP_MAX_PHOTOS env var — raises (or lowers) the
#     numeric cap at server-start time. CLI / dev users still
#     have this path; Tauri users get the DB-override path.
#  3. Default 200_000 — covers the comfortable case on a 16 GB
#     machine with browser + OS sharing memory.
_env_max = os.environ.get("BPP_CLIP_MAX_PHOTOS", "")
CLIP_EMBEDDING_MAX_ROWS: int = int(_env_max) if _env_max.isdigit() else 200_000

# Sentinel value stored in the settings table when the user
# explicitly enables CLIP semantic dedup despite being over the cap.
# Constant so callers (status endpoint, override endpoint, the cap
# check itself) all reference the same key.
CLIP_MAX_OVERRIDE_KEY = "clip_max_override"
CLIP_MAX_OVERRIDE_BYPASS = "bypass"


def _clip_cap_overridden(conn: sqlite3.Connection) -> bool:
    """Return True when the per-library override is set to 'bypass'.

    Imported lazily to avoid a circular import between this module
    and bpp.db.settings (settings.py is import-light; this file is
    not, so the cycle would resolve here at runtime).
    """
    from bpp.db.settings import get_setting

    return get_setting(conn, CLIP_MAX_OVERRIDE_KEY) == CLIP_MAX_OVERRIDE_BYPASS


# ---------------------------------------------------------------------------
# CLIP embeddings
# ---------------------------------------------------------------------------


def upsert_clip_embedding(
    conn: sqlite3.Connection,
    photo_id: int,
    embedding: np.ndarray,
    model_name: str = CLIP_MODEL_NAME,
) -> None:
    """Insert or replace a CLIP embedding for a photo."""
    blob = embedding.astype(np.float32).tobytes()
    conn.execute(
        "INSERT INTO clip_embeddings (photo_id, model_name, embedding) VALUES (?, ?, ?)"
        " ON CONFLICT(photo_id, model_name) DO UPDATE SET embedding=excluded.embedding,"
        " computed_at=datetime('now')",
        (photo_id, model_name, blob),
    )
    conn.commit()


class ClipEmbeddingsTooLarge(_ResourceExhaustedError, RuntimeError):
    """Raised when a library exceeds `CLIP_EMBEDDING_MAX_ROWS`.

    Surfaces the count + cap so the caller (WebAppState.load_clip_embeddings)
    can log a helpful message + degrade gracefully (skip CLIP-based
    semantic dedupe rather than crash the server with OOM).

    P7: see :class:`bpp.db.face_queries.FaceEmbeddingsTooLarge` for
    the same back-compat pattern.
    """

    code = "clip_embeddings_too_large"

    def __init__(self, count: int, cap: int) -> None:
        self.count = count
        self.cap = cap
        # Peak footprint is ~3x the embedding dict: dict + cached
        # search matrix + active dedup scratch matrix can all be live
        # at once. Report the peak so users can size BPP_CLIP_MAX_PHOTOS
        # against their actual RAM, not against the dict alone.
        dict_mb = count * CLIP_EMBEDDING_BYTES / (1024 * 1024)
        peak_mb = dict_mb * 3
        super().__init__(
            f"CLIP embedding table has {count} rows, above the {cap} cap. "
            f"Peak load would be ~{peak_mb:.0f} MB (dict + search matrix + "
            f"dedup scratch ≈ 3x {dict_mb:.0f} MB). "
            "Refusing to load to avoid OOM."
        )

    def __reduce__(self) -> tuple:
        """T1.5: see :meth:`bpp.db.face_queries.FaceEmbeddingsTooLarge.__reduce__`.

        Same multi-positional-arg __init__ pickle trap; same fix.
        """
        return (self.__class__, (self.count, self.cap))


def get_all_clip_embeddings(
    conn: sqlite3.Connection,
    model_name: str = CLIP_MODEL_NAME,
) -> dict[int, np.ndarray]:
    """Get all CLIP embeddings as {photo_id: embedding} dict.

    refuses to load when the row count exceeds
    `CLIP_EMBEDDING_MAX_ROWS`. Without the guard, a 1M-photo library
    would allocate ~2GB just for the embedding dict, plus another
    ~2GB for the np.stack matrix in `WebAppState.load_clip_embeddings`,
    and the process would OOM mid-recompute. Callers should catch
    `ClipEmbeddingsTooLarge` and degrade to skip-CLIP semantic dedupe.
    """
    count = get_clip_embedding_count(conn, model_name=model_name)
    if count > CLIP_EMBEDDING_MAX_ROWS and not _clip_cap_overridden(conn):
        raise ClipEmbeddingsTooLarge(count, CLIP_EMBEDDING_MAX_ROWS)

    rows = conn.execute(
        "SELECT photo_id, embedding FROM clip_embeddings WHERE model_name = ?",
        (model_name,),
    ).fetchall()
    result: dict[int, np.ndarray] = {}
    for row in rows:
        photo_id, blob = row[0], row[1]
        if len(blob) != CLIP_EMBEDDING_BYTES:
            log.warning(
                "Skipping malformed CLIP embedding for photo_id=%s: %d bytes (expected %d)",
                photo_id,
                len(blob),
                CLIP_EMBEDDING_BYTES,
            )
            continue
        emb = np.frombuffer(blob, dtype=np.float32).copy()
        if emb.shape != (CLIP_EMBEDDING_DIM,):
            log.warning(
                "Skipping malformed CLIP embedding for photo_id=%s: shape=%s",
                photo_id,
                emb.shape,
            )
            continue
        result[photo_id] = emb
    return result


def get_clip_embedding_count(
    conn: sqlite3.Connection,
    model_name: str = CLIP_MODEL_NAME,
) -> int:
    """Count how many photos have CLIP embeddings."""
    row = conn.execute(
        "SELECT COUNT(*) FROM clip_embeddings WHERE model_name = ?",
        (model_name,),
    ).fetchone()
    return row[0] if row else 0


# ---------------------------------------------------------------------------
# Dedup feedback
# ---------------------------------------------------------------------------


def record_dedup_feedback(
    conn: sqlite3.Connection,
    photo_id_a: int,
    photo_id_b: int,
    similarity: float,
    verdict: str,
    album_id: int | None = None,
) -> None:
    """Record user feedback on whether two photos are duplicates.

    Pair ordering is normalized (min, max) to avoid duplicate entries.
    verdict must be 'same' or 'different'.
    """
    if verdict not in ("same", "different"):
        raise ValueError(f"verdict must be 'same' or 'different', got {verdict!r}")
    # Normalize ordering
    id_a, id_b = min(photo_id_a, photo_id_b), max(photo_id_a, photo_id_b)
    # SQLite treats NULL != NULL for UNIQUE constraints, so delete+insert for NULL album_id
    if album_id is None:
        conn.execute(
            "DELETE FROM dedup_feedback WHERE photo_id_a=? AND photo_id_b=? AND album_id IS NULL",
            (id_a, id_b),
        )
    conn.execute(
        "INSERT INTO dedup_feedback (photo_id_a, photo_id_b, similarity, verdict, album_id)"
        " VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT(photo_id_a, photo_id_b, album_id)"
        " DO UPDATE SET similarity=excluded.similarity, verdict=excluded.verdict,"
        " created_at=datetime('now')",
        (id_a, id_b, similarity, verdict, album_id),
    )
    conn.commit()


def get_dedup_feedback(
    conn: sqlite3.Connection,
    album_id: int | None = None,
) -> list[dict[str, Any]]:
    """Get all dedup feedback records, optionally filtered by album."""
    if album_id is not None:
        rows = conn.execute(
            "SELECT photo_id_a, photo_id_b, similarity, verdict, album_id, created_at"
            " FROM dedup_feedback WHERE album_id = ?",
            (album_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT photo_id_a, photo_id_b, similarity, verdict, album_id, created_at"
            " FROM dedup_feedback",
        ).fetchall()
    return [
        {
            "photo_id_a": r[0],
            "photo_id_b": r[1],
            "similarity": r[2],
            "verdict": r[3],
            "album_id": r[4],
            "created_at": r[5],
        }
        for r in rows
    ]


def compute_adaptive_threshold(
    conn: sqlite3.Connection,
    default: float = 0.92,
    album_id: int | None = None,
) -> tuple[float, dict[str, Any]]:
    """Compute the optimal dedup threshold from accumulated feedback.

    Returns (threshold, info) where info contains metadata for UI display.

    Algorithm:
    - Collect all feedback pairs with their similarity scores
    - Find the boundary between 'same' and 'different' verdicts
    - Smooth toward default based on sample count (confidence)
    """
    feedback = get_dedup_feedback(conn, album_id=album_id)
    info: dict[str, Any] = {
        "feedback_count": len(feedback),
        "same_count": 0,
        "different_count": 0,
        "confidence": 0.0,
        "source": "default",
    }

    if not feedback:
        return default, info

    same_sims = [f["similarity"] for f in feedback if f["verdict"] == "same"]
    diff_sims = [f["similarity"] for f in feedback if f["verdict"] == "different"]
    info["same_count"] = len(same_sims)
    info["different_count"] = len(diff_sims)

    margin = 0.02

    if same_sims and diff_sims:
        s_same_min = min(same_sims)
        s_diff_max = max(diff_sims)
        # Clean separation: midpoint. Overlap: bias toward keeping photos.
        computed = (s_diff_max + s_same_min) / 2 if s_diff_max < s_same_min else s_diff_max + margin
        info["source"] = "learned"
    elif same_sims:
        computed = min(same_sims) - margin
        info["source"] = "learned (same only)"
    else:
        computed = max(diff_sims) + margin
        info["source"] = "learned (different only)"

    # Clamp to reasonable range
    computed = max(0.75, min(0.98, computed))

    # Smooth toward default based on sample count
    n = len(feedback)
    alpha = min(1.0, n / 20.0)
    info["confidence"] = round(alpha, 2)

    threshold = alpha * computed + (1.0 - alpha) * default
    return round(threshold, 4), info

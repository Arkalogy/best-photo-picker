"""Document smart album refresher (CLIP-based).

Extracted from :mod:`bpp.db.smart_album_refreshers` as part of the
500-LOC cap enforcement. Documents are detected via CLIP text-to-
image similarity against a fixed prompt — none of the other
built-in refreshers need the CLIP stack, so isolating this one
keeps the rest of the refreshers free of the bpp.scoring.clip_embed
import (and its lazy ML model load).

smart_album_refreshers re-exports ``_refresh_document_album`` so the
SmartAlbumRegistry built-in registration block in smart_albums.py
keeps working unchanged.
"""

from __future__ import annotations

import sqlite3

from bpp.constants import ACTIVE_PHOTO_SQL
from bpp.db.smart_album_queries import DOCUMENT_CLIP_THRESHOLD, DOCUMENT_PROMPT
from bpp.utils.logging import get_logger

# _ensure_smart_album / _remove_smart_album_if_exists are imported lazily
# inside the refresh function to break the circular dependency: this
# module is imported by smart_album_refreshers (which defines those
# helpers), so we can't import them at module load.

log = get_logger(__name__)

_ACTIVE = ACTIVE_PHOTO_SQL


def _refresh_document_album(conn: sqlite3.Connection) -> None:
    """Create or remove a 'Documents' smart album using CLIP semantic matching."""

    import numpy as np

    from bpp.db.smart_album_refreshers import (
        _ensure_smart_album,
        _remove_smart_album_if_exists,
    )

    try:
        from bpp.db.clip import get_all_clip_embeddings
        from bpp.scoring.clip_embed import compute_text_embedding
    except ImportError:
        return

    embeddings = get_all_clip_embeddings(conn)
    if not embeddings:
        _remove_smart_album_if_exists(conn, "smart_document", {"document": True})
        return

    text_emb = compute_text_embedding(DOCUMENT_PROMPT)
    if text_emb is None:
        return

    # Vectorized cosine similarity (embeddings are L2-normalized)
    photo_ids_list = list(embeddings.keys())
    emb_matrix = np.stack([embeddings[pid] for pid in photo_ids_list])
    similarities = emb_matrix @ text_emb
    threshold = DOCUMENT_CLIP_THRESHOLD
    matching_ids = [
        photo_ids_list[i] for i in range(len(photo_ids_list)) if similarities[i] >= threshold
    ]

    # Filter to active photos only
    if matching_ids:
        placeholders = ", ".join(["?"] * len(matching_ids))
        rows = conn.execute(
            f"SELECT id FROM photos WHERE id IN ({placeholders}) AND {_ACTIVE}",
            matching_ids,
        ).fetchall()
        photo_ids = [r[0] for r in rows]
    else:
        photo_ids = []

    if photo_ids:
        _ensure_smart_album(
            conn,
            name="Documents",
            album_type="smart_document",
            rule={"document": True},
            photo_ids=photo_ids,
        )
    else:
        _remove_smart_album_if_exists(conn, "smart_document", {"document": True})

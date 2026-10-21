"""Memories blueprint: auto-generated photo stories."""

from __future__ import annotations

from flask import Blueprint, Response, jsonify

from bpp.constants import ACTIVE_PHOTO_SQL
from bpp.db.memories import generate_memories, get_memory, list_memories
from bpp.db.sql_batches import in_batches
from bpp.errors import NotFoundError
from bpp.utils.logging import get_logger
from bpp.web.photo_dict import build_photo_brief
from bpp.web.share import requires_local_app
from bpp.web.state import get_ctx

log = get_logger(__name__)

bp = Blueprint("memories", __name__)


@bp.get("/api/v1/memories")
def api_memories_list() -> tuple[Response, int]:
    """Return the list of saved memory cards (auto-generated photo
    stories) with title, cover photo, and photo counts. Use the
    detail endpoint to fetch the photos in a specific memory."""
    ctx = get_ctx()
    conn = ctx.get_conn()
    memories = list_memories(conn)
    return jsonify({"memories": memories}), 200


@bp.get("/api/v1/memories/<int:memory_id>")
def api_memories_detail(memory_id: int) -> tuple[Response, int]:
    """Get a single memory with its photos."""
    ctx = get_ctx()
    conn = ctx.get_conn()
    m = get_memory(conn, memory_id)
    if m is None:
        raise NotFoundError("Memory not found", memory_id=memory_id)
    # Fetch photo details — batch to avoid SQLite variable limit.
    # Re-check ACTIVE_PHOTO_SQL at resolution time: a memory's photo_ids are
    # a snapshot from generation, and a photo can become inactive AFTER the
    # memory was built (deleted, hidden, or — the bug that surfaced this —
    # tagged as a Live Photo sidecar by the phash backfill). Stored id lists
    # must never bypass the active filter.
    photo_ids = m.get("photo_ids") or []
    all_rows = []
    for placeholders, batch in in_batches(photo_ids):
        rows = conn.execute(
            f"SELECT id, filepath, date, aggregate_score, original_filename "
            f"FROM photos WHERE id IN ({placeholders}) AND {ACTIVE_PHOTO_SQL} "
            f"ORDER BY date",
            batch,
        ).fetchall()
        all_rows.extend(rows)
    m["photos"] = [build_photo_brief(dict(r), ctx.thumbs) for r in all_rows]
    return jsonify(m), 200


@bp.post("/api/v1/memories/refresh")
@requires_local_app
def api_memories_refresh() -> tuple[Response, int]:
    """Regenerate all memories from current photo data."""
    ctx = get_ctx()
    conn = ctx.get_conn()
    memories = generate_memories(conn)
    return jsonify({"count": len(memories), "memories": memories}), 200

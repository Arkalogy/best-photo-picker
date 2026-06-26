"""Memories blueprint: auto-generated photo stories."""

from __future__ import annotations

from flask import Blueprint, Response, jsonify

from bpp.constants import ACTIVE_PHOTO_SQL
from bpp.db.memories import generate_memories, get_memory, list_memories
from bpp.errors import NotFoundError
from bpp.utils.logging import get_logger
from bpp.web.share import requires_local_app
from bpp.web.state import get_ctx

log = get_logger(__name__)

bp = Blueprint("memories", __name__)

_BATCH_SIZE = 500


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
    for i in range(0, len(photo_ids), _BATCH_SIZE):
        batch = photo_ids[i : i + _BATCH_SIZE]
        placeholders = ",".join("?" * len(batch))
        rows = conn.execute(
            f"SELECT id, filepath, sha256, date, aggregate_score "
            f"FROM photos WHERE id IN ({placeholders}) AND {ACTIVE_PHOTO_SQL} "
            f"ORDER BY date",
            batch,
        ).fetchall()
        all_rows.extend(rows)
    m["photos"] = [
        {
            "id": r["id"],
            "filepath": r["filepath"],
            "hash": r["sha256"],
            "date": r["date"],
            "score": r["aggregate_score"],
        }
        for r in all_rows
    ]
    return jsonify(m), 200


@bp.post("/api/v1/memories/refresh")
@requires_local_app
def api_memories_refresh() -> tuple[Response, int]:
    """Regenerate all memories from current photo data."""
    ctx = get_ctx()
    conn = ctx.get_conn()
    memories = generate_memories(conn)
    return jsonify({"count": len(memories), "memories": memories}), 200

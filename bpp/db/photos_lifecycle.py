"""Photo lifecycle operations — soft delete / restore / hide / unhide /
permanent delete, plus paginated read helpers for the Trash and Hidden
views.

Extracted from ``bpp.db.photos`` during the v0.1 cleanup. Re-exported
from ``bpp.db.photos`` for back-compat with the dozens of callers that
import these symbols directly.

Post-commit hooks for plugins are dispatched via
``bpp.db.photo_hooks.dispatch_photo_deletion``.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from bpp.db.sql_batches import in_batches
from bpp.utils.logging import get_logger

log = get_logger(__name__)


def soft_delete_photos(conn: sqlite3.Connection, photo_ids: list[int]) -> int:
    """Soft-delete photos by setting deleted_at. Returns count deleted."""
    if not photo_ids:
        return 0
    affected_ids: list[int] = []
    for ph, batch in in_batches(photo_ids):
        rows = conn.execute(
            f"SELECT id FROM photos WHERE id IN ({ph}) AND deleted_at IS NULL", batch
        ).fetchall()
        affected_ids.extend(int(r[0]) for r in rows)
    if not affected_ids:
        log.info("Soft-deleted 0 photos (requested %d)", len(photo_ids))
        return 0
    deleted = 0
    for ph, batch in in_batches(affected_ids):
        deleted += conn.execute(
            f"UPDATE photos SET deleted_at=datetime('now') WHERE id IN ({ph})", batch
        ).rowcount
    conn.commit()
    log.info("Soft-deleted %d photos (requested %d)", deleted, len(photo_ids))
    # Post-commit hook dispatch for plugins. See bpp/db/photo_hooks.py.
    from bpp.db.photo_hooks import dispatch_photo_deletion

    dispatch_photo_deletion(conn, affected_ids, "soft")
    return deleted


def restore_photos(conn: sqlite3.Connection, photo_ids: list[int]) -> int:
    """Restore soft-deleted photos. Returns count restored."""
    if not photo_ids:
        return 0
    affected_ids: list[int] = []
    for ph, batch in in_batches(photo_ids):
        rows = conn.execute(
            f"SELECT id FROM photos WHERE id IN ({ph}) AND deleted_at IS NOT NULL", batch
        ).fetchall()
        affected_ids.extend(int(r[0]) for r in rows)
    if not affected_ids:
        log.info("Restored 0 photos (requested %d)", len(photo_ids))
        return 0
    restored = 0
    for ph, batch in in_batches(affected_ids):
        restored += conn.execute(
            f"UPDATE photos SET deleted_at=NULL WHERE id IN ({ph})", batch
        ).rowcount
    conn.commit()
    log.info("Restored %d photos (requested %d)", restored, len(photo_ids))
    from bpp.db.photo_hooks import dispatch_photo_deletion

    dispatch_photo_deletion(conn, affected_ids, "restore")
    return restored


def get_deleted_photos(
    conn: sqlite3.Connection,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Get soft-deleted photos, most-recently-deleted first.

    When ``limit`` is None, returns the full list (legacy callers).
    When ``limit`` is an int, returns at most that many rows starting
    at ``offset`` — caller is expected to pair with ``count_deleted_photos``
    for the total.
    """
    # Lazy import to avoid a circular import (photos.py imports this module
    # at the bottom for re-export).
    from bpp.db.photos import PHOTO_COLS_SLIM

    sql = (
        f"SELECT {PHOTO_COLS_SLIM} FROM photos WHERE deleted_at IS NOT NULL "
        "ORDER BY deleted_at DESC"
    )
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        rows = conn.execute(sql, (limit, offset)).fetchall()
    else:
        rows = conn.execute(sql).fetchall()
    return [dict(r) for r in rows]


def count_deleted_photos(conn: sqlite3.Connection) -> int:
    """Count soft-deleted photos. Pairs with get_deleted_photos(limit=...) for pagination."""
    row = conn.execute("SELECT COUNT(*) FROM photos WHERE deleted_at IS NOT NULL").fetchone()
    return int(row[0]) if row else 0


def hide_photos(conn: sqlite3.Connection, photo_ids: list[int]) -> int:
    """Hide photos by setting hidden_at. Returns count hidden."""
    if not photo_ids:
        return 0
    hidden = 0
    for ph, batch in in_batches(photo_ids):
        hidden += conn.execute(
            f"UPDATE photos SET hidden_at=datetime('now') WHERE id IN ({ph}) AND hidden_at IS NULL",
            batch,
        ).rowcount
    conn.commit()
    log.info("Hidden %d photos (requested %d)", hidden, len(photo_ids))
    return hidden


def unhide_photos(conn: sqlite3.Connection, photo_ids: list[int]) -> int:
    """Unhide photos by clearing hidden_at. Returns count unhidden."""
    if not photo_ids:
        return 0
    unhidden = 0
    for ph, batch in in_batches(photo_ids):
        unhidden += conn.execute(
            f"UPDATE photos SET hidden_at=NULL WHERE id IN ({ph}) AND hidden_at IS NOT NULL",
            batch,
        ).rowcount
    conn.commit()
    log.info("Unhidden %d photos (requested %d)", unhidden, len(photo_ids))
    return unhidden


def get_hidden_photos(
    conn: sqlite3.Connection,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Get hidden (not deleted) photos, most-recently-hidden first.

    Same limit/offset contract as ``get_deleted_photos``.
    """
    from bpp.db.photos import PHOTO_COLS_SLIM

    sql = (
        f"SELECT {PHOTO_COLS_SLIM} FROM photos WHERE hidden_at IS NOT NULL "
        "AND deleted_at IS NULL ORDER BY hidden_at DESC"
    )
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        rows = conn.execute(sql, (limit, offset)).fetchall()
    else:
        rows = conn.execute(sql).fetchall()
    return [dict(r) for r in rows]


def count_hidden_photos(conn: sqlite3.Connection) -> int:
    """Count hidden (not deleted) photos."""
    row = conn.execute(
        "SELECT COUNT(*) FROM photos WHERE hidden_at IS NOT NULL AND deleted_at IS NULL"
    ).fetchone()
    return int(row[0]) if row else 0


def permanent_delete_photos(conn: sqlite3.Connection, photo_ids: list[int]) -> list[str]:
    """Permanently delete photos from DB. Returns list of filepaths for disk cleanup."""
    if not photo_ids:
        return []
    # Only rows already in the recycle bin, and only the rows this very
    # statement removed (a restore from another request between a
    # SELECT and the DELETE would otherwise be reported as deleted).
    # ON DELETE CASCADE handles album_photos, face_embeddings, clip_embeddings, etc.
    rows: list[Any] = []
    for ph, batch in in_batches(photo_ids):
        rows.extend(
            conn.execute(
                f"DELETE FROM photos WHERE id IN ({ph}) AND deleted_at IS NOT NULL "
                "RETURNING id, filepath",
                batch,
            ).fetchall()
        )
    conn.commit()
    affected_ids = [int(r[0]) for r in rows]
    filepaths = [r[1] for r in rows]
    if not filepaths:
        return []
    log.info("Permanently deleted %d photos (requested %d)", len(filepaths), len(photo_ids))
    # Post-commit hook for plugins. Rows are already gone (ON DELETE
    # CASCADE flushed albums, embeddings, etc.) — plugins that need
    # the old row contents should subscribe to "soft" instead.
    from bpp.db.photo_hooks import dispatch_photo_deletion

    dispatch_photo_deletion(conn, affected_ids, "permanent")
    return filepaths


def purge_old_deleted(conn: sqlite3.Connection, days: int = 30) -> list[str]:
    """Permanently delete photos that have been soft-deleted for more than `days` days.
    Returns list of filepaths for disk cleanup."""
    rows = conn.execute(
        "SELECT id, filepath FROM photos "
        "WHERE deleted_at IS NOT NULL AND deleted_at < datetime('now', ?)",
        (f"-{days} days",),
    ).fetchall()
    if not rows:
        return []
    photo_ids = [r[0] for r in rows]
    log.info("Purging %d photos soft-deleted more than %d days ago", len(photo_ids), days)
    return permanent_delete_photos(conn, photo_ids)

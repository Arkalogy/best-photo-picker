"""Migration utilities for importing data from legacy formats."""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Any

from bpp.db.dialect import dialect
from bpp.db.photos import bulk_upsert_photos
from bpp.db.presets import save_preset
from bpp.db.schema import SCHEMA_VERSION
from bpp.utils.logging import get_logger

log = get_logger(__name__)


def get_version(conn: sqlite3.Connection) -> int:
    """Read the current schema version (0 on a fresh DB)."""
    return dialect.get_user_version(conn)


def migrate(conn: sqlite3.Connection) -> None:
    """Bump schema version on a fresh DB (legacy stub — not the active migration path).

    This function is NOT where per-version migration steps live.  All real
    schema migrations (adding columns, indexes, back-filling data) belong in
    ``bpp/db/migrations.py`` and are orchestrated by ``bpp/db/schema.py:_migrate()``,
    which is called automatically at startup via ``create_tables()``.

    This stub was the original migration entry-point before the per-step
    SAVEPOINT machinery was added.  It still exists because a small number of
    tests and the CLI restore command import it by name.  Do NOT add new
    migration steps here — they will be silently skipped at runtime.
    """
    version = get_version(conn)
    if version >= SCHEMA_VERSION:
        return
    dialect.set_user_version(conn, SCHEMA_VERSION)
    conn.commit()


def import_from_analysis_json(conn: sqlite3.Connection, json_path: str) -> int:
    """Import photos from a legacy analysis.json file. Returns count imported."""
    if not os.path.exists(json_path):
        log.warning("analysis.json not found at %s", json_path)
        return 0

    try:
        with open(json_path) as f:
            data: list[dict[str, Any]] = json.load(f)
    except (json.JSONDecodeError, ValueError):
        log.warning("Corrupt JSON in %s, skipping import", json_path)
        return 0

    count = bulk_upsert_photos(conn, data)
    log.info("Imported %d photos from %s", count, json_path)
    return count


def import_presets_from_json(conn: sqlite3.Connection, json_path: str) -> int:
    """Import presets from legacy ~/.config/bpp/presets.json. Returns count."""
    if not os.path.exists(json_path):
        return 0

    try:
        with open(json_path) as f:
            presets: dict[str, Any] = json.load(f)
    except (json.JSONDecodeError, ValueError):
        log.warning("Corrupt JSON in %s, skipping import", json_path)
        return 0

    count = 0
    for name, settings in presets.items():
        if isinstance(settings, dict):
            save_preset(conn, name, settings)
            count += 1
    log.info("Imported %d presets from %s", count, json_path)
    return count


def import_face_embeddings(conn: sqlite3.Connection, old_db_path: str) -> int:
    """Migrate face embeddings from legacy analysis_cache.db to the new schema.

    The old schema stores embeddings keyed by (filepath, file_size, file_mtime, face_index).
    The new schema uses photo_id FK. This function resolves filepaths to photo IDs.
    """
    if not os.path.exists(old_db_path):
        return 0

    from bpp.db.connection import get_db

    old_conn = get_db(old_db_path)
    try:
        rows = old_conn.execute(
            "SELECT filepath, face_index, bbox_x, bbox_y, bbox_w, bbox_h, "
            "embedding, cluster_id FROM face_embeddings"
        ).fetchall()
    except sqlite3.OperationalError:
        return 0

    # Pre-load filepath→id map to avoid N per-row SELECT queries
    photo_map = {r[0]: r[1] for r in conn.execute("SELECT filepath, id FROM photos").fetchall()}
    to_insert = [
        (photo_map[fp], fi, bx, by, bw, bh, emb, cid)
        for fp, fi, bx, by, bw, bh, emb, cid in rows
        if fp in photo_map
    ]
    conn.executemany(
        "INSERT OR IGNORE INTO face_embeddings "
        "(photo_id, face_index, bbox_x, bbox_y, bbox_w, bbox_h, embedding, cluster_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        to_insert,
    )
    count = len(to_insert)
    conn.commit()
    log.info("Imported %d face embeddings from %s", count, old_db_path)
    return count

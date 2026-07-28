"""Per-version schema migration steps.

Each version's migration is its own function so the per-step logic
is independently readable and testable. The orchestrator (in
schema.py) walks ``MIGRATIONS`` in order, wraps each step in a
SAVEPOINT, and bumps ``user_version`` after a successful step.

Adding a migration:

  1. Define ``def _migrate_vN(conn): ...`` here, mirroring the shape
     of the existing ones (``ALTER TABLE`` guarded by
     ``dialect.column_names``, ``CREATE TABLE IF NOT EXISTS`` for
     new tables, idempotent everywhere — a step may re-run after a
     partial failure).
  2. Bump ``SCHEMA_VERSION`` in schema.py to N.
  3. Add ``(N, _migrate_vN)`` at the end of ``MIGRATIONS``.

Invariants:
  - Use ``conn.execute()``, never ``executescript()`` — the latter
    issues an implicit COMMIT that drops the surrounding SAVEPOINT.
  - Idempotent. ``ALTER TABLE`` requires a column-name guard (the
    SAVEPOINT rolls back column adds, but a re-run on a partially
    completed schema still has to be safe).
  - Keep migrations small. If you need ``> ~30 LOC`` factor a helper
    out (see ``_backfill_exif_json`` in schema.py for the pattern).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from bpp.db.dialect import dialect
from bpp.utils.logging import get_logger

log = get_logger(__name__)


MigrationFn = Callable[[sqlite3.Connection], None]


# ── Per-version steps ────────────────────────────────────────────────


def _migrate_v3(conn: sqlite3.Connection) -> None:
    cols = dialect.column_names(conn, "photos")
    if "deleted_at" not in cols:
        conn.execute("ALTER TABLE photos ADD COLUMN deleted_at TEXT")


def _migrate_v4(conn: sqlite3.Connection) -> None:
    cols = dialect.column_names(conn, "photos")
    for col, default in [("pet_count", "0"), ("has_cat", "0"), ("has_dog", "0")]:
        if col not in cols:
            conn.execute(f"ALTER TABLE photos ADD COLUMN {col} INTEGER DEFAULT {default}")


def _migrate_v5(conn: sqlite3.Connection) -> None:
    cols = dialect.column_names(conn, "pet_detections")
    if "cluster_id" not in cols and cols:
        from bpp.constants import CLUSTER_UNASSIGNED

        conn.execute(
            f"ALTER TABLE pet_detections ADD COLUMN cluster_id INTEGER DEFAULT {CLUSTER_UNASSIGNED}"
        )


def _migrate_v6(conn: sqlite3.Connection) -> None:
    cols = dialect.column_names(conn, "photos")
    if "exif_json" not in cols:
        conn.execute("ALTER TABLE photos ADD COLUMN exif_json TEXT")


def _migrate_v8(conn: sqlite3.Connection) -> None:
    cols = dialect.column_names(conn, "photos")
    if "is_video" not in cols:
        conn.execute("ALTER TABLE photos ADD COLUMN is_video BOOLEAN DEFAULT 0")


def _migrate_v9(conn: sqlite3.Connection) -> None:
    cols = dialect.column_names(conn, "photos")
    if "is_raw" not in cols:
        conn.execute("ALTER TABLE photos ADD COLUMN is_raw BOOLEAN DEFAULT 0")


def _migrate_v10(conn: sqlite3.Connection) -> None:
    cols = dialect.column_names(conn, "albums")
    if "parent_id" not in cols:
        conn.execute(
            "ALTER TABLE albums ADD COLUMN parent_id INTEGER"
            " REFERENCES albums(id) ON DELETE SET NULL"
        )


def _migrate_v11(conn: sqlite3.Connection) -> None:
    cols = dialect.column_names(conn, "photos")
    if "hidden_at" not in cols:
        conn.execute("ALTER TABLE photos ADD COLUMN hidden_at TEXT")


# v12: photo_edits table — handled by CREATE TABLE IF NOT EXISTS in TABLES_SQL


def _migrate_v13(conn: sqlite3.Connection) -> None:
    cols = dialect.column_names(conn, "photo_edits")
    if cols:
        for col, col_type, default in [
            ("crop_x", "REAL", None),
            ("crop_y", "REAL", None),
            ("crop_w", "REAL", None),
            ("crop_h", "REAL", None),
            ("rotation", "INTEGER", "0"),
            ("flip_h", "BOOLEAN", "0"),
            ("flip_v", "BOOLEAN", "0"),
        ]:
            if col not in cols:
                default_clause = f" DEFAULT {default}" if default is not None else ""
                conn.execute(f"ALTER TABLE photo_edits ADD COLUMN {col} {col_type}{default_clause}")


def _migrate_v14(conn: sqlite3.Connection) -> None:
    cols = dialect.column_names(conn, "photo_edits")
    if cols:
        for col, col_type, default in [
            ("warmth", "REAL", "0.0"),
            ("highlights", "REAL", "0.0"),
            ("shadows", "REAL", "0.0"),
            ("vignette", "REAL", "0.0"),
            ("grain", "REAL", "0.0"),
            ("fade", "REAL", "0.0"),
            ("redeye_json", "TEXT", None),
            ("filter_name", "TEXT", None),
        ]:
            if col not in cols:
                default_clause = f" DEFAULT {default}" if default is not None else ""
                conn.execute(f"ALTER TABLE photo_edits ADD COLUMN {col} {col_type}{default_clause}")


def _migrate_v15(conn: sqlite3.Connection) -> None:
    cols = dialect.column_names(conn, "photos")
    if "video_duration" not in cols:
        conn.execute("ALTER TABLE photos ADD COLUMN video_duration REAL")


def _migrate_v16(conn: sqlite3.Connection) -> None:
    cols = dialect.column_names(conn, "photos")
    for col, col_type in [
        ("video_width", "INTEGER"),
        ("video_height", "INTEGER"),
        ("video_fps", "REAL"),
        ("video_codec", "TEXT"),
    ]:
        if col not in cols:
            conn.execute(f"ALTER TABLE photos ADD COLUMN {col} {col_type}")


def _migrate_v18(conn: sqlite3.Connection) -> None:
    # Defer the import to runtime to avoid a circular at module load
    # (schema.py imports this module; backfill helper lives in schema).
    from bpp.db.schema import _backfill_exif_json

    _backfill_exif_json(conn)


def _migrate_v21(conn: sqlite3.Connection) -> None:
    cols = dialect.column_names(conn, "photo_edits")
    if cols:
        for col, col_type, default in [
            ("exposure", "REAL", "0.0"),
            ("brilliance", "REAL", "0.0"),
            ("black_point", "REAL", "0.0"),
            ("vibrance", "REAL", "0.0"),
            ("tint", "REAL", "0.0"),
            ("definition", "REAL", "0.0"),
            ("noise_reduction", "REAL", "0.0"),
            ("straighten", "REAL", "0.0"),
            ("perspective_v", "REAL", "0.0"),
            ("perspective_h", "REAL", "0.0"),
        ]:
            if col not in cols:
                conn.execute(
                    f"ALTER TABLE photo_edits ADD COLUMN {col} {col_type} DEFAULT {default}"
                )


def _migrate_v22(conn: sqlite3.Connection) -> None:
    cursor = conn.execute(
        "DELETE FROM face_embeddings "
        "WHERE cluster_id >= 0 "
        "  AND id NOT IN ("
        "    SELECT MIN(id) FROM face_embeddings "
        "    WHERE cluster_id >= 0 "
        "    GROUP BY photo_id, cluster_id"
        "  )"
    )
    if cursor.rowcount:
        log.info("Migration v22: removed %d duplicate face embeddings", cursor.rowcount)


# v23-v35 live in migrations_recent.py since the v0.1 cleanup.
from bpp.db.migrations_recent import (  # noqa: E402
    _migrate_v23,
    _migrate_v25,
    _migrate_v26,
    _migrate_v27,
    _migrate_v28,
    _migrate_v29,
    _migrate_v30,
    _migrate_v31,
    _migrate_v32,
    _migrate_v33,
    _migrate_v34,
    _migrate_v35,
    _migrate_v36,
    _migrate_v37,
    _migrate_v38,
    _migrate_v39,
    _migrate_v40,
    _migrate_v41,
    _migrate_v42,
    _migrate_v43,
    _migrate_v44,
)

# Gaps in the version sequence (7, 12, 17, 19, 20, 24) are intentional —
# either rolled into the surrounding step or never had a schema change
# associated with them. Keep the gaps so the version numbers in
# user_version columns line up with historical DBs.
MIGRATIONS: tuple[tuple[int, MigrationFn], ...] = (
    (3, _migrate_v3),
    (4, _migrate_v4),
    (5, _migrate_v5),
    (6, _migrate_v6),
    (8, _migrate_v8),
    (9, _migrate_v9),
    (10, _migrate_v10),
    (11, _migrate_v11),
    (13, _migrate_v13),
    (14, _migrate_v14),
    (15, _migrate_v15),
    (16, _migrate_v16),
    (18, _migrate_v18),
    (21, _migrate_v21),
    (22, _migrate_v22),
    (23, _migrate_v23),
    (25, _migrate_v25),
    (26, _migrate_v26),
    (27, _migrate_v27),
    (28, _migrate_v28),
    (29, _migrate_v29),
    (30, _migrate_v30),
    (31, _migrate_v31),
    (32, _migrate_v32),
    (33, _migrate_v33),
    (34, _migrate_v34),
    (35, _migrate_v35),
    (36, _migrate_v36),
    (37, _migrate_v37),
    (38, _migrate_v38),
    (39, _migrate_v39),
    (40, _migrate_v40),
    (41, _migrate_v41),
    (42, _migrate_v42),
    (43, _migrate_v43),
    (44, _migrate_v44),
)

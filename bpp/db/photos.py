"""CRUD operations for the photos table."""

from __future__ import annotations

import os
import sqlite3
from typing import Any

from bpp.constants import ACTIVE_PHOTO_SQL, SQL_BATCH_SIZE, visible_photo_conditions
from bpp.scoring.registry import get_score_db_columns
from bpp.utils.logging import get_logger
from bpp.utils.retry import retry_io

log = get_logger(__name__)

# Columns that map directly from analysis dicts to the photos table.
_SCORE_COLUMNS = get_score_db_columns()

_ALL_COLUMNS = (
    "filepath",
    "original_filename",
    "import_batch",
    "sha256",
    "file_size",
    "file_mtime",
    "missing",
    "date",
    "date_day",
    "date_month",
    *_SCORE_COLUMNS,
    "phash",
    "ahash",
    "cluster_size",
    "exif_json",
    # stable GPS columns. Surfaced in the standard photo
    # column list so build_photo_dict + every SELECT picks them up
    # without per-call json_extract.
    "gps_lat",
    "gps_lon",
    "is_video",
    "video_duration",
    "video_width",
    "video_height",
    "video_fps",
    "video_codec",
    "is_raw",
    # Live Photo sidecar columns (schema v33)
    "is_live_photo_sidecar",
    "live_photo_parent_id",
    # Near-duplicate cluster id (schema v34)
    "dup_cluster_id",
    # Moment cluster (schema v42) — visually-similar shots near in time.
    # In the standard column list so build_photo_dict + every SELECT surface
    # them for the gallery's in-place Moment grouping.
    "moment_cluster_id",
    "moment_size",
)

# Complete column list for the photos table (including auto-managed columns).
# sensitive_override (v43) deliberately lives HERE and not in _ALL_COLUMNS:
# it is user data written only by the override endpoint. _ALL_COLUMNS feeds
# bulk_upsert_photos, which coerces missing keys to defaults — putting the
# override there would stomp user corrections on every re-analysis (the
# same write-path stomp that NULLs parent phashes by design).
_PHOTO_COL_NAMES = (
    "id",
    *_ALL_COLUMNS,
    "sensitive_override",
    "created_at",
    "deleted_at",
    "hidden_at",
)

_PHOTO_COL_NAMES_SLIM = tuple(c for c in _PHOTO_COL_NAMES if c != "exif_json")

# SQL fragments for SELECT queries — bare (no table alias) and p-prefixed.
PHOTO_COLS = ", ".join(_PHOTO_COL_NAMES)
PHOTO_COLS_SLIM = ", ".join(_PHOTO_COL_NAMES_SLIM)
PHOTO_COLS_PREFIXED = ", ".join(f"p.{c}" for c in _PHOTO_COL_NAMES)
PHOTO_COLS_SLIM_PREFIXED = ", ".join(f"p.{c}" for c in _PHOTO_COL_NAMES_SLIM)


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


# GPS validation helpers live in photos_gps.py. Imported here at module
# scope (not lazily) because upsert_photo / bulk_upsert_photos call
# ``_maybe_lift_gps_from_exif`` on every row — the per-call import
# overhead would be a real cost on bulk imports.
from bpp.db.photos_gps import _maybe_lift_gps_from_exif, _valid_gps_pair  # noqa: E402, F401


def upsert_photo(conn: sqlite3.Connection, photo: dict[str, Any]) -> int:
    """Insert or update a photo record keyed on filepath. Returns the photo id."""
    filepath = photo["filepath"]
    try:
        stat = os.stat(filepath)
        file_size = stat.st_size
        file_mtime = stat.st_mtime
        missing = photo.get("missing", 0)
    except OSError:
        file_size = photo.get("file_size", 0)
        file_mtime = photo.get("file_mtime", 0.0)
        missing = photo.get("missing", 1)
    values = {
        "filepath": filepath,
        "original_filename": photo.get("original_filename", os.path.basename(filepath)),
        "import_batch": photo.get("import_batch"),
        "sha256": photo.get("sha256"),
        "file_size": photo.get("file_size", file_size),
        "file_mtime": photo.get("file_mtime", file_mtime),
        "missing": missing,
        "date": photo.get("date"),
        "date_day": photo.get("date_day"),
        "date_month": photo.get("date_month"),
    }
    for col in _SCORE_COLUMNS:
        values[col] = photo.get(col)
    for col in (
        "phash",
        "ahash",
        "cluster_size",
        "exif_json",
        "gps_lat",
        "gps_lon",
        "is_video",
        "is_raw",
        "video_duration",
        "video_width",
        "video_height",
        "video_fps",
        "video_codec",
    ):
        if col in photo:
            values[col] = photo[col]

    # Live Photo sidecar columns — default to 0 / NULL so callers that
    # predate v33 (analysis.json importers, test fixtures, etc.) don't
    # need to know about these fields.
    values["is_live_photo_sidecar"] = int(photo.get("is_live_photo_sidecar") or 0)
    if "live_photo_parent_id" in photo:
        values["live_photo_parent_id"] = photo["live_photo_parent_id"]
    # Near-duplicate cluster id (v34) — 0 means "not yet clustered".
    # assign_near_duplicate_clusters() updates this after import/analyze.
    values["dup_cluster_id"] = int(photo.get("dup_cluster_id") or 0)

    # if the caller passed `exif_json` but didn't pre-populate
    # the new gps_lat / gps_lon columns, parse the JSON and lift them
    # over. Lets older callers (and existing tests) that only know
    # about the JSON blob keep working without forcing every writer
    # to know about both fields.
    _maybe_lift_gps_from_exif(values)

    cols = list(values.keys())
    placeholders = ", ".join(["?"] * len(cols))
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "filepath")

    conn.execute(
        f"INSERT INTO photos ({', '.join(cols)}) VALUES ({placeholders})"
        f" ON CONFLICT(filepath) DO UPDATE SET {updates}",
        [values[c] for c in cols],
    )
    conn.commit()

    row = conn.execute("SELECT id FROM photos WHERE filepath=?", (filepath,)).fetchone()
    if row is None:
        raise RuntimeError(f"Photo upsert failed - row not found for {filepath!r}")
    return row[0]


def get_photo(conn: sqlite3.Connection, photo_id: int) -> dict[str, Any] | None:
    """Return one photo by id, or None if it doesn't exist.

    Returns ALL photos including soft-deleted/hidden — callers that
    only want active photos must filter on `deleted_at` / `hidden_at`
    themselves (see `bpp.constants.ACTIVE_PHOTO_SQL`).
    """
    row = conn.execute(f"SELECT {PHOTO_COLS} FROM photos WHERE id=?", (photo_id,)).fetchone()
    return _row_to_dict(row)


def get_photo_by_path(conn: sqlite3.Connection, filepath: str) -> dict[str, Any] | None:
    """Return one photo by absolute filepath, or None if not in DB.

    Same active-photo caveat as `get_photo`. Filepath is the unique
    identifier on the photos table.
    """
    row = conn.execute(f"SELECT {PHOTO_COLS} FROM photos WHERE filepath=?", (filepath,)).fetchone()
    return _row_to_dict(row)


def set_sensitive_override(conn: sqlite3.Connection, photo_id: int, value: int | None) -> None:
    """Set the user's sensitive-photo override (v43).

    ``value``: 1 = user says sensitive, 0 = user says not sensitive,
    None = clear the override (follow the model again). Written ONLY
    here — bulk_upsert_photos never touches this column, so re-analysis
    can't stomp user corrections.
    """
    if value is not None:
        value = 1 if value else 0
    conn.execute(
        "UPDATE photos SET sensitive_override=? WHERE id=?",
        (value, photo_id),
    )
    conn.commit()


def get_photo_id_by_path(conn: sqlite3.Connection, filepath: str) -> int | None:
    """Return the photo ID for a single filepath, or None if not found."""
    row = conn.execute("SELECT id FROM photos WHERE filepath=?", (filepath,)).fetchone()
    return row[0] if row else None


def get_photo_id_map_by_paths(conn: sqlite3.Connection, filepaths: list[str]) -> dict[str, int]:
    """Resolve filepaths to a {filepath: photo_id} map in batched queries."""
    if not filepaths:
        return {}
    result: dict[str, int] = {}
    batch_size = SQL_BATCH_SIZE
    for i in range(0, len(filepaths), batch_size):
        batch = filepaths[i : i + batch_size]
        placeholders = ",".join("?" * len(batch))
        rows = conn.execute(
            f"SELECT filepath, id FROM photos WHERE filepath IN ({placeholders})",
            batch,
        ).fetchall()
        for r in rows:
            result[r[0]] = r[1]
    return result


def get_photo_ids_by_paths(conn: sqlite3.Connection, filepaths: list[str]) -> list[int]:
    """Resolve a list of filepaths to photo IDs in a single query.

    Returns IDs for all paths that exist in the DB (order not guaranteed).
    Uses batched WHERE IN queries to avoid SQLite variable limits.
    """
    if not filepaths:
        return []
    ids: list[int] = []
    batch_size = SQL_BATCH_SIZE
    for i in range(0, len(filepaths), batch_size):
        batch = filepaths[i : i + batch_size]
        placeholders = ",".join("?" * len(batch))
        rows = conn.execute(
            f"SELECT id FROM photos WHERE filepath IN ({placeholders})",
            batch,
        ).fetchall()
        ids.extend(r[0] for r in rows)
    return ids


def get_all_photos(
    conn: sqlite3.Connection,
    include_missing: bool = False,
    include_deleted: bool = False,
    include_hidden: bool = False,
) -> list[dict[str, Any]]:
    """Get all photos, optionally including missing, soft-deleted, and/or hidden ones.

    Live Photo sidecars are ALWAYS excluded — no opt-in. They are stored
    but invisible in every user-facing view (project invariant), and
    this getter feeds the Library endpoint + the in-memory analysis list.
    Internal code that needs raw sidecar rows queries them explicitly
    (bpp/db/live_photo.py).
    """
    conditions = visible_photo_conditions(
        include_missing=include_missing,
        include_deleted=include_deleted,
        include_hidden=include_hidden,
    )
    where = f" WHERE {' AND '.join(conditions)}"
    rows = conn.execute(f"SELECT {PHOTO_COLS_SLIM} FROM photos{where} ORDER BY date").fetchall()
    return [dict(r) for r in rows]


def get_photos_page(
    conn: sqlite3.Connection,
    limit: int,
    offset: int,
    include_missing: bool = False,
    include_deleted: bool = False,
    include_hidden: bool = False,
) -> list[dict[str, Any]]:
    """Fetch a single page of photos using SQL LIMIT/OFFSET.

    Same filter semantics as get_all_photos(); same ORDER BY date sort.
    Use this instead of slicing the in-memory analysis list to avoid
    loading the entire library into Python on each page request.
    """
    # Serves GET /api/v1/photos (the Library grid). Conditions come from
    # the shared builder — hand-rolling them here is how the sidecar
    # filter went missing and 3k placeholder rows flooded the Library
    # (2026-06-12).
    conditions = visible_photo_conditions(
        include_missing=include_missing,
        include_deleted=include_deleted,
        include_hidden=include_hidden,
    )
    where = f" WHERE {' AND '.join(conditions)}"
    rows = conn.execute(
        f"SELECT {PHOTO_COLS_SLIM} FROM photos{where} ORDER BY date LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    return [dict(r) for r in rows]


def update_scores(conn: sqlite3.Connection, filepath: str, scores: dict[str, Any]) -> None:
    """Update score columns for a photo."""
    allowed = {*_SCORE_COLUMNS, "cluster_size"}
    updates = {k: v for k, v in scores.items() if k in allowed}
    if not updates:
        return
    set_clause = ", ".join(f"{k}=?" for k in updates)
    conn.execute(
        f"UPDATE photos SET {set_clause} WHERE filepath=?",
        [*updates.values(), filepath],
    )
    conn.commit()


def update_hashes(
    conn: sqlite3.Connection, filepath: str, phash: int | None, ahash: int | None
) -> None:
    """Persist perceptual hashes (phash, ahash) for a photo by filepath.

    Used by the background dedup pass to fill in hashes lazily after
    initial analyze. Pass None for either if computation failed.
    """
    conn.execute(
        "UPDATE photos SET phash=?, ahash=? WHERE filepath=?",
        (phash, ahash, filepath),
    )
    conn.commit()


def bulk_upsert_photos(conn: sqlite3.Connection, photos: list[dict[str, Any]]) -> int:
    """Batch upsert photos in a single transaction. Returns count inserted/updated."""
    if not photos:
        return 0

    # Use a fixed column set so all rows share one SQL statement for executemany.
    cols = list(_ALL_COLUMNS)
    placeholders = ", ".join(["?"] * len(cols))
    # Derived clustering columns are PRESERVED on update: analyze/import
    # callers never carry them, and `col=excluded.col` with the 0/1
    # defaults silently wiped every dup/Moment assignment on each
    # re-analyze (2026-06-12: Moments collapsed 765→17, Duplicates to 5
    # groups, until recompute). They're written by their owners —
    # assign_near_duplicate_clusters / assign_moment_clusters — via
    # direct UPDATEs. NOTE: phash/ahash DO stay in the update set and ARE
    # wiped deliberately (re-hash + the live-photo no-ghost-row chain
    # depends on it; see analyze-worker seam docs).
    _PRESERVE_ON_UPDATE = {"dup_cluster_id", "moment_cluster_id", "moment_size", "cluster_size"}
    updates = ", ".join(
        f"{c}=excluded.{c}" for c in cols if c != "filepath" and c not in _PRESERVE_ON_UPDATE
    )
    sql = (
        f"INSERT INTO photos ({', '.join(cols)}) VALUES ({placeholders})"
        f" ON CONFLICT(filepath) DO UPDATE SET {updates}"
    )

    rows: list[tuple] = []
    for photo in photos:
        filepath = photo["filepath"]
        try:
            stat = os.stat(filepath)
            file_size = stat.st_size
            file_mtime = stat.st_mtime
            missing = 0
        except OSError:
            file_size = photo.get("file_size", 0)
            file_mtime = photo.get("file_mtime", 0.0)
            missing = 1 if "file_size" not in photo else 0
        values = {
            "filepath": filepath,
            "original_filename": photo.get("original_filename", os.path.basename(filepath)),
            "import_batch": photo.get("import_batch"),
            "sha256": photo.get("sha256"),
            "file_size": photo.get("file_size", file_size),
            "file_mtime": photo.get("file_mtime", file_mtime),
            "missing": missing,
            "date": photo.get("date"),
            "date_day": photo.get("date_day"),
            "date_month": photo.get("date_month"),
        }
        for col in _SCORE_COLUMNS:
            values[col] = photo.get(col)
        for col in (
            "phash",
            "ahash",
            "cluster_size",
            "exif_json",
            "gps_lat",
            "gps_lon",
            "is_video",
            "video_duration",
            "video_width",
            "video_height",
            "video_fps",
            "video_codec",
            "is_raw",
        ):
            values[col] = photo.get(col)
        # same lift-from-exif fallback as upsert_photo
        _maybe_lift_gps_from_exif(values)
        # Live Photo sidecar — default 0 for callers that predate v33
        values["is_live_photo_sidecar"] = int(photo.get("is_live_photo_sidecar") or 0)
        if "live_photo_parent_id" in photo:
            values["live_photo_parent_id"] = photo["live_photo_parent_id"]
        values["dup_cluster_id"] = int(photo.get("dup_cluster_id") or 0)
        # Moment columns (v42) are NOT NULL and bulk_upsert binds the full
        # _ALL_COLUMNS list, so default them for importers that don't know
        # about Moments (assign_moment_clusters fills real values later).
        values["moment_cluster_id"] = int(photo.get("moment_cluster_id") or 0)
        values["moment_size"] = int(photo.get("moment_size") or 1)
        rows.append(tuple(values.get(c) for c in cols))

    # NAS-jitter resilience: wrap the write step in retry_io so a
    # transient I/O error (EIO / ETIMEDOUT / ESTALE — typical SMB/NFS
    # blips) gets one quick exponential-backoff retry instead of
    # bubbling out and leaving the import/analyze worker with a
    # half-done batch + the user wondering why their photo count
    # dropped overnight. Local-disk callers see zero overhead because
    # is_transient() returns False for everything they'd hit. Atomicity
    # is preserved: executemany is a single SQLite transaction and the
    # commit is the boundary, so a retried run starts cleanly.
    def _do_write() -> None:
        conn.executemany(sql, rows)
        conn.commit()

    retry_io(_do_write, label="bulk_upsert_photos")
    log.info("Bulk upserted %d photos", len(rows))
    return len(rows)


def sample_random_photos(conn: sqlite3.Connection, count: int = 100) -> list[str]:
    """Return up to `count` random non-missing photo filepaths for spot-checking."""
    rows = conn.execute(
        "SELECT filepath FROM photos WHERE missing=0 ORDER BY RANDOM() LIMIT ?",
        (count,),
    ).fetchall()
    return [r[0] for r in rows]


def get_photo_count(conn: sqlite3.Connection, include_missing: bool = False) -> int:
    """Total photos in the library.

    Excludes soft-deleted/hidden photos always; pass
    `include_missing=True` to also count photos whose files have gone
    missing on disk.
    """
    if include_missing:
        # Same as ACTIVE_PHOTO_SQL minus the missing=0 clause — still
        # excludes Live Photo motion sidecars and soft-deleted/hidden
        # rows. Spelled out (rather than reusing ACTIVE_PHOTO_SQL) so
        # the include_missing branch is grep-able.
        row = conn.execute(
            "SELECT COUNT(*) FROM photos "
            "WHERE deleted_at IS NULL AND hidden_at IS NULL "
            "AND is_live_photo_sidecar = 0"
        ).fetchone()
    else:
        row = conn.execute(f"SELECT COUNT(*) FROM photos WHERE {ACTIVE_PHOTO_SQL}").fetchone()
    return row[0]


# Date-keyed queries + update_photo_date moved to photos_dates.py
# during the 500-LOC split. Re-exported here.
from bpp.db.photos_dates import (  # noqa: E402, F401
    get_date_distribution,
    get_photos_by_date_range,
    update_photo_date,
)

# GPS query helpers also live in photos_gps.py — re-exported.
from bpp.db.photos_gps import count_photos_with_gps, get_photos_with_gps  # noqa: E402, F401

# Photo lifecycle ops (soft delete / restore / hide / permanent delete /
# purge_old_deleted) live in photos_lifecycle.py since the v0.1 cleanup.
# Re-exported here so existing callers keep working unchanged.
from bpp.db.photos_lifecycle import (  # noqa: E402, F401
    count_deleted_photos,
    count_hidden_photos,
    get_deleted_photos,
    get_hidden_photos,
    hide_photos,
    permanent_delete_photos,
    purge_old_deleted,
    restore_photos,
    soft_delete_photos,
    unhide_photos,
)

# Missing-on-disk detection + SHA-256 relocation moved to
# photos_missing.py during the 500-LOC split. Re-exported.
from bpp.db.photos_missing import (  # noqa: E402, F401
    check_missing,
    mark_missing,
    relocate_missing,
)

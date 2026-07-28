"""Schema migration steps v23 through v35.

Extracted from ``bpp.db.migrations`` during the v0.1 cleanup so the
host module stays under the 500-LOC soft cap. The early steps (v3-v22)
stay in ``bpp.db.migrations``; the orchestrator there imports this
module and concatenates the two function tables.

See ``bpp.db.migrations`` for the contributor guide.
"""

from __future__ import annotations

import sqlite3

from bpp.db.dialect import dialect
from bpp.utils.logging import get_logger

log = get_logger(__name__)


def _migrate_v23(conn: sqlite3.Connection) -> None:
    cols = dialect.column_names(conn, "face_embeddings")
    if "quality" not in cols:
        conn.execute("ALTER TABLE face_embeddings ADD COLUMN quality REAL")
        log.info("Migration v23: added quality column to face_embeddings")


def _migrate_v25(conn: sqlite3.Connection) -> None:
    cols = dialect.column_names(conn, "photo_edits")
    if "auto_enhanced" not in cols:
        conn.execute("ALTER TABLE photo_edits ADD COLUMN auto_enhanced BOOLEAN DEFAULT 0")
        log.info("Migration v25: added auto_enhanced column to photo_edits")


def _migrate_v26(conn: sqlite3.Connection) -> None:
    cols = dialect.column_names(conn, "face_embeddings")
    if "identity" not in cols:
        conn.execute("ALTER TABLE face_embeddings ADD COLUMN identity TEXT")
        log.info("Migration v26: added identity column to face_embeddings")
    if "user_confirmed" not in cols:
        conn.execute("ALTER TABLE face_embeddings ADD COLUMN user_confirmed INTEGER DEFAULT 0")
        log.info("Migration v26: added user_confirmed column to face_embeddings")

    # Backfill identity from existing named smart_person albums
    try:
        rows = conn.execute(
            "SELECT name, rule_json FROM albums WHERE album_type='smart_person'"
        ).fetchall()
        backfilled = 0
        from bpp.utils.json_utils import safe_json_loads

        for name, rule_json in rows:
            if not rule_json:
                continue
            # Use safe_json_loads — a corrupt rule_json row must not
            # crash startup migrations.
            rule = safe_json_loads(rule_json, {}, context="album rule_json v26 migration")
            if not isinstance(rule, dict):
                continue
            cid = rule.get("cluster_id")
            if cid is None:
                continue
            # Skip default "Person N" names
            if name.startswith("Person ") and name[7:].isdigit():
                continue
            cur = conn.execute(
                "UPDATE face_embeddings SET identity = ? WHERE cluster_id = ? AND identity IS NULL",
                (name, cid),
            )
            backfilled += cur.rowcount
        if backfilled:
            log.info("Migration v26: backfilled %d identity labels", backfilled)
    except Exception:
        log.warning("Migration v26: identity backfill failed (non-fatal)", exc_info=True)


def _migrate_v27(conn: sqlite3.Connection) -> None:
    # LAN share access log — capped audit trail for share-token auths.
    # Use execute (not executescript) so the SAVEPOINT in `_step`
    # stays active; executescript issues an implicit COMMIT.
    from bpp.db.schema import _PK

    conn.execute(
        "CREATE TABLE IF NOT EXISTS share_access_log ("
        f"  id {_PK},"
        "  ts INTEGER NOT NULL,"
        "  ip TEXT NOT NULL,"
        "  user_agent TEXT NOT NULL DEFAULT ''"
        ")"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_share_access_log_ts ON share_access_log(ts)")


def _migrate_v28(conn: sqlite3.Connection) -> None:
    # share_devices: TOFU pairing for LAN sharing. user_id + scope_json
    # are forward-compat NULLs.
    from bpp.db.schema import _PK

    conn.execute(
        "CREATE TABLE IF NOT EXISTS share_devices ("
        f"  id {_PK},"
        "  fingerprint TEXT UNIQUE NOT NULL,"
        "  name TEXT NOT NULL DEFAULT '',"
        "  ip_at_pair TEXT NOT NULL DEFAULT '',"
        "  user_id INTEGER,"
        "  scope_json TEXT,"
        "  first_seen INTEGER NOT NULL,"
        "  last_seen INTEGER NOT NULL,"
        "  trusted_at INTEGER,"
        "  revoked_at INTEGER,"
        "  prev_revoked INTEGER NOT NULL DEFAULT 0"
        ")"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_share_devices_fingerprint ON share_devices(fingerprint)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_share_devices_state"
        " ON share_devices(trusted_at, revoked_at)"
    )


def _migrate_v29(conn: sqlite3.Connection) -> None:
    # Operation journal: durable breadcrumb for long-running mutations
    # so crash recovery on next startup is mechanical rather than
    # guesswork.
    from bpp.db.schema import _PK

    conn.execute(
        "CREATE TABLE IF NOT EXISTS operation_journal ("
        f"  id {_PK},"
        "  kind TEXT NOT NULL,"
        "  payload_json TEXT NOT NULL,"
        "  started_at INTEGER NOT NULL,"
        "  completed_at INTEGER"
        ")"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_operation_journal_pending"
        " ON operation_journal(kind, completed_at)"
    )


def _migrate_v30(conn: sqlite3.Connection) -> None:
    """lift gps_lat / gps_lon out of the exif_json blob into
    real columns + a partial index, then backfill from existing rows.

    Why: the map endpoint and album-stats query both did
    ``json_extract(exif_json, '$.gps_lat')`` per row, plus a
    ``WHERE … IS NOT NULL`` filter that couldn't use any index.
    On a 50k-photo library that's a full table scan + a JSON parse
    per row — visibly slow on the map view's first paint.

    Migration safety:
    - ALTER TABLE ADD COLUMN with no DEFAULT is O(1) on SQLite —
      doesn't rewrite the table.
    - The partial index only covers rows where both columns are
      non-null, so it stays compact for typical libraries
      (5-30% GPS coverage).
    - Backfill reads exif_json with safe_json_loads (project convention);
      malformed blobs are skipped, not raised.
    - Idempotent: ``ADD COLUMN`` checks PRAGMA first; backfill
      skips rows that already have a value.
    """
    from bpp.utils.json_utils import safe_json_loads

    cols = {r[1] for r in conn.execute("PRAGMA table_info(photos)").fetchall()}
    if "gps_lat" not in cols:
        conn.execute("ALTER TABLE photos ADD COLUMN gps_lat REAL")
    if "gps_lon" not in cols:
        conn.execute("ALTER TABLE photos ADD COLUMN gps_lon REAL")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_photos_gps ON photos(gps_lat, gps_lon) "
        "WHERE gps_lat IS NOT NULL AND gps_lon IS NOT NULL"
    )

    # Backfill — only touch rows that have exif_json AND don't already
    # have the new columns populated (handles re-runs cleanly).
    rows = conn.execute(
        "SELECT id, exif_json FROM photos "
        "WHERE exif_json IS NOT NULL "
        "AND (gps_lat IS NULL AND gps_lon IS NULL)"
    ).fetchall()
    # validate the coord pair before writing. SQLite REAL
    # accepts garbage (NaN, 999.0) verbatim; the indexed column would
    # otherwise carry corrupt EXIF onto the map view forever.
    from bpp.db.photos import _valid_gps_pair

    backfilled = 0
    for row in rows:
        data = safe_json_loads(row[1], default=None)
        if not isinstance(data, dict):
            continue
        lat = data.get("gps_lat")
        lon = data.get("gps_lon")
        if _valid_gps_pair(lat, lon):
            conn.execute(
                "UPDATE photos SET gps_lat = ?, gps_lon = ? WHERE id = ?",
                (lat, lon, row[0]),
            )
            backfilled += 1
    if backfilled:
        log.info(
            "Migration v30: backfilled gps_lat/gps_lon for %d photos from exif_json",
            backfilled,
        )


# ── Dispatch table ───────────────────────────────────────────────────


def _migrate_v31(conn: sqlite3.Connection) -> None:
    """Strip legacy settings keys from album config_json.

    Older versions stored global settings (lan_share_token, first_run,
    zoom_pct) in album 1's config_json. Those keys have since moved to
    the dedicated settings table, but historical DBs may still carry them.
    Stripping them prevents the lan_share_token from leaking via the album
    list API to LAN devices. Idempotent — albums without these keys are
    left unchanged.
    """
    import json

    from bpp.utils.json_utils import safe_json_loads

    _LEGACY_KEYS = {"lan_share_token", "first_run", "zoom_pct"}

    cols = {r[1] for r in conn.execute("PRAGMA table_info(albums)").fetchall()}
    if "config_json" not in cols:
        return  # column absent in very old schemas — nothing to strip

    rows = conn.execute(
        "SELECT id, config_json FROM albums WHERE config_json IS NOT NULL"
    ).fetchall()
    updates: list[tuple[str | None, int]] = []
    for album_id, config_json in rows:
        cfg = safe_json_loads(config_json, context="v31 migration")
        if not cfg:
            continue
        cleaned = {k: v for k, v in cfg.items() if k not in _LEGACY_KEYS}
        if len(cleaned) == len(cfg):
            continue
        updates.append((json.dumps(cleaned) if cleaned else None, album_id))
    if updates:
        conn.executemany("UPDATE albums SET config_json=? WHERE id=?", updates)
        log.info("Migration v31: stripped legacy settings keys from %d album(s)", len(updates))


def _migrate_v32(conn: sqlite3.Connection) -> None:
    """Re-run the v31 legacy-key strip for DBs that skipped v31.

    Some DBs reached user_version=31 via the fresh-DB init path before
    _migrate_v31 was added to the MIGRATIONS dispatch table.  Those DBs
    had the version number bumped but never ran the actual strip, leaving
    lan_share_token (and other legacy keys) in album config_json.  This
    step is identical to v31 — it is idempotent and safe to run twice.
    """
    _migrate_v31(conn)


# v36-v39 live in bpp.db.migrations_latest. Re-exported here so
# bpp.db.migrations keeps importing the entire v23-v39 range from
# migrations_recent without caring about the file split.
from bpp.db.migrations_latest import (  # noqa: E402, F401
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


def _migrate_v35(conn: sqlite3.Connection) -> None:
    """Re-encode face_embeddings.embedding from float64 to float32 (schema v35).

    SFace (the face-embedding model) produces 128-dim float32 vectors.
    Earlier writers in bpp/scoring/face_embed.py called .astype(np.float64)
    before serializing, which doubled the on-disk blob size (1024 → 1024 bytes
    per face) AND, more importantly, doubled the peak RAM footprint of the
    face-clustering matrix at runtime (10K faces ≈ 40 MB instead of 20 MB;
    500K faces ≈ 2 GB instead of 1 GB). The extra precision is decorative —
    the model itself runs at float32 internally, so the high half of every
    f64 lift was zero-padding, not signal.

    This step rewrites every existing embedding blob in place from float64
    (1024 bytes, 128 doubles) to float32 (512 bytes, 128 floats). New writes
    in face_embed.py go in as float32 from v35 on.

    Migration safety:
    - Idempotent: skip rows whose blob is already 512 bytes (float32 size).
    - Per-step backup is taken by the migration runner before this step,
      so a botched re-encode rolls back from <library>/photopicker.db.backup.
    - Batched UPDATEs (1000 rows per executemany) keep memory bounded
      even on huge libraries.
    - Sanity check: refuse to touch rows whose blob is not 512 or 1024 bytes;
      log a warning and leave them — those are pre-existing corruption that
      this migration shouldn't try to guess at.
    """
    import numpy as np

    EXPECTED_F32_BYTES = 128 * 4  # 512
    EXPECTED_F64_BYTES = 128 * 8  # 1024
    BATCH_SIZE = 1000

    # Count what we're working with up front so the log is meaningful.
    total = conn.execute("SELECT COUNT(*) FROM face_embeddings").fetchone()[0]
    if total == 0:
        log.info("Migration v35: face_embeddings is empty — nothing to re-encode")
        return

    cursor = conn.execute("SELECT id, embedding FROM face_embeddings")
    converted = 0
    skipped_already_f32 = 0
    skipped_bad_size = 0
    batch: list[tuple[bytes, int]] = []
    while True:
        rows = cursor.fetchmany(BATCH_SIZE)
        if not rows:
            break
        for row_id, blob in rows:
            n = len(blob) if blob else 0
            if n == EXPECTED_F32_BYTES:
                skipped_already_f32 += 1
                continue
            if n != EXPECTED_F64_BYTES:
                log.warning(
                    "Migration v35: skipping face_embeddings.id=%s with unexpected blob size %d "
                    "(expected %d for float64 or %d for float32)",
                    row_id,
                    n,
                    EXPECTED_F64_BYTES,
                    EXPECTED_F32_BYTES,
                )
                skipped_bad_size += 1
                continue
            f64 = np.frombuffer(blob, dtype=np.float64)
            f32_bytes = f64.astype(np.float32).tobytes()
            batch.append((f32_bytes, row_id))
            if len(batch) >= BATCH_SIZE:
                conn.executemany("UPDATE face_embeddings SET embedding=? WHERE id=?", batch)
                converted += len(batch)
                batch.clear()
    if batch:
        conn.executemany("UPDATE face_embeddings SET embedding=? WHERE id=?", batch)
        converted += len(batch)
    log.info(
        "Migration v35: face_embeddings dtype f64->f32 — "
        "converted=%d already_f32=%d bad_size=%d total=%d",
        converted,
        skipped_already_f32,
        skipped_bad_size,
        total,
    )


def _migrate_v34(conn: sqlite3.Connection) -> None:
    """Add dup_cluster_id for near-duplicate grouping (schema v34).

    assign_near_duplicate_clusters() in bpp/db/dedupe.py populates this
    column after import/analysis using perceptual-hash hamming distance.
    Photos in the same near-duplicate group share the same dup_cluster_id.
    Singletons and unprocessed photos have dup_cluster_id=0.

    The existing cluster_size column (set to len(cluster) for all members)
    drives the Duplicates smart album (WHERE cluster_size > 1).

    Migration safety:
    - ADD COLUMN with DEFAULT is O(1) on SQLite.
    - Backfill deferred to assign_near_duplicate_clusters() on next run.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(photos)").fetchall()}
    if "dup_cluster_id" not in cols:
        conn.execute("ALTER TABLE photos ADD COLUMN dup_cluster_id INTEGER NOT NULL DEFAULT 0")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_photos_dup_cluster "
        "ON photos(dup_cluster_id) WHERE dup_cluster_id != 0"
    )
    log.info("Migration v34: dup_cluster_id column added")


def _migrate_v33(conn: sqlite3.Connection) -> None:
    """Add Live Photo sidecar columns (schema v33).

    iPhone Live Photos export as two files: the key still frame
    (IMG_xxxx.HEIC) and a motion-component sidecar (IMG_xxxx_1.HEIC /
    IMG_xxxx_1.MOV).  Without explicit tracking these sidecars inflate
    photo counts, duplicate detection, and scoring — they appear as
    near-identical duplicates of their parent because phash equality
    correctly identifies them as visually the same image.

    is_live_photo_sidecar  — 1 when this row is a sidecar; 0 otherwise.
                             Backfilled retroactively by
                             detect_and_link_live_photo_sidecars() in
                             bpp/db/live_photo.py on the first server
                             start after this migration.
    live_photo_parent_id   — FK to the parent still frame. Nullable:
                             a sidecar whose parent was not imported (or
                             was later deleted) keeps is_live_photo_sidecar=1
                             but loses the FK.

    Migration safety:
    - ADD COLUMN with DEFAULT is O(1) on SQLite; no table rewrite.
    - Backfill is deferred to application startup to avoid blocking the
      migration transaction on large libraries.
    - Idempotent: PRAGMA table_info check before each ADD COLUMN.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(photos)").fetchall()}
    if "is_live_photo_sidecar" not in cols:
        conn.execute(
            "ALTER TABLE photos ADD COLUMN is_live_photo_sidecar BOOLEAN NOT NULL DEFAULT 0"
        )
    if "live_photo_parent_id" not in cols:
        conn.execute(
            "ALTER TABLE photos ADD COLUMN live_photo_parent_id INTEGER "
            "REFERENCES photos(id) ON DELETE SET NULL"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_photos_live_photo_parent "
        "ON photos(live_photo_parent_id) WHERE live_photo_parent_id IS NOT NULL"
    )
    log.info("Migration v33: Live Photo sidecar columns added")


# (target_version, fn) — applied in order. Gaps are intentional —
# missing version numbers (e.g., v7, v12, v17, v19, v20, v24) were
# either rolled into the surrounding step or never had a schema
# change associated with them. Keep the gaps so the version numbers

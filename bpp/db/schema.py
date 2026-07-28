"""Database schema definitions.

DDL fragments that vary by backend (autoincrement primary keys, JSON
extraction, PRAGMA-equivalents) go through `bpp.db.dialect.dialect`.
A future Postgres dialect drops in by subclassing `DBDialect`.
"""

from __future__ import annotations

import sqlite3

from bpp.constants import CLUSTER_UNASSIGNED
from bpp.db.dialect import dialect
from bpp.utils.logging import get_logger

log = get_logger(__name__)

SCHEMA_VERSION = 44

# Resolved once at import time. Module-level dialect singleton means
# this is stable for the process lifetime.
_PK = dialect.autoincrement_pk()

TABLES_SQL = f"""
CREATE TABLE IF NOT EXISTS photos (
    id {_PK},
    filepath TEXT UNIQUE NOT NULL,
    original_filename TEXT NOT NULL,
    import_batch TEXT,
    sha256 TEXT,
    file_size INTEGER NOT NULL,
    file_mtime REAL NOT NULL,
    missing BOOLEAN DEFAULT 0,
    date TEXT,
    date_day TEXT,
    date_month TEXT,
    blur_raw REAL,
    exposure_score REAL,
    face_score REAL,
    face_count INTEGER DEFAULT 0,
    largest_face_ratio REAL,
    face_center_dist REAL,
    composition_score REAL,
    skin_score REAL,
    nudity_score REAL,
    -- user override for the sensitive-photo flag (v43). NULL = follow the
    -- model (nudity_score vs SENSITIVE_NUDITY_THRESHOLD), 1 = user says
    -- sensitive, 0 = user says not sensitive. The override always wins;
    -- see is_sensitive in bpp/web/photo_dict.py — the single derivation.
    sensitive_override INTEGER,
    blur_score REAL,
    aggregate_score REAL,
    pet_count INTEGER DEFAULT 0,
    has_cat BOOLEAN DEFAULT 0,
    has_dog BOOLEAN DEFAULT 0,
    phash INTEGER,
    ahash INTEGER,
    cluster_size INTEGER DEFAULT 1,
    analyzed_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    exif_json TEXT,
    -- stable GPS columns + partial index. The map view used
    -- to scan the photos table and json_extract the coords from
    -- exif_json on every render — full table scan + JSON parse per
    -- row, ~50k photos = visible lag. These columns are the
    -- canonical source for coords going forward; exif_json keeps
    -- the rest of the metadata blob (camera, lens, ISO, etc.).
    gps_lat REAL,
    gps_lon REAL,
    is_video BOOLEAN DEFAULT 0,
    video_duration REAL,
    video_width INTEGER,
    video_height INTEGER,
    video_fps REAL,
    video_codec TEXT,
    is_raw BOOLEAN DEFAULT 0,
    deleted_at TEXT,
    hidden_at TEXT,
    -- Live Photo sidecar support (schema v33).
    --
    -- iPhone Live Photos export as two files: the key still frame
    -- (IMG_xxxx.HEIC) and a motion-component sidecar (IMG_xxxx_1.HEIC).
    -- The sidecar is a near-identical still extracted from the motion
    -- clip — not a photo the user composed or intended as a standalone
    -- image.
    --
    -- Assumptions:
    --   1. A photo is a sidecar if its basename matches <parent>_<N>.<ext>
    --      (N = 1-9, same extension) AND the parent exists in the same
    --      library directory. Cross-directory name collisions are
    --      explicitly NOT treated as sidecars.
    --   2. The sidecar is systematically lower quality than its parent
    --      (the key still was chosen by the photographer; the sidecar is
    --      a motion-sequence frame). It is excluded from scoring, picking,
    --      and all smart albums by default.
    --   3. Import-time user opt-in: the "Import Live Photo sidecars"
    --      checkbox (default: off) controls whether sidecars are imported
    --      at all. If off, they are skipped at scan time. If on, they are
    --      imported and linked but still hidden from the main grid.
    --   4. The parent-child link enables the ⊙ Live Photo badge on the
    --      parent card and (future) motion-preview in the lightbox.
    --   5. .MOV Live Photo video sidecars follow the same pattern and
    --      are treated identically (same column, same filter).
    is_live_photo_sidecar BOOLEAN NOT NULL DEFAULT 0,
    live_photo_parent_id  INTEGER REFERENCES photos(id) ON DELETE SET NULL,
    -- Near-duplicate clustering (schema v34).
    -- assign_near_duplicate_clusters() in bpp/db/dedupe.py populates this
    -- using perceptual-hash hamming distance (default threshold: 8 bits).
    -- dup_cluster_id: opaque integer shared by all photos in the same
    --   near-duplicate group. 0 means singleton (no near-duplicate found).
    -- cluster_size (already exists from earlier schema): count of photos in
    --   the group. The Duplicates smart album queries WHERE cluster_size > 1.
    dup_cluster_id INTEGER NOT NULL DEFAULT 0,
    -- Moments: visually-similar shots grouped by CLIP cosine within a time
    -- window (schema v42). Distinct from the tight phash dup_cluster_id above
    -- so "Duplicates" stays near-identical frames and "Moments" is the looser
    -- prune-the-similar surface. assign_moment_clusters() in bpp/db/moments.py
    -- populates these from the pass-1 time-windowed CLIP clustering.
    -- moment_cluster_id: 0 = no similar siblings; moment_size: photos in group.
    moment_cluster_id INTEGER NOT NULL DEFAULT 0,
    moment_size INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS albums (
    id {_PK},
    name TEXT NOT NULL,
    album_type TEXT DEFAULT 'manual',
    rule_json TEXT,
    config_json TEXT,
    k INTEGER DEFAULT 50,
    parent_id INTEGER REFERENCES albums(id) ON DELETE SET NULL,
    created_at TEXT DEFAULT (datetime('now')),
    modified_at TEXT DEFAULT (datetime('now')),
    -- v36: shadow column populated by smart_person album writers.
    -- Replaces the json_extract / LIKE '%cluster_id%' anti-pattern in
    -- the cluster→album lookup path. Indexed via idx_albums_smart_person_cluster
    -- below; lookups are now O(log N) instead of O(N) full table scan.
    smart_person_cluster_id INTEGER
);

CREATE TABLE IF NOT EXISTS album_photos (
    album_id INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
    photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    selected BOOLEAN DEFAULT 0,
    override TEXT,
    favorite BOOLEAN DEFAULT 0,
    PRIMARY KEY (album_id, photo_id)
);

CREATE TABLE IF NOT EXISTS presets (
    id {_PK},
    name TEXT UNIQUE NOT NULL,
    settings_json TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS face_embeddings (
    id {_PK},
    photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    face_index INTEGER NOT NULL,
    bbox_x INTEGER,
    bbox_y INTEGER,
    bbox_w INTEGER,
    bbox_h INTEGER,
    embedding BLOB NOT NULL,
    cluster_id INTEGER DEFAULT {CLUSTER_UNASSIGNED},
    quality REAL,
    identity TEXT,
    user_confirmed INTEGER DEFAULT 0,
    -- v40: detector input size at extraction time. Lets the read path
    -- reconstruct the exact detector dimensions regardless of current
    -- settings, closing the Bug #9 class of failures. NULL on pre-v40
    -- rows; the read path falls back to the current config.
    extraction_max_long_side INTEGER,
    -- v41: id of the registered model that produced this row. Lets
    -- the Batch-7 derived-data-purge flow find every embedding
    -- produced by a specific model at removal time. NULL on
    -- pre-v41 rows; the purge skips NULL rows so the migration
    -- doesn't accidentally wipe historical data on first run.
    producing_model_id TEXT,
    UNIQUE(photo_id, face_index)
);

CREATE TABLE IF NOT EXISTS clip_embeddings (
    id {_PK},
    photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    model_name TEXT NOT NULL DEFAULT 'ViT-B-32',
    embedding BLOB NOT NULL,
    computed_at TEXT DEFAULT (datetime('now')),
    UNIQUE(photo_id, model_name)
);

CREATE TABLE IF NOT EXISTS dedup_feedback (
    id {_PK},
    photo_id_a INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    photo_id_b INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    similarity REAL NOT NULL,
    verdict TEXT NOT NULL CHECK(verdict IN ('same', 'different')),
    album_id INTEGER REFERENCES albums(id) ON DELETE SET NULL,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(photo_id_a, photo_id_b, album_id)
);

CREATE TABLE IF NOT EXISTS pet_detections (
    id {_PK},
    photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    detection_index INTEGER NOT NULL,
    class TEXT NOT NULL,
    confidence REAL,
    bbox_x INTEGER,
    bbox_y INTEGER,
    bbox_w INTEGER,
    bbox_h INTEGER,
    cluster_id INTEGER DEFAULT {CLUSTER_UNASSIGNED},
    UNIQUE(photo_id, detection_index)
);

CREATE TABLE IF NOT EXISTS tags (
    id {_PK},
    name TEXT UNIQUE NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS photo_tags (
    photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    created_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (photo_id, tag_id)
);

CREATE TABLE IF NOT EXISTS photo_person_tags (
    photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    cluster_id INTEGER NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (photo_id, cluster_id)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dismissed_smart_albums (
    album_type TEXT NOT NULL,
    rule_json TEXT NOT NULL,
    dismissed_at TEXT DEFAULT (datetime('now')),
    UNIQUE(album_type, rule_json)
);

CREATE TABLE IF NOT EXISTS photo_edits (
    id {_PK},
    photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    brightness REAL DEFAULT 1.0,
    contrast REAL DEFAULT 1.0,
    saturation REAL DEFAULT 1.0,
    sharpness REAL DEFAULT 1.0,
    crop_x REAL,
    crop_y REAL,
    crop_w REAL,
    crop_h REAL,
    rotation INTEGER DEFAULT 0,
    flip_h BOOLEAN DEFAULT 0,
    flip_v BOOLEAN DEFAULT 0,
    warmth REAL DEFAULT 0.0,
    highlights REAL DEFAULT 0.0,
    shadows REAL DEFAULT 0.0,
    vignette REAL DEFAULT 0.0,
    grain REAL DEFAULT 0.0,
    fade REAL DEFAULT 0.0,
    redeye_json TEXT,
    filter_name TEXT,
    exposure REAL DEFAULT 0.0,
    brilliance REAL DEFAULT 0.0,
    black_point REAL DEFAULT 0.0,
    vibrance REAL DEFAULT 0.0,
    tint REAL DEFAULT 0.0,
    definition REAL DEFAULT 0.0,
    noise_reduction REAL DEFAULT 0.0,
    straighten REAL DEFAULT 0.0,
    perspective_v REAL DEFAULT 0.0,
    perspective_h REAL DEFAULT 0.0,
    auto_enhanced BOOLEAN DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    modified_at TEXT DEFAULT (datetime('now')),
    UNIQUE(photo_id)
);

CREATE TABLE IF NOT EXISTS face_cluster_feedback (
    id {_PK},
    action TEXT NOT NULL CHECK(action IN ('merge', 'reassign_in', 'reassign_out')),
    cluster_id_a INTEGER NOT NULL,
    cluster_id_b INTEGER,
    distance REAL NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS face_hard_negatives (
    cluster_id_a INTEGER NOT NULL,
    cluster_id_b INTEGER NOT NULL,
    count INTEGER NOT NULL DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(cluster_id_a, cluster_id_b)
);

CREATE TABLE IF NOT EXISTS share_access_log (
    id {_PK},
    ts INTEGER NOT NULL,
    ip TEXT NOT NULL,
    user_agent TEXT NOT NULL DEFAULT ''
);

-- Trusted-on-first-use device list for LAN sharing. A phone scanning
-- the QR creates a row here; the owner approves on Mac (sets
-- trusted_at). Forward-compat: user_id and scope_json are NULL today
-- but exist so a future user-account migration can backfill without
-- a schema change.
CREATE TABLE IF NOT EXISTS share_devices (
    id {_PK},
    fingerprint TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    ip_at_pair TEXT NOT NULL DEFAULT '',
    user_id INTEGER,
    scope_json TEXT,
    first_seen INTEGER NOT NULL,
    last_seen INTEGER NOT NULL,
    trusted_at INTEGER,
    revoked_at INTEGER,
    prev_revoked INTEGER NOT NULL DEFAULT 0
);

-- Operation journal: tracks long-running mutations (permanent delete,
-- face clustering, CLIP extraction) so a SIGKILL/crash mid-flight
-- leaves a recovery breadcrumb. Entries with completed_at IS NULL on
-- startup are replayed by per-kind handlers in bpp/db/journal.py.
CREATE TABLE IF NOT EXISTS operation_journal (
    id {_PK},
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    started_at INTEGER NOT NULL,
    completed_at INTEGER
);

-- v37: face extraction journal. Tracks per-phase completion for a single
-- ``extract_and_cluster_faces`` invocation so a SIGKILL between phases
-- resumes at the next phase rather than re-running from phase 1.
--
-- Lifecycle:
--   * One row per ``run_id`` (uuid string) at the start of the orchestrator.
--   * ``phases_complete`` is a bitmask of completed phases (bit 0 = phase 1,
--     bit 1 = phase 2, ...). Phases 1-7 use bits 0-6.
--   * ``snapshot_json`` carries PreExtractSnapshot fields as JSON so a
--     resumed run picks up dismissed_slots / old_cluster_photos / etc.
--   * ``completed_at`` set when phase 7 finishes.
--
-- Recovery: startup scans for rows with completed_at IS NULL and re-runs
-- the orchestrator with the saved run_id. Each phase function is
-- idempotent so re-running already-committed phases is safe.
CREATE TABLE IF NOT EXISTS face_extraction_journal (
    id {_PK},
    run_id TEXT NOT NULL UNIQUE,
    phases_complete INTEGER NOT NULL DEFAULT 0,
    snapshot_json TEXT,
    started_at INTEGER NOT NULL,
    completed_at INTEGER,
    -- v39 (T0.4): bounded recovery retries. Recovery handler increments
    -- before each attempt; if it exceeds MAX_RECOVERY_RETRIES the row
    -- is force-completed with `completed_at = -1` (sentinel for "gave
    -- up"). Without this, a deterministic phase-7 failure would loop
    -- on every server restart forever.
    retry_count INTEGER NOT NULL DEFAULT 0
);

-- Auto-generated memories (event-based photo stories).
-- Populated by bpp/db/memories.py:generate_memories() which clusters
-- photos by time + content and writes one row per memory + N rows in
-- memory_photos. Regenerated rather than incrementally updated.
CREATE TABLE IF NOT EXISTS memories (
    id {_PK},
    title TEXT NOT NULL,
    date_start TEXT,
    date_end TEXT,
    hero_photo_id INTEGER REFERENCES photos(id) ON DELETE SET NULL,
    photo_count INTEGER DEFAULT 0,
    score REAL DEFAULT 0,
    generated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS memory_photos (
    memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    PRIMARY KEY (memory_id, photo_id)
);
"""

INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_photos_filepath ON photos(filepath);
CREATE INDEX IF NOT EXISTS idx_photos_sha256 ON photos(sha256);
CREATE INDEX IF NOT EXISTS idx_photos_date ON photos(date);
CREATE INDEX IF NOT EXISTS idx_photos_missing ON photos(missing);
CREATE INDEX IF NOT EXISTS idx_album_photos_album ON album_photos(album_id);
CREATE INDEX IF NOT EXISTS idx_album_photos_photo ON album_photos(photo_id);
CREATE INDEX IF NOT EXISTS idx_face_embeddings_photo ON face_embeddings(photo_id);
CREATE INDEX IF NOT EXISTS idx_face_embeddings_cluster ON face_embeddings(cluster_id);
CREATE INDEX IF NOT EXISTS idx_face_embeddings_cluster_photo
    ON face_embeddings(cluster_id, photo_id);
-- v41: covering index for the Batch-7 derived-data-purge filter.
CREATE INDEX IF NOT EXISTS idx_face_embeddings_producing_model
    ON face_embeddings(producing_model_id);
CREATE INDEX IF NOT EXISTS idx_clip_embeddings_photo ON clip_embeddings(photo_id);
CREATE INDEX IF NOT EXISTS idx_dedup_feedback_photos ON dedup_feedback(photo_id_a, photo_id_b);
CREATE INDEX IF NOT EXISTS idx_dedup_feedback_album ON dedup_feedback(album_id);
CREATE INDEX IF NOT EXISTS idx_photos_deleted ON photos(deleted_at);
CREATE INDEX IF NOT EXISTS idx_photos_has_cat ON photos(has_cat);
CREATE INDEX IF NOT EXISTS idx_photos_has_dog ON photos(has_dog);
CREATE INDEX IF NOT EXISTS idx_pet_detections_photo ON pet_detections(photo_id);
CREATE INDEX IF NOT EXISTS idx_pet_detections_cluster ON pet_detections(cluster_id);
CREATE INDEX IF NOT EXISTS idx_photo_person_tags_photo ON photo_person_tags(photo_id);
CREATE INDEX IF NOT EXISTS idx_photo_person_tags_cluster ON photo_person_tags(cluster_id);
CREATE INDEX IF NOT EXISTS idx_tags_name ON tags(name);
CREATE INDEX IF NOT EXISTS idx_photo_tags_photo ON photo_tags(photo_id);
CREATE INDEX IF NOT EXISTS idx_photo_tags_tag ON photo_tags(tag_id);
CREATE INDEX IF NOT EXISTS idx_albums_parent ON albums(parent_id);
CREATE INDEX IF NOT EXISTS idx_photos_hidden ON photos(hidden_at);
CREATE INDEX IF NOT EXISTS idx_photos_date_month ON photos(date_month);
CREATE INDEX IF NOT EXISTS idx_photo_edits_photo ON photo_edits(photo_id);
CREATE INDEX IF NOT EXISTS idx_photos_aggregate_score ON photos(aggregate_score);
CREATE INDEX IF NOT EXISTS idx_photos_phash ON photos(phash);
CREATE INDEX IF NOT EXISTS idx_photos_is_video ON photos(is_video);
CREATE INDEX IF NOT EXISTS idx_album_photos_album_selected ON album_photos(album_id, selected);
CREATE UNIQUE INDEX IF NOT EXISTS idx_albums_type_rule ON albums(album_type, rule_json); -- v44
-- v36: partial index on the shadow column. Only rows where the column
-- is non-NULL get indexed (smart_person albums); everything else is
-- ignored. Lookup pattern is "find the album for cluster X" which is
-- now an O(log N) probe instead of a full-table scan + JSON parse.
CREATE INDEX IF NOT EXISTS idx_albums_smart_person_cluster
    ON albums(smart_person_cluster_id)
    WHERE smart_person_cluster_id IS NOT NULL;

-- v38: auto-populate the smart_person_cluster_id shadow column from
-- the cluster_id field of rule_json on every INSERT/UPDATE of a
-- smart_person album. Single source of truth — writers can't "forget"
-- the shadow column. The trigger uses json_extract (sqlite3 has it
-- built in since 3.38); falls back to LIKE-style extraction would be
-- an option for older sqlite but every supported python ships 3.38+.
CREATE TRIGGER IF NOT EXISTS trg_albums_sync_smart_person_cluster_insert
AFTER INSERT ON albums
WHEN NEW.album_type = 'smart_person' AND NEW.rule_json IS NOT NULL
BEGIN
    UPDATE albums
    SET smart_person_cluster_id = CAST(json_extract(NEW.rule_json, '$.cluster_id') AS INTEGER)
    WHERE id = NEW.id
      AND json_extract(NEW.rule_json, '$.cluster_id') IS NOT NULL
      AND (smart_person_cluster_id IS NULL
           OR smart_person_cluster_id !=
              CAST(json_extract(NEW.rule_json, '$.cluster_id') AS INTEGER));
END;

CREATE TRIGGER IF NOT EXISTS trg_albums_sync_smart_person_cluster_update
AFTER UPDATE OF rule_json, album_type ON albums
WHEN NEW.album_type = 'smart_person' AND NEW.rule_json IS NOT NULL
BEGIN
    UPDATE albums
    SET smart_person_cluster_id = CAST(json_extract(NEW.rule_json, '$.cluster_id') AS INTEGER)
    WHERE id = NEW.id
      AND json_extract(NEW.rule_json, '$.cluster_id') IS NOT NULL
      AND (smart_person_cluster_id IS NULL
           OR smart_person_cluster_id !=
              CAST(json_extract(NEW.rule_json, '$.cluster_id') AS INTEGER));
END;
CREATE INDEX IF NOT EXISTS idx_share_access_log_ts ON share_access_log(ts);
CREATE INDEX IF NOT EXISTS idx_share_devices_fingerprint ON share_devices(fingerprint);
CREATE INDEX IF NOT EXISTS idx_share_devices_state ON share_devices(trusted_at, revoked_at);
CREATE INDEX IF NOT EXISTS idx_operation_journal_pending
    ON operation_journal(kind, completed_at);
CREATE INDEX IF NOT EXISTS idx_face_extraction_journal_pending
    ON face_extraction_journal(completed_at);
-- partial index on the GPS columns. Sparse data (5-30% of
-- photos have GPS), so a partial index keeps the b-tree compact
-- AND lets `WHERE gps_lat IS NOT NULL AND gps_lon IS NOT NULL`
-- queries skip most of the table without scanning.
CREATE INDEX IF NOT EXISTS idx_photos_gps ON photos(gps_lat, gps_lon)
    WHERE gps_lat IS NOT NULL AND gps_lon IS NOT NULL;
-- partial index covering the Moments query (moment_size > 1) —
-- mirrors the v42 migration so fresh DBs match migrated ones.
CREATE INDEX IF NOT EXISTS idx_photos_moment ON photos (moment_cluster_id)
    WHERE moment_size > 1;
-- partial index covering the Sensitive smart-album scan: flagged photos
-- are sparse (model hits + manual overrides), so index only those rows.
-- Mirrors the v43 migration.
CREATE INDEX IF NOT EXISTS idx_photos_sensitive ON photos (nudity_score)
    WHERE nudity_score > 0 OR sensitive_override IS NOT NULL;
"""


def create_tables(conn: sqlite3.Connection) -> None:
    """Create all tables and indexes if they don't exist."""
    # Detect a truly fresh DB before DDL runs (the tables don't exist yet).
    # After create_tables, the DB file always exists, so checking file
    # existence is unreliable. Checking the settings table for 'first_run'
    # is the canonical approach.
    is_new_db = (
        conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='photos'"
        ).fetchone()[0]
        == 0
    )
    conn.executescript(TABLES_SQL)
    _migrate(conn)
    conn.executescript(INDEXES_SQL)
    # NOTE: do NOT write PRAGMA user_version here. `_migrate()` already
    # sets it after each migration step. Writing it unconditionally caused
    # "database is locked" errors because PRAGMA user_version requires an
    # exclusive write lock — when background threads (phash compute, face
    # worker) hold a brief transaction, every concurrent new-thread call to
    # init_db() would fail. `_migrate()` handles the version correctly for
    # both fresh DBs (runs all steps, each bumps version) and up-to-date
    # DBs (no steps run, version already correct — no write needed).
    if is_new_db:
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('first_run', 'true')")
    conn.commit()


# Migration orchestrator (+ exif backfill helper) live in schema_migrate.py
# since the v0.1 cleanup. Re-exported here so callers like create_tables (above)
# and any external code that imported _migrate from schema keep working.
from bpp.db.schema_migrate import (  # noqa: E402, F401
    _backfill_exif_json,
    _migrate,
    _resolve_db_path,
)

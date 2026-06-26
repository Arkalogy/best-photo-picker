"""Schema migration steps v36 through v41.

Extracted from :mod:`bpp.db.migrations_recent` so both files stay
under the 500-LOC soft cap. The split is by version range and
keeps the most recent migrations together — they share the
Phase-3.5 / Phase-5 audit context that motivated them (the
smart_person_cluster_id shadow column and the face_extraction_journal
table). v40 hardens face_embeddings against the Bug #9 class —
self-describing detector input size per row so a config change can't
silently invalidate stored coords.

migrations_recent re-exports these names so :mod:`bpp.db.migrations`
keeps importing the whole v23-v40 range from a single module.
"""

from __future__ import annotations

import sqlite3

from bpp.db.dialect import dialect
from bpp.utils.logging import get_logger

log = get_logger(__name__)


def _migrate_v38(conn: sqlite3.Connection) -> None:
    """Add auto-sync triggers for smart_person_cluster_id (schema v38).

    P5b refinement on v36. Until v38, every writer of a smart_person
    album had to remember to also set the ``smart_person_cluster_id``
    shadow column. ``create_album`` did this correctly; raw INSERTs
    (test fixtures + a couple of legacy paths) did not, leaving the
    shadow column NULL on those rows and breaking the indexed reader
    sites that P5b migrated.

    v38 makes the shadow column self-maintaining via two triggers:

    * After INSERT on albums with album_type='smart_person', copy
      ``cluster_id`` from rule_json into the shadow column.
    * After UPDATE of rule_json or album_type on a smart_person row,
      re-sync the shadow column.

    Both triggers use ``json_extract`` (sqlite 3.38+ built-in; every
    supported Python ships that version). Idempotent — the trigger
    only updates when the resolved value differs from the current
    shadow value, so it can't loop on itself.

    Idempotent migration: skips if the triggers already exist.
    """
    existing = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='trigger' AND name LIKE 'trg_albums_sync_smart_person_cluster_%'"
        ).fetchall()
    }
    if "trg_albums_sync_smart_person_cluster_insert" not in existing:
        conn.execute(
            "CREATE TRIGGER IF NOT EXISTS trg_albums_sync_smart_person_cluster_insert "
            "AFTER INSERT ON albums "
            "WHEN NEW.album_type = 'smart_person' AND NEW.rule_json IS NOT NULL "
            "BEGIN "
            "UPDATE albums "
            "SET smart_person_cluster_id = "
            "CAST(json_extract(NEW.rule_json, '$.cluster_id') AS INTEGER) "
            "WHERE id = NEW.id "
            "  AND json_extract(NEW.rule_json, '$.cluster_id') IS NOT NULL "
            "  AND (smart_person_cluster_id IS NULL "
            "       OR smart_person_cluster_id != "
            "          CAST(json_extract(NEW.rule_json, '$.cluster_id') AS INTEGER));"
            "END"
        )
    if "trg_albums_sync_smart_person_cluster_update" not in existing:
        conn.execute(
            "CREATE TRIGGER IF NOT EXISTS trg_albums_sync_smart_person_cluster_update "
            "AFTER UPDATE OF rule_json, album_type ON albums "
            "WHEN NEW.album_type = 'smart_person' AND NEW.rule_json IS NOT NULL "
            "BEGIN "
            "UPDATE albums "
            "SET smart_person_cluster_id = "
            "CAST(json_extract(NEW.rule_json, '$.cluster_id') AS INTEGER) "
            "WHERE id = NEW.id "
            "  AND json_extract(NEW.rule_json, '$.cluster_id') IS NOT NULL "
            "  AND (smart_person_cluster_id IS NULL "
            "       OR smart_person_cluster_id != "
            "          CAST(json_extract(NEW.rule_json, '$.cluster_id') AS INTEGER));"
            "END"
        )

    # Backfill any smart_person rows where rule_json is set but the
    # shadow column is still NULL (pre-trigger rows). One SQL statement
    # covers the whole table.
    # NOTE: no explicit conn.commit() — the migration runner owns the
    # savepoint + commit. An internal commit would release the
    # savepoint and break rollback on error.
    cur = conn.execute(
        "UPDATE albums "
        "SET smart_person_cluster_id = CAST(json_extract(rule_json, '$.cluster_id') AS INTEGER) "
        "WHERE album_type = 'smart_person' "
        "  AND rule_json IS NOT NULL "
        "  AND smart_person_cluster_id IS NULL "
        "  AND json_extract(rule_json, '$.cluster_id') IS NOT NULL"
    )
    if cur.rowcount:
        log.info(
            "Migration v38: triggers installed; backfilled "
            "smart_person_cluster_id on %d row(s) the v36 pass missed",
            cur.rowcount,
        )
    else:
        log.info("Migration v38: triggers installed; backfill found 0 rows needing repair")


def _migrate_v41(conn: sqlite3.Connection) -> None:
    """Add ``producing_model_id`` column to face_embeddings (v41).

    Batch 7 / item 21 of the legal-posture rollout. The column tags
    every face_embeddings row with the registry id of the model that
    produced it (``sface_yunet``, ``dlib_face_recognition_resnet_v1``,
    or a ``byom_<hash>`` user-supplied entry). The
    derived-data-purge flow at model-removal time reads this column
    to find every embedding produced by a specific model and delete
    them as a single batch, strengthening the biometric-privacy
    posture (Colorado HB24-1130, Texas CUBI) the legal-posture spec
    flagged in item 13.

    Idempotent: skips when the column already exists. Default value
    NULL for existing rows is intentional — pre-v41 rows have no
    recorded producing model, and the purge flow treats NULL as
    "unknown producing model" (skipped by the purge so existing
    rows are not lost on first run after the migration). New writes
    (Batch 7 wires ``face_extraction_phase5.py``) populate the
    column based on the resolved
    :func:`bpp.scoring.face_embed.embedding_method` value.
    """
    cols = dialect.column_names(conn, "face_embeddings")
    if not cols:
        log.info("Migration v41: face_embeddings not present yet, skipping")
        return
    if "producing_model_id" in cols:
        log.info("Migration v41: producing_model_id column already present, no-op")
        return
    conn.execute("ALTER TABLE face_embeddings ADD COLUMN producing_model_id TEXT")
    # Cheap covering index for the purge query (Batch 7's
    # count_derived_for_model / purge_derived_for_model both filter
    # on producing_model_id). Without an index, the purge does a
    # full-table scan, which is fine for a small library but
    # noticeable at 50k+ rows.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_face_embeddings_producing_model "
        "ON face_embeddings (producing_model_id)"
    )
    log.info("Migration v41: added producing_model_id column + index to face_embeddings")


def _migrate_v40(conn: sqlite3.Connection) -> None:
    """Add ``extraction_max_long_side`` column to face_embeddings (v40).

    Bug #9 hardening. Before v40, the bbox_x/y/w/h coordinates in
    face_embeddings were stored in detector-input pixel space (the
    image after ``load_and_downscale(filepath, max_long_side)``), but
    the read sites (``bp_faces_photo.py:_compute_bbox_pct`` etc.)
    recomputed the detector scale from the *current* config value.
    If the user changed Settings → Max scoring resolution between
    extraction and read, every stored bbox would render at the wrong
    coordinates — exactly the systematic upper-left-corner overlay
    Bug #9 documented (3,705 face rows stored at ~320 px detector
    space, then re-interpreted at 1024 → bboxes appeared as tiny
    upper-left boxes, false-positive clusters formed from background
    crops).

    v40 makes face_embeddings self-describing: each row records the
    ``max_long_side`` the detector was actually configured with, so
    the read path can reconstruct the exact detector dimensions
    regardless of current settings. New writes (in
    ``face_extraction_phase5.py``) include the value; read sites
    (``bp_faces_photo.py``) use the per-row value, falling back to
    the current config when NULL (pre-v40 rows on a DB that hasn't
    been re-extracted yet — the fallback is best-effort and surfaces
    a once-per-process warning so the user re-extracts).

    Idempotent: skips when the column already exists. Default value
    NULL for existing rows is intentional — the migration deliberately
    does NOT guess at historical max_long_side, because guessing wrong
    is exactly what produced Bug #9. The read path's fallback uses
    the current setting, which is what callers already do today; v40
    just gives correct rows a way to opt out of that guess.
    """
    cols = dialect.column_names(conn, "face_embeddings")
    if not cols:
        log.info("Migration v40: face_embeddings not present yet, skipping")
        return
    if "extraction_max_long_side" in cols:
        log.info("Migration v40: extraction_max_long_side column already present, no-op")
        return
    conn.execute("ALTER TABLE face_embeddings ADD COLUMN extraction_max_long_side INTEGER")
    log.info("Migration v40: added extraction_max_long_side column to face_embeddings")


def _migrate_v39(conn: sqlite3.Connection) -> None:
    """Add ``retry_count`` column to face_extraction_journal (T0.4 schema v39).

    Before v39 a deterministic phase-7 failure (or any post-snapshot
    crash that the resume couldn't get past) would leave the row
    pending forever — each server restart would re-attempt and re-fail,
    and the row never moved. v39 adds ``retry_count`` so the recovery
    handler can bail after N attempts and force-complete the row with
    a sentinel ``completed_at = -1`` (interpreted as "gave up; manual
    intervention required").

    Idempotent: skips when the column already exists. Default value 0
    handles pre-existing rows seamlessly.
    """
    cols = dialect.column_names(conn, "face_extraction_journal")
    if not cols:
        # No journal table yet — v37 hasn't run. This is a defensive
        # path; in practice the migration runner orders strictly so
        # v37 runs before v39.
        log.info("Migration v39: face_extraction_journal not present yet, skipping")
        return
    if "retry_count" in cols:
        log.info("Migration v39: retry_count column already present, no-op")
        return
    conn.execute(
        "ALTER TABLE face_extraction_journal ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0"
    )
    log.info("Migration v39: added retry_count column to face_extraction_journal")


def _migrate_v37(conn: sqlite3.Connection) -> None:
    """Add face_extraction_journal table for per-phase resume (schema v37).

    P3 of refactor-plan.md. Pre-v37 a SIGKILL during ``extract_and_cluster_faces``
    forced a full re-run from phase 1 on next startup. v37 introduces a
    per-run journal so the orchestrator can pick up at the next
    incomplete phase, with the pre-extract snapshot rehydrated from the
    journal row.

    Schema:
        face_extraction_journal(
            id PK,
            run_id TEXT UNIQUE,
            phases_complete INTEGER  -- bitmask: bit N = phase N+1 done
            snapshot_json TEXT       -- serialized PreExtractSnapshot
            started_at INTEGER,
            completed_at INTEGER
        )

    Idempotent: skips if the table already exists. Safe to re-run.
    """
    # SQLite-friendly existence probe — works across both SQLite and
    # postgres dialects via the dialect's column_names helper, which
    # returns an empty set when the table doesn't exist.
    cols = dialect.column_names(conn, "face_extraction_journal")
    if not cols:
        autoincrement_pk = dialect.autoincrement_pk()
        conn.execute(
            f"CREATE TABLE face_extraction_journal ("
            f" id {autoincrement_pk},"
            " run_id TEXT NOT NULL UNIQUE,"
            " phases_complete INTEGER NOT NULL DEFAULT 0,"
            " snapshot_json TEXT,"
            " started_at INTEGER NOT NULL,"
            " completed_at INTEGER"
            ")"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_face_extraction_journal_pending "
            "ON face_extraction_journal(completed_at)"
        )
        # No explicit commit — the migration runner's savepoint owns the
        # commit boundary.
        log.info("Migration v37: created face_extraction_journal table + index")
    else:
        log.info("Migration v37: face_extraction_journal already present, no-op")


def _migrate_v36(conn: sqlite3.Connection) -> None:
    """Add smart_person_cluster_id shadow column to albums (schema v36).

    P5 of refactor-plan.md. Until v36 the cluster→album lookup ran via:

        SELECT id, name FROM albums
        WHERE album_type='smart_person' AND json_extract(rule_json,'$.cluster_id') = ?

    On SQLite that is a full-table scan + JSON parse per row — at
    50k photos with hundreds of person clusters and dozens of
    refreshes per day the cost is real. The audit measured ~30% of
    the post-cluster smart-album refresh time on a representative
    library.

    v36 promotes the per-row ``cluster_id`` from inside ``rule_json``
    to a top-level ``smart_person_cluster_id`` column on the same row.
    A partial index covers the column so the lookup becomes an O(log N)
    probe. ``rule_json`` is still written by every smart_person album
    writer (and read by the album list endpoint) — the column is a
    shadow, not a replacement, so a botched migration can be reverted
    by dropping the column without touching album data.

    Migration safety:

    * Idempotent: skips if the column already exists.
    * Backfill is a single ``UPDATE`` driven by SQLite's
      ``json_extract`` / ``typeof`` — no Python loop, no per-row
      commits, atomic with respect to other writers under the
      surrounding SAVEPOINT. Pre-T4 the loop did N round-trips and N
      WARN lines for malformed rows; the single-statement form is
      both faster and friendlier to the SAVEPOINT contract. Malformed
      / non-integer rows are summarized in one WARN line instead of
      a per-row stream.
    * Index is created with ``IF NOT EXISTS`` so re-applying the
      migration on a partial state is safe.
    """
    cols = dialect.column_names(conn, "albums")
    if "smart_person_cluster_id" not in cols:
        conn.execute("ALTER TABLE albums ADD COLUMN smart_person_cluster_id INTEGER")
        log.info("Migration v36: added smart_person_cluster_id column to albums")

    # Diagnostics first: how many smart_person rows have unusable
    # rule_json (NULL, invalid JSON, missing cluster_id key, or non-integer
    # cluster_id). ``json_valid()`` is the gate that prevents json_extract
    # from raising ``sqlite3.OperationalError: malformed JSON`` on garbage
    # strings; without it the single COUNT below would crash on the first
    # bad row.
    malformed = conn.execute(
        "SELECT COUNT(*) FROM albums "
        "WHERE album_type = 'smart_person' "
        "  AND smart_person_cluster_id IS NULL "
        "  AND (rule_json IS NULL "
        "       OR json_valid(rule_json) = 0 "
        "       OR json_extract(rule_json, '$.cluster_id') IS NULL "
        "       OR typeof(json_extract(rule_json, '$.cluster_id')) != 'integer')"
    ).fetchone()[0]
    if malformed:
        log.warning(
            "Migration v36: %d smart_person album(s) have malformed rule_json "
            "or non-integer cluster_id; leaving smart_person_cluster_id NULL "
            "on those rows (readers fall back to the JSON path during the "
            "deprecation window)",
            malformed,
        )

    # Single-statement backfill. ``json_valid(rule_json) = 1`` screens out
    # malformed strings BEFORE ``json_extract`` runs on them (it raises on
    # bad input in some SQLite builds). ``typeof(...) = 'integer'`` then
    # screens out NULL / string / float cluster_ids — only real integers
    # survive into the UPDATE set.
    cursor = conn.execute(
        "UPDATE albums "
        "SET smart_person_cluster_id = "
        "    CAST(json_extract(rule_json, '$.cluster_id') AS INTEGER) "
        "WHERE album_type = 'smart_person' "
        "  AND smart_person_cluster_id IS NULL "
        "  AND rule_json IS NOT NULL "
        "  AND json_valid(rule_json) = 1 "
        "  AND typeof(json_extract(rule_json, '$.cluster_id')) = 'integer'"
    )
    backfilled = cursor.rowcount or 0

    # Partial index over the new column. Match the schema.py definition
    # so callers running fresh-init see the same shape.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_albums_smart_person_cluster "
        "ON albums(smart_person_cluster_id) "
        "WHERE smart_person_cluster_id IS NOT NULL"
    )

    log.info(
        "Migration v36: backfilled smart_person_cluster_id on %d row(s); "
        "%d row(s) had malformed/non-integer rule_json",
        backfilled,
        malformed,
    )


def _migrate_v42(conn: sqlite3.Connection) -> None:
    """Add Moments columns to photos (v42).

    Moments groups visually-similar shots (CLIP cosine within a time
    window) so the user can review near-identical attempts and prune
    down to the keeper(s). Stored separately from the tight phash
    ``dup_cluster_id`` / ``cluster_size`` so "Duplicates" stays
    near-identical frames and "Moments" is the looser surface.

    ``assign_moment_clusters()`` in bpp/db/moments.py populates these:
      - moment_cluster_id: opaque int shared by a similar-shot group;
        0 = no similar siblings (singleton).
      - moment_size: photos in the group. The Moments surface queries
        WHERE moment_size > 1.

    Idempotent: skips columns that already exist. Default 0 / 1 for
    existing rows is intentional — they read as "ungrouped" until the
    next assign_moment_clusters() run backfills them.
    """
    cols = dialect.column_names(conn, "photos")
    if not cols:
        log.info("Migration v42: photos table not present yet, skipping")
        return
    if "moment_cluster_id" not in cols:
        conn.execute("ALTER TABLE photos ADD COLUMN moment_cluster_id INTEGER NOT NULL DEFAULT 0")
    if "moment_size" not in cols:
        conn.execute("ALTER TABLE photos ADD COLUMN moment_size INTEGER NOT NULL DEFAULT 1")
    # Partial index covering the Moments query (moment_size > 1) — mirrors
    # the phash dup index. Keeps the "show me reviewable groups" scan cheap
    # at 50k+ photos.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_photos_moment "
        "ON photos (moment_cluster_id) WHERE moment_size > 1"
    )
    log.info("Migration v42: added moment_cluster_id + moment_size columns + index to photos")


def _migrate_v43(conn: sqlite3.Connection) -> None:
    """Add the sensitive-photo override column to photos (v43).

    ``sensitive_override`` is the user's per-photo correction of the
    NudeNet flag: NULL = follow the model (nudity_score vs
    SENSITIVE_NUDITY_THRESHOLD), 1 = user says sensitive, 0 = user says
    not sensitive. The override always wins; the single derivation lives
    in ``is_sensitive`` (bpp/web/photo_dict.py) so every surface — the
    Sensitive smart album, the export review gate, the lightbox chip —
    reads the same verdict.

    Idempotent: skips the column when it already exists.
    """
    cols = dialect.column_names(conn, "photos")
    if not cols:
        log.info("Migration v43: photos table not present yet, skipping")
        return
    if "sensitive_override" not in cols:
        conn.execute("ALTER TABLE photos ADD COLUMN sensitive_override INTEGER")
    # Partial index covering the Sensitive smart-album scan — flagged rows
    # are sparse. Mirrored in the canonical schema (INDEXES_SQL).
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_photos_sensitive "
        "ON photos (nudity_score) WHERE nudity_score > 0 OR sensitive_override IS NOT NULL"
    )
    log.info("Migration v43: added sensitive_override column + index to photos")

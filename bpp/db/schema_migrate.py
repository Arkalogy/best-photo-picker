"""Schema migration orchestrator + a small EXIF backfill helper.

Owns the per-step migration runner — savepoint-wrapped fn calls,
`.backup` rotation between steps, post-restore sentinel handling, and
the newer-than-this-binary refusal. The actual per-version steps live
in :mod:`bpp.db.migrations` and :mod:`bpp.db.migrations_recent`; this
module just walks them.

Extracted from :mod:`bpp.db.schema` during the v0.1 cleanup so the
host module stays under the 500-LOC soft cap. :func:`_migrate` is the
only entry point ``create_tables`` calls from schema.py.
"""

from __future__ import annotations

import json
import os
import sqlite3

from bpp.db.dialect import dialect
from bpp.utils.logging import get_logger

log = get_logger(__name__)


def _resolve_db_path(conn: sqlite3.Connection) -> str:
    """Recover the SQLite file path from a live connection.

    `PRAGMA database_list` returns rows of (seq, name, file). We want
    the row where name == 'main'. In-memory DBs return an empty
    string; callers must skip backup for those.
    """
    try:
        for row in conn.execute("PRAGMA database_list").fetchall():
            if (row[1] if not isinstance(row, sqlite3.Row) else row["name"]) == "main":
                return row[2] if not isinstance(row, sqlite3.Row) else row["file"]
    except sqlite3.Error:
        pass
    return ""


def _migrate(conn: sqlite3.Connection) -> None:
    """Run any needed schema migrations.

    Each step runs inside a savepoint so a mid-migration crash rolls back
    that step cleanly.  ``user_version`` is bumped after each successful
    step. The actual per-version steps live in ``bpp.db.migrations`` so
    this orchestrator stays trivially auditable.

    Per-step backup: after each step's commit, ``backup_db`` is
    called so ``.backup`` reflects the highest-known-good schema. With
    only the startup-time backup, a multi-version upgrade (v23->v29 = 6
    steps) that fails at step 5 left the user with a v23 .backup —
    restore-backup would then roll back ALL the successful intermediate
    work too. Per-step backup keeps the rollback window to a single
    migration. Backup failure is logged but does NOT roll back the
    successful schema bump (the version bump is the source of truth for
    "step completed").

    two safety properties on the per-step backup loop:

      1. `.backup.prev` is preserved across the whole `_migrate()`
         run. Only the FIRST step's backup is allowed to rotate
         `.backup → .backup.prev`. Subsequent steps pass
         `preserve_prev=True` so `.backup.prev` continues to hold
         the pre-upgrade snapshot — that's the snapshot the user
         will reach for via `bpp db restore-backup --previous` if
         the multi-step upgrade goes bad on a later step.

      2. After a `bpp db restore-backup` run, the next process boot
         consumes a sentinel that signals "skip ALL backup activity
         this startup". The sentinel turns off the *startup* backup
         (already wired) AND every per-step backup here — otherwise
         forward migrations re-running on the just-restored DB
         would push the bad upgrade's state right back into
         `.backup`, undoing the restore.
    """
    from bpp.db.connection import backup_db, is_post_restore_skip_backup
    from bpp.db.migrations import MIGRATIONS
    from bpp.db.schema import SCHEMA_VERSION

    db_path = _resolve_db_path(conn)

    initial_version = dialect.get_user_version(conn)

    # P2: refuse a DB at a NEWER schema version than this binary
    # could migrate to. Without this, the migration loop silently
    # no-ops (every step's `version >= target` short-circuit) and
    # the binary opens the DB as if it understood the schema.
    # Real-world trigger: user runs newer bpp on machine A, syncs
    # library to machine B where bpp is older. Mirrors the guard
    # in `bpp db restore-backup`.
    #
    # Compare against the highest migration target in the live
    # MIGRATIONS list (not just SCHEMA_VERSION) so test fixtures
    # that monkeypatch MIGRATIONS to higher version numbers still
    # exercise the per-step path with a deliberately lower
    # initial_version.
    max_target = max((target for target, _ in MIGRATIONS), default=SCHEMA_VERSION)
    expected_max = max(SCHEMA_VERSION, max_target)
    if initial_version > expected_max:
        raise RuntimeError(
            f"Database at {db_path or '<unknown>'} is at schema "
            f"version {initial_version}, but this bpp build expects "
            f"version {expected_max} or lower. Refusing to open — "
            "running this binary against a newer-schema DB risks "
            "data loss. Either upgrade bpp, or run "
            "`bpp db restore-backup --previous` to roll back."
        )

    # Per-step backup only matters for *upgrades* of an existing DB
    # (initial_version > 0). On a fresh DB (version 0 → SCHEMA_VERSION
    # via TABLES_SQL + idempotent migration replay), there is no user
    # data to lose, so per-step backups would just churn `.backup`
    # files for no benefit and break tests that expect a clean dir.
    skip_post_restore = is_post_restore_skip_backup()
    do_per_step_backup = bool(db_path) and initial_version > 0 and not skip_post_restore
    if skip_post_restore and bool(db_path) and initial_version > 0:
        log.info(
            "Restore-pending sentinel was consumed earlier this startup — "
            "skipping per-step backup for this _migrate() run to preserve "
            "the just-restored .backup / .backup.prev pair."
        )

    # log when migrations are about to run and what target
    # range we're crossing. Without this, a multi-step upgrade was
    # silent — a user reporting "first launch took 30 seconds" had
    # no log evidence which migrations ran or how long each took.
    if initial_version < SCHEMA_VERSION:
        log.info(
            "DB schema migration: v%d -> v%d (%d step(s) pending)",
            initial_version,
            SCHEMA_VERSION,
            sum(1 for t, _ in MIGRATIONS if t > initial_version),
        )

    version = initial_version
    backup_step_count = 0
    for target, fn in MIGRATIONS:
        if version >= target:
            continue
        sp = f"migrate_v{target}"
        conn.execute(f"SAVEPOINT {sp}")
        try:
            fn(conn)
            conn.execute(f"RELEASE SAVEPOINT {sp}")
            dialect.set_user_version(conn, target)
            conn.commit()
            version = target
            # per-step success breadcrumb. Pairs with the
            # existing error-path log; together they let on-call
            # tell whether a migration step ran cleanly or rolled
            # back, just from server.log.
            log.info("Migration step v%d committed", target)
        except Exception:
            conn.execute(f"ROLLBACK TO SAVEPOINT {sp}")
            log.error("Migration to v%d failed — rolled back", target, exc_info=True)
            raise

        # Roll the .backup snapshot forward. Skipped on fresh inits
        # (no data) and in-memory DBs (no path). Backup failure is
        # logged but does NOT roll back the committed schema bump.
        # Pin .backup.prev after the first per-step backup so the
        # pre-upgrade snapshot survives the whole `_migrate()` run.
        if do_per_step_backup:
            try:
                backup_db(db_path, preserve_prev=backup_step_count > 0)
                backup_step_count += 1
            except Exception:
                log.warning(
                    "Per-step backup after migration v%d failed — "
                    "schema bump committed; .backup may lag the live "
                    "DB by one step",
                    target,
                    exc_info=True,
                )

    # completion breadcrumb. Closes the loop opened by the
    # "migration v18 -> v29 (N steps pending)" line above; together
    # they let an operator confirm a multi-step upgrade landed
    # cleanly without re-deriving it from the per-step lines.
    if version != initial_version:
        log.info("DB schema migration complete: v%d -> v%d", initial_version, version)


def _backfill_exif_json(conn: sqlite3.Connection) -> None:
    """One-time migration: extract EXIF metadata for photos that have none."""
    rows = conn.execute(
        "SELECT id, filepath FROM photos WHERE exif_json IS NULL AND deleted_at IS NULL"
    ).fetchall()
    if not rows:
        return

    # Import here to avoid circular imports at module level
    from bpp.exif_utils import extract_exif_metadata

    updated = 0
    for row in rows:
        photo_id = row[0] if not isinstance(row, sqlite3.Row) else row["id"]
        filepath = row[1] if not isinstance(row, sqlite3.Row) else row["filepath"]
        if not os.path.isfile(filepath):
            continue
        try:
            exif = extract_exif_metadata(filepath)
            if exif:
                conn.execute(
                    "UPDATE photos SET exif_json = ? WHERE id = ?",
                    (json.dumps(exif), photo_id),
                )
                updated += 1
        except Exception:
            log.debug("EXIF backfill failed for %s", filepath, exc_info=True)
            continue
    if updated:
        log.info("EXIF backfill: updated %d photos", updated)

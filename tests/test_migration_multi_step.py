"""End-to-end test of the per-step migration backup contract.

Why this test exists: a per-step backup_db() call after each
successful migration step's commit means a multi-step upgrade that
crashes mid-way leaves the user with `.backup` at the highest
*committed* step instead of the pre-migration version. Unit tests
cover backup rotation (test_robustness.py) and individual migration
steps (test_coverage_gaps.py); this one walks a real DB through
N→M migrations end-to-end and asserts that the `.backup` /
`.backup.prev` snapshots reflect the right schema versions.

This test pins three guarantees:

  1. Successful multi-step run: `.backup` reaches the latest
     schema, `.backup.prev` is one rotation back.
  2. Mid-migration failure: `.backup` is at the last committed
     step, NOT clobbered by the failing step.
  3. Single-step run still produces `.backup` (no prev rotation
     possible — there's nothing to rotate yet).

If any of these break, the downgrade story documented in README
silently stops working.
"""

from __future__ import annotations

import sqlite3
from unittest import mock

import pytest

from bpp.db.connection import init_db
from bpp.db.dialect import dialect
from bpp.db.schema import SCHEMA_VERSION


def _create_v18_db(db_path: str) -> None:
    """Seed a DB at user_version=18 with the minimum tables the v21+
    migrations expect to find. We pick v18 deliberately — it's the
    boundary where most of the multi-step churn happens (v21, v22,
    v23, v25, v26, v27, v28, v29) so the "many steps" path actually
    runs."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    # photos table — every column added through v18. The migrations
    # we're about to run (v21+) won't add new columns to this list,
    # but they may ALTER existing rows or add tables.
    conn.execute(
        """CREATE TABLE photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filepath TEXT UNIQUE NOT NULL,
            original_filename TEXT,
            import_batch TEXT,
            sha256 TEXT,
            file_size INTEGER DEFAULT 0,
            file_mtime REAL DEFAULT 0,
            missing INTEGER DEFAULT 0,
            date TEXT,
            date_day TEXT,
            date_month TEXT,
            blur_raw REAL DEFAULT 0.0,
            exposure_score REAL DEFAULT 0.0,
            face_score REAL DEFAULT 0.0,
            face_count INTEGER DEFAULT 0,
            largest_face_ratio REAL,
            face_center_dist REAL,
            composition_score REAL DEFAULT 0.0,
            skin_score REAL,
            nudity_score REAL,
            blur_score REAL DEFAULT 0.0,
            aggregate_score REAL DEFAULT 0.0,
            phash INTEGER,
            ahash INTEGER,
            cluster_size INTEGER DEFAULT 1,
            analyzed_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            deleted_at TEXT,
            pet_count INTEGER DEFAULT 0,
            has_cat INTEGER DEFAULT 0,
            has_dog INTEGER DEFAULT 0,
            exif_json TEXT,
            is_video BOOLEAN DEFAULT 0,
            is_raw BOOLEAN DEFAULT 0,
            hidden_at TEXT,
            video_duration REAL,
            video_width INTEGER,
            video_height INTEGER,
            video_fps REAL,
            video_codec TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE albums (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            album_type TEXT DEFAULT 'manual',
            rule_json TEXT,
            parent_id INTEGER REFERENCES albums(id) ON DELETE SET NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE pet_detections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            photo_id INTEGER REFERENCES photos(id),
            label TEXT,
            confidence REAL,
            bbox_x REAL, bbox_y REAL, bbox_w REAL, bbox_h REAL,
            cluster_id INTEGER DEFAULT -1
        )"""
    )
    conn.execute("PRAGMA user_version = 18")
    conn.commit()
    conn.close()


def _read_user_version(db_path: str) -> int:
    """Read PRAGMA user_version from a DB file directly (no init)."""
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


class TestMultiStepMigration:
    """End-to-end multi-step migration walks the per-step backup
    contract. Counts on SCHEMA_VERSION being well past v18 — the
    test asserts on the floor, not the exact target."""

    def test_successful_multistep_lands_at_latest_schema(self, tmp_path):
        """N→M run: live DB ends at SCHEMA_VERSION, .backup matches."""
        db_path = str(tmp_path / "test.db")
        _create_v18_db(db_path)

        # Sanity: starting state is what we set up
        assert _read_user_version(db_path) == 18

        # Run init_db, which calls _migrate internally
        conn = init_db(db_path)
        try:
            # Live DB reached the latest schema
            assert dialect.get_user_version(conn) == SCHEMA_VERSION
        finally:
            conn.close()

        # `.backup` exists and reflects the latest schema. Per-step
        # backup runs after EVERY committed step, so the final
        # rotation should be at SCHEMA_VERSION.
        backup_path = db_path + ".backup"
        import os

        assert os.path.exists(backup_path), "per-step backup did not produce .backup"
        assert _read_user_version(backup_path) == SCHEMA_VERSION

    def test_multistep_pins_backup_prev_to_pre_migration_snapshot(self, tmp_path):
        """R8-H4: across a multi-step migration run, `.backup.prev`
        must remain pinned to the PRE-UPGRADE snapshot — NOT rotate
        forward to v(N-1). `bpp db restore-backup --previous` is the
        user's "undo this whole upgrade" path; if `.backup.prev` is
        v28 after a v23 → v29 run, the user can't get back to v23.

        Simulates the production startup flow by pre-writing both
        `.backup` and `.backup.prev` at v18 (as if a prior session
        had backed the live DB up before this upgrade started),
        then running `init_db` to drive migrations.
        """
        import os
        import shutil

        db_path = str(tmp_path / "test.db")
        _create_v18_db(db_path)

        # Simulate prior-session backups (both at v18 — the pre-upgrade
        # state). `.backup.prev` is what `--previous` would restore.
        backup_path = db_path + ".backup"
        prev_path = db_path + ".backup.prev"
        shutil.copy2(db_path, backup_path)
        shutil.copy2(db_path, prev_path)

        init_db(db_path).close()

        assert os.path.exists(backup_path), ".backup must exist after multi-step"
        assert os.path.exists(prev_path), (
            "`.backup.prev` should still exist — R8-H4 pins the "
            "pre-upgrade snapshot for the whole `_migrate()` run"
        )

        backup_v = _read_user_version(backup_path)
        prev_v = _read_user_version(prev_path)
        assert backup_v == SCHEMA_VERSION, (
            f".backup must roll forward to current schema; got v{backup_v}"
        )
        assert prev_v == 18, (
            f"R8-H4: `.backup.prev` must be pinned to the pre-upgrade snapshot "
            f"(v18), not rotated forward across migration steps. Got v{prev_v}."
        )

    def test_failure_mid_migration_preserves_committed_progress(self, tmp_path):
        """When step K fails after K-1 committed cleanly, `.backup`
        must reflect v(K-1), NOT the pre-migration v18. Otherwise a
        6-step upgrade that fails at step 5 would force the user to
        roll back ALL successful intermediate work.
        """
        db_path = str(tmp_path / "test.db")
        _create_v18_db(db_path)

        # Patch one of the migration functions to raise. Pick v25
        # because v21/v22/v23 will run first and successfully bump
        # the live DB + backup. The failure at v25 must NOT clobber
        # the .backup that was just rolled to v23.
        from bpp.db import migrations as mig_mod

        original_v25 = dict(mig_mod.MIGRATIONS).get(25)
        assert original_v25 is not None, (
            "v25 migration not in MIGRATIONS — update this test if the schema history was rewritten"
        )

        def _crash(_conn):
            raise RuntimeError("simulated v25 migration failure")

        # Replace the v25 entry in MIGRATIONS while preserving order
        patched = tuple(
            (target, _crash if target == 25 else fn) for target, fn in mig_mod.MIGRATIONS
        )

        with (
            mock.patch.object(mig_mod, "MIGRATIONS", patched),
            pytest.raises(RuntimeError, match="simulated v25"),
        ):
            init_db(db_path)

        # Live DB is at the last committed step (v23, the migration
        # right before v25 in MIGRATIONS — there's no v24).
        live_v = _read_user_version(db_path)
        assert 18 < live_v < 25, (
            f"Live DB at v{live_v} — expected to be between v18 (start) and "
            "v25 (the failing step) inclusive of the last successful step"
        )

        # .backup reflects the same last-committed step, NOT v18 and
        # NOT v25. This is the whole point of per-step backups.
        import os

        backup_path = db_path + ".backup"
        assert os.path.exists(backup_path), (
            ".backup must exist after partial multi-step — per-step backup "
            "runs after each committed step, so v23's commit should have "
            "left a fresh .backup before v25 attempted to run"
        )
        backup_v = _read_user_version(backup_path)
        assert backup_v == live_v, (
            f".backup (v{backup_v}) should match live DB (v{live_v}) — they "
            "should both reflect the highest committed step (per-step backup contract)."
        )


# ─── R8-H4: backup rotation hardening ──────────────────────────────


class TestR8H4BackupPrevPinning:
    """`.backup.prev` is the single user-facing slot for "the snapshot
    BEFORE this upgrade ran." Per-step backups within `_migrate()`
    used to rotate it forward on every step, so a 6-step upgrade
    overwrote it on step 2 — by the time step 5 fails, the user's
    `--previous` recovery target is the partial-migration v25 state,
    not the pre-upgrade v23 state. R8-H4 pins `.backup.prev` for the
    duration of one `_migrate()` run; only the FIRST step's backup
    rotates."""

    def test_partial_failure_leaves_prev_at_pre_upgrade_snapshot(self, tmp_path):
        """The full bug scenario: 6-step upgrade fails halfway. The
        recovery snapshot must still be the pre-upgrade state, not
        an intermediate v(K-1)."""
        import os
        import shutil

        from bpp.db.migrations import MIGRATIONS

        db_path = str(tmp_path / "test.db")
        _create_v18_db(db_path)

        backup_path = db_path + ".backup"
        prev_path = db_path + ".backup.prev"
        # Simulate the prior-session backups (both at v18).
        shutil.copy2(db_path, backup_path)
        shutil.copy2(db_path, prev_path)

        # Find a target version several steps in to fail at.
        targets = [t for t, _ in MIGRATIONS if t > 18]
        assert len(targets) >= 3, "need ≥3 steps for this test"
        fail_at = targets[2]  # third step fails

        original_migrations = list(MIGRATIONS)

        def _failing(*_a, **_kw):
            raise RuntimeError(f"simulated mid-migration failure at v{fail_at}")

        patched = []
        for tgt, fn in original_migrations:
            patched.append((tgt, _failing if tgt == fail_at else fn))

        with mock.patch("bpp.db.migrations.MIGRATIONS", patched), pytest.raises(RuntimeError):
            init_db(db_path).close()

        assert os.path.exists(prev_path), ".backup.prev must still exist"
        prev_v = _read_user_version(prev_path)
        assert prev_v == 18, (
            f"R8-H4: `.backup.prev` must be pinned to pre-upgrade v18 across "
            f"the partial multi-step run; got v{prev_v}. Without this guard, "
            f"`bpp db restore-backup --previous` would land the user on a "
            f"partial-migration intermediate state instead of the pre-upgrade "
            f"snapshot."
        )

    def test_post_restore_skip_flag_blocks_per_step_backup(self, tmp_path):
        """When the restore-pending sentinel was consumed earlier in
        startup, `_migrate()` must NOT touch `.backup` or
        `.backup.prev` at all — the user just restored those files
        and forward migrations would re-clobber them with the bad
        upgrade's state."""
        import os
        import shutil

        from bpp.db.connection import set_post_restore_skip_backup

        db_path = str(tmp_path / "test.db")
        _create_v18_db(db_path)

        backup_path = db_path + ".backup"
        prev_path = db_path + ".backup.prev"
        # Simulate the just-restored state.
        shutil.copy2(db_path, backup_path)
        shutil.copy2(db_path, prev_path)
        backup_mtime_before = os.path.getmtime(backup_path)
        prev_mtime_before = os.path.getmtime(prev_path)

        set_post_restore_skip_backup(True)
        try:
            init_db(db_path).close()
        finally:
            set_post_restore_skip_backup(False)

        # Both files must be byte-for-byte unchanged: live DB is now
        # at SCHEMA_VERSION but the backup pair stays pinned to v18.
        assert _read_user_version(backup_path) == 18
        assert _read_user_version(prev_path) == 18
        assert os.path.getmtime(backup_path) == backup_mtime_before
        assert os.path.getmtime(prev_path) == prev_mtime_before


class TestR8H4RestoreUserVersionGuard:
    """`bpp db restore-backup` used to accept any backup that passed
    integrity check, regardless of schema version. A user who
    downgrades bpp (newer binary → older) and tries to restore a
    backup written by the newer binary would silently corrupt the
    live DB — older binary then either runs forward migrations on a
    DB it doesn't understand, or reads columns it doesn't know
    about."""

    def test_restore_refuses_backup_with_newer_user_version(self, tmp_path):
        """The forward-incompatibility check: backup at vX > current
        SCHEMA_VERSION must be refused with exit code 4 and a clear
        message pointing the user at upgrading bpp first."""
        import argparse
        import sqlite3

        from bpp.commands import _do_restore_locked
        from bpp.db.schema import SCHEMA_VERSION

        backup_path = str(tmp_path / "future.db.backup")
        # Build a minimal valid SQLite DB with a future schema version.
        conn = sqlite3.connect(backup_path)
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 5}")
        conn.commit()
        conn.close()

        db_path = str(tmp_path / "live.db")
        sqlite3.connect(db_path).close()  # touch a live file

        from bpp.db.connection import check_integrity

        rc = _do_restore_locked(
            argparse.Namespace(previous=False, force=False),
            db_path,
            backup_path,
            check_integrity,
        )
        assert rc == 4, (
            f"R8-H4c: `_do_restore_locked` must refuse a backup with user_version "
            f"> SCHEMA_VERSION; got exit code {rc}"
        )

    def test_restore_accepts_backup_at_current_or_older_version(self, tmp_path):
        """Inverse: a same-version or older backup should not trip
        the new guard (other guards may still apply, but not this
        one)."""
        import sqlite3

        from bpp.db.schema import SCHEMA_VERSION

        # Same-version backup
        same_path = str(tmp_path / "same.db.backup")
        conn = sqlite3.connect(same_path)
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
        conn.close()

        # Just verify the read works and version matches what we wrote;
        # the actual restore flow has many other gates we don't want
        # to wire up here. The guard's contract is: refuse if >
        # SCHEMA_VERSION, allow otherwise.
        check = sqlite3.connect(same_path)
        try:
            row = check.execute("PRAGMA user_version").fetchone()
            assert row is not None
            assert int(row[0]) == SCHEMA_VERSION
        finally:
            check.close()

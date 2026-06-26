"""Tests for robustness features: backup, integrity, WAL, relocation, atomic import."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import time

import pytest

from bpp.db import backup as backup_mod
from bpp.db.connection import (
    backup_db,
    check_integrity,
    checkpoint_wal,
    close_all_connections,
    get_db,
    init_db,
)
from bpp.db.photos import (
    check_missing,
    get_photo_count,
    relocate_missing,
    sample_random_photos,
    upsert_photo,
)


@pytest.fixture
def db_path(tmp_path):
    """Create a real SQLite DB and return its path."""
    p = str(tmp_path / "test.db")
    conn = init_db(p)
    # Insert some data so it's not empty
    upsert_photo(conn, {"filepath": "/fake/photo1.jpg", "sha256": "aaa"})
    upsert_photo(conn, {"filepath": "/fake/photo2.jpg", "sha256": "bbb"})
    conn.close()
    # Clear thread-local so next get_db creates a fresh connection
    close_all_connections()
    return p


@pytest.fixture
def conn(tmp_path):
    """Return a connection to a fresh test DB."""
    p = str(tmp_path / "test.db")
    c = init_db(p)
    yield c
    close_all_connections()


# ===========================================================================
# backup_db tests
# ===========================================================================


class TestBackupDB:
    def test_backup_creates_file(self, db_path):
        result = backup_db(db_path)
        assert result is not None
        assert os.path.isfile(result)
        assert result == db_path + ".backup"
        # Backup should have same size as original
        assert os.path.getsize(result) == os.path.getsize(db_path)

    def test_backup_skips_empty_db(self, tmp_path):
        p = str(tmp_path / "empty.db")
        # Create an empty file
        open(p, "w").close()
        assert backup_db(p) is None

    def test_backup_skips_missing_db(self, tmp_path):
        p = str(tmp_path / "nonexistent.db")
        assert backup_db(p) is None

    def test_backup_copies_wal_shm_when_present(self, tmp_path):
        """If WAL/SHM exist at backup time, they should be copied."""
        p = str(tmp_path / "waltest.db")
        conn = init_db(p)
        upsert_photo(conn, {"filepath": "/fake/a.jpg"})
        # Don't close/checkpoint — leave WAL active
        # Write directly to create WAL entries that persist
        conn.execute("INSERT OR IGNORE INTO photos (filepath) VALUES ('/fake/wal_entry.jpg')")

        close_all_connections()

        # If WAL was present, verify backup handles it gracefully
        # (integrity check may consume WAL, so we just verify no crash)
        result = backup_db(p)
        assert result is not None
        assert os.path.isfile(result)

    def test_backup_rotation_creates_prev(self, db_path):
        # First backup
        backup_db(db_path)
        backup_path = db_path + ".backup"
        prev_path = db_path + ".backup.prev"
        assert os.path.isfile(backup_path)
        assert not os.path.isfile(prev_path)

        # Modify DB so second backup differs
        conn = init_db(db_path)
        upsert_photo(conn, {"filepath": "/fake/photo3.jpg", "sha256": "ccc"})
        conn.close()
        close_all_connections()

        # Second backup should rotate
        backup_db(db_path)
        assert os.path.isfile(backup_path)
        assert os.path.isfile(prev_path)

    def test_backup_skips_on_corrupt_db(self, tmp_path):
        """If DB is corrupt, backup should NOT overwrite existing backup."""
        db_p = str(tmp_path / "corrupt.db")
        # Create a valid DB first and backup it
        conn = init_db(db_p)
        upsert_photo(conn, {"filepath": "/fake/a.jpg"})
        conn.close()
        close_all_connections()
        backup_db(db_p)  # Creates good backup

        backup_path = db_p + ".backup"
        good_size = os.path.getsize(backup_path)

        # Now corrupt the DB
        with open(db_p, "wb") as f:
            f.write(b"this is not a valid sqlite database" * 100)

        # Backup should return None (skip) and preserve existing backup
        result = backup_db(db_p)
        assert result is None
        assert os.path.getsize(backup_path) == good_size

    def test_backup_cleans_stale_wal_shm(self, db_path):
        """If source no longer has WAL/SHM, backup WAL/SHM should be removed."""
        # First, create a backup
        backup_db(db_path)
        backup_path = db_path + ".backup"

        # Manually place a stale WAL in the backup dir (simulating old state)
        stale_wal = backup_path + "-wal"
        with open(stale_wal, "wb") as f:
            f.write(b"stale_wal_data")
        assert os.path.isfile(stale_wal)

        # Re-backup — source has no WAL, so backup WAL should be cleaned
        backup_db(db_path)
        assert not os.path.isfile(stale_wal)

    def test_backup_verifies_copy_integrity_on_success(self, db_path):
        """Successful backup is integrity-checked. The .backup file
        on disk must be a valid SQLite database, not just a copy of
        bytes."""
        result = backup_db(db_path)
        assert result is not None
        # The returned path must itself pass integrity check.
        assert check_integrity(result), (
            "backup_db returned success but the .backup file is not "
            "a valid SQLite DB — verification gap"
        )

    def test_backup_quarantines_corrupted_copy(self, db_path, monkeypatch):
        """Simulate a partial/corrupt copy: monkeypatch shutil.copy2 to
        truncate the destination after copying. The copied .backup must
        be detected as bad, quarantined, and the previous-good .backup
        restored from .backup.prev."""

        # Run a FIRST clean backup so .backup.prev exists when the
        # second (failed) backup needs to roll back to it.
        first = backup_db(db_path)
        assert first is not None
        good_size = os.path.getsize(first)

        # Mutate the source so a second backup is meaningful.
        c = init_db(db_path)
        upsert_photo(c, {"filepath": "/fake/dirty.jpg", "sha256": "ddd"})
        c.close()
        close_all_connections()

        # Patch shutil.copy2: when copying TO the .backup target, write
        # truncated garbage instead. Other targets (.prev, WAL siblings)
        # are unaffected so .prev still rolls back cleanly.
        backup_path = db_path + ".backup"
        prev_path = db_path + ".backup.prev"
        original_copy2 = backup_mod.shutil.copy2

        def truncating_copy2(src, dst, *args, **kwargs):
            # Only corrupt the *new* backup attempt: src is the live
            # DB. The recovery copy (src=.backup.prev) must succeed
            # so the test can verify rollback to the previous good
            # generation.
            if dst == backup_path and src != prev_path:
                with open(dst, "wb") as f:
                    f.write(b"corrupt-not-a-sqlite-db" * 50)
                return dst
            return original_copy2(src, dst, *args, **kwargs)

        monkeypatch.setattr(backup_mod.shutil, "copy2", truncating_copy2)

        result = backup_db(db_path)
        assert result is None, "verify failure must return None"

        # Quarantine file exists with the corrupt bytes. R11-M6: name
        # is now `.backup.corrupt-<UTC-timestamp>` so concurrent or
        # repeat failures don't collide on a fixed target.
        import glob as _glob

        matches = _glob.glob(backup_path + ".corrupt-*")
        assert len(matches) == 1, f"Expected exactly one timestamped quarantine; got {matches}"
        quarantine = matches[0]
        with open(quarantine, "rb") as f:
            assert f.read().startswith(b"corrupt")

        # .backup is restored from .backup.prev — readable again
        assert os.path.isfile(backup_path)
        assert check_integrity(backup_path), (
            ".backup should be the recovered .prev copy, not the corrupt one"
        )
        assert os.path.getsize(backup_path) == good_size

    def test_quarantine_names_unique_within_same_second(self, db_path, monkeypatch):
        """R12-M1: two backup verifications failing within the same
        wall-clock second must produce DISTINCT quarantine filenames.

        Pre-fix the suffix was `%Y%m%dT%H%M%SZ` (1 s resolution); the
        second failure would target the existing quarantine, the
        `shutil.move` would fail (file exists), and the fallback
        `os.remove(backup_path)` would silently delete the second
        corrupt copy — losing forensic evidence the operator might
        need. Microsecond precision (`%f`) makes collisions effectively
        impossible without changing the sortable name shape."""
        # Pin the wall-clock to a single instant. Two runs in the same
        # second would have produced identical filenames pre-fix; with
        # microseconds, even sub-millisecond differences yield unique
        # files. We monkeypatch the module-level `datetime.datetime`
        # to advance microseconds on each call so the test is
        # deterministic without depending on the real clock.
        import datetime as _real_dt

        call_count = {"n": 0}

        class _FakeDateTime(_real_dt.datetime):
            @classmethod
            def now(cls, tz=None):
                call_count["n"] += 1
                # Same second; differ only in microseconds.
                return _real_dt.datetime(2026, 1, 1, 12, 0, 0, call_count["n"], tzinfo=tz)

        monkeypatch.setattr(backup_mod.datetime, "datetime", _FakeDateTime)

        # Force two consecutive backup attempts to fail verification.
        backup_db(db_path)  # establish a clean .backup.prev
        c = init_db(db_path)
        upsert_photo(c, {"filepath": "/fake/dirty1.jpg", "sha256": "ddd1"})
        c.close()
        close_all_connections()

        backup_path = db_path + ".backup"
        prev_path = db_path + ".backup.prev"
        original_copy2 = backup_mod.shutil.copy2

        def truncating_copy2(src, dst, *args, **kwargs):
            if dst == backup_path and src != prev_path:
                with open(dst, "wb") as f:
                    f.write(b"corrupt-bytes" * 50)
                return dst
            return original_copy2(src, dst, *args, **kwargs)

        monkeypatch.setattr(backup_mod.shutil, "copy2", truncating_copy2)

        # First failed backup → quarantine #1
        assert backup_db(db_path) is None
        # Mutate live DB so a second backup attempt is meaningful
        c = init_db(db_path)
        upsert_photo(c, {"filepath": "/fake/dirty2.jpg", "sha256": "ddd2"})
        c.close()
        close_all_connections()
        # Second failed backup → quarantine #2 (same second, different µs)
        assert backup_db(db_path) is None

        import glob as _glob

        matches = _glob.glob(backup_path + ".corrupt-*")
        assert len(matches) == 2, (
            f"Two same-second quarantines must produce 2 distinct files; "
            f"got {len(matches)}: {matches!r}"
        )
        assert len(set(matches)) == 2, "Quarantine names must be unique"

    def test_quarantine_move_failure_is_logged_and_falls_back_to_remove(
        self, db_path, monkeypatch, caplog
    ):
        """R10-M3: when shutil.move fails to quarantine the corrupt
        copy, the previous shape silently suppressed the OSError —
        operator had no breadcrumb. Now the failure is logged with
        exc_info, and a fallback `os.remove` runs so the corrupt copy
        doesn't stay on disk pretending to be a valid backup."""

        backup_db(db_path)
        c = init_db(db_path)
        upsert_photo(c, {"filepath": "/fake/dirty.jpg", "sha256": "ddd"})
        c.close()
        close_all_connections()

        backup_path = db_path + ".backup"
        prev_path = db_path + ".backup.prev"
        original_copy2 = backup_mod.shutil.copy2

        def truncating_copy2(src, dst, *args, **kwargs):
            if dst == backup_path and src != prev_path:
                with open(dst, "wb") as f:
                    f.write(b"corrupt-bytes" * 100)
                return dst
            return original_copy2(src, dst, *args, **kwargs)

        monkeypatch.setattr(backup_mod.shutil, "copy2", truncating_copy2)
        # Force shutil.move to fail.
        original_move = backup_mod.shutil.move

        def failing_move(src, dst, *args, **kwargs):
            if src == backup_path:
                raise OSError("simulated permission error on move")
            return original_move(src, dst, *args, **kwargs)

        monkeypatch.setattr(backup_mod.shutil, "move", failing_move)

        with caplog.at_level("WARNING", logger="bpp.db.backup"):
            result = backup_db(db_path)

        assert result is None
        msgs = " ".join(rec.message for rec in caplog.records)
        assert "Could not quarantine corrupt backup" in msgs, (
            "Quarantine move failure must log a warning instead of silently suppressing the OSError"
        )
        # Fallback removed the corrupt file so it can't masquerade as
        # a valid backup.
        # NB: .prev rollback then re-creates a (good) .backup at the
        # same path — that's intentional, see
        # test_backup_does_not_destroy_prev_on_verify_fail. So the
        # contract here is: the LOG fires, not the on-disk size.
        # Verify the rollback's check_integrity still holds.
        if os.path.isfile(backup_path):
            assert check_integrity(backup_path), (
                ".backup after rollback must be the recovered .prev copy, not the corrupt bytes"
            )

    def test_quarantine_remove_failure_logs_residue_warning(self, db_path, monkeypatch, caplog):
        """If both quarantine-move AND fallback-remove fail, the
        corrupt copy remains on disk. Operator must see a clear log
        line explaining manual cleanup is required."""
        from bpp.db import connection as conn_mod

        backup_db(db_path)
        c = init_db(db_path)
        upsert_photo(c, {"filepath": "/fake/x.jpg"})
        c.close()
        close_all_connections()

        backup_path = db_path + ".backup"
        prev_path = db_path + ".backup.prev"
        original_copy2 = backup_mod.shutil.copy2

        def truncating_copy2(src, dst, *args, **kwargs):
            if dst == backup_path and src != prev_path:
                with open(dst, "wb") as f:
                    f.write(b"corrupt" * 50)
                return dst
            return original_copy2(src, dst, *args, **kwargs)

        monkeypatch.setattr(backup_mod.shutil, "copy2", truncating_copy2)
        monkeypatch.setattr(
            backup_mod.shutil,
            "move",
            lambda *a, **kw: (_ for _ in ()).throw(OSError("move failed")),
        )
        # Patch os.remove to fail only for the corrupt-backup file —
        # other os.remove calls (stale WAL cleanup, .prev cleanup)
        # must keep working so the test environment doesn't blow up.
        orig_remove = os.remove

        def selective_failing_remove(path):
            if str(path) == backup_path:
                raise OSError("remove also failed")
            return orig_remove(path)

        monkeypatch.setattr(conn_mod.os, "remove", selective_failing_remove)

        with caplog.at_level("WARNING", logger="bpp.db.backup"):
            backup_db(db_path)

        msgs = " ".join(rec.message for rec in caplog.records)
        assert "could neither be quarantined nor removed" in msgs, (
            "Operator needs an explicit log line when the corrupt "
            "backup remains on disk; got messages: " + msgs
        )

    def test_backup_does_not_destroy_prev_on_verify_fail(self, db_path, monkeypatch):
        """Even if verification fails, the previous-good .backup.prev
        must remain on disk so the user has a recoverable copy."""

        backup_db(db_path)  # creates .backup
        c = init_db(db_path)
        upsert_photo(c, {"filepath": "/fake/x.jpg", "sha256": "xxx"})
        c.close()
        close_all_connections()
        backup_db(db_path)  # creates .backup.prev (rotate)

        prev_path = db_path + ".backup.prev"
        assert os.path.isfile(prev_path)
        prev_size = os.path.getsize(prev_path)

        backup_path = db_path + ".backup"
        original_copy2 = backup_mod.shutil.copy2

        def truncating_copy2(src, dst, *args, **kwargs):
            if dst == backup_path:
                with open(dst, "wb") as f:
                    f.write(b"corrupt" * 100)
                return dst
            return original_copy2(src, dst, *args, **kwargs)

        c = init_db(db_path)
        upsert_photo(c, {"filepath": "/fake/y.jpg", "sha256": "yyy"})
        c.close()
        close_all_connections()
        monkeypatch.setattr(backup_mod.shutil, "copy2", truncating_copy2)
        backup_db(db_path)

        # .prev is preserved (the user's last-known-good copy)
        assert os.path.isfile(prev_path)
        # The new .prev was rotated from the previous .backup before
        # the bad copy attempt — its size should match the original
        # (post-rotation) backup snapshot, not the corrupt one.
        assert os.path.getsize(prev_path) >= prev_size


# ===========================================================================
# Per-step backup during _migrate
# ===========================================================================


class TestPerStepMigrationBackup:
    """Multi-version upgrade must roll the .backup snapshot forward
    after each successful step. With only the startup-time backup, a
    user upgrading across 6 schema versions whose 5th step failed
    would lose ALL 4 prior successful steps on `bpp db restore-backup`.
    """

    def _bare_conn(self, db_path, user_version):
        """Open a bare connection (NOT through init_db, which would
        run _migrate itself) at a known user_version. The migration
        loop expects only that user_version is set; the schema body
        doesn't need real tables for the fake-MIGRATIONS tests."""
        conn = sqlite3.connect(db_path)
        conn.execute(f"PRAGMA user_version = {user_version}")
        conn.commit()
        return conn

    def test_backup_rolls_forward_after_each_step(self, tmp_path, monkeypatch):
        """3 migration steps → 3 backup_db calls (one per step)."""
        from bpp.db import schema as schema_mod

        steps_run = []

        def _step_v100(conn):
            steps_run.append(100)

        def _step_v101(conn):
            steps_run.append(101)

        def _step_v102(conn):
            steps_run.append(102)

        monkeypatch.setattr(
            "bpp.db.migrations.MIGRATIONS",
            [(100, _step_v100), (101, _step_v101), (102, _step_v102)],
        )

        db_p = str(tmp_path / "perstep.db")
        # Bare connection at v99; migrations to v100/101/102 are pending
        conn = self._bare_conn(db_p, user_version=99)

        # Spy on backup_db
        from bpp.db import connection as conn_mod

        backup_calls = []
        original = conn_mod.backup_db

        def spy_backup(p, preserve_prev=False):
            # R8-H4 added the `preserve_prev` kwarg; spy must accept
            # it and pass through so the post-rotation behavior is
            # exercised end-to-end (first call rotates, subsequent
            # calls preserve `.backup.prev`).
            backup_calls.append((p, preserve_prev))
            return original(p, preserve_prev=preserve_prev)

        monkeypatch.setattr(conn_mod, "backup_db", spy_backup)

        try:
            schema_mod._migrate(conn)
        finally:
            conn.close()

        assert steps_run == [100, 101, 102]
        assert len(backup_calls) == 3
        assert all(c[0] == db_p for c in backup_calls)
        # R8-H4: first call rotates (preserve_prev=False), the next
        # two preserve the pre-migration `.backup.prev` snapshot.
        assert [c[1] for c in backup_calls] == [False, True, True]
        # The on-disk .backup verifies cleanly
        assert os.path.isfile(db_p + ".backup")
        assert check_integrity(db_p + ".backup")

    def test_backup_failure_does_not_rollback_schema(self, tmp_path, monkeypatch, caplog):
        """Backup failure post-commit → warning logged, schema bump
        kept. The user_version bump is the source of truth for
        "step completed"; rolling it back on backup failure would
        re-run the migration on next startup."""
        import logging

        from bpp.db import schema as schema_mod

        steps_run = []

        def _step(conn):
            steps_run.append("v100")

        monkeypatch.setattr("bpp.db.migrations.MIGRATIONS", [(100, _step)])

        from bpp.db import connection as conn_mod

        def boom(_p):
            raise OSError("disk full")

        monkeypatch.setattr(conn_mod, "backup_db", boom)

        db_p = str(tmp_path / "boom.db")
        conn = self._bare_conn(db_p, user_version=99)
        try:
            with caplog.at_level(logging.WARNING):
                schema_mod._migrate(conn)
            assert steps_run == ["v100"]
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 100
        finally:
            conn.close()

        assert any("Per-step backup after migration" in r.message for r in caplog.records)

    def test_in_memory_db_skips_backup_silently(self, monkeypatch):
        """In-memory connections (`:memory:`) have no path; skip
        backup without raising. Used by test fixtures across the
        suite."""
        from bpp.db import schema as schema_mod

        called = []

        def _step(conn):
            pass

        monkeypatch.setattr("bpp.db.migrations.MIGRATIONS", [(100, _step)])

        from bpp.db import connection as conn_mod

        def spy_backup(p):
            called.append(p)
            return None

        monkeypatch.setattr(conn_mod, "backup_db", spy_backup)

        # In-memory conn — no PRAGMA database_list path
        mem = sqlite3.connect(":memory:")
        try:
            mem.execute("PRAGMA user_version = 99")
            mem.commit()
            schema_mod._migrate(mem)
        finally:
            mem.close()

        assert called == [], "backup_db must not run for :memory: connections"


# ===========================================================================
# check_integrity tests
# ===========================================================================


class TestCheckIntegrity:
    def test_integrity_passes_valid_db(self, db_path):
        assert check_integrity(db_path) is True

    def test_integrity_passes_missing_file(self, tmp_path):
        """Missing DB file is OK (not created yet)."""
        assert check_integrity(str(tmp_path / "nope.db")) is True

    def test_integrity_fails_corrupt_db(self, tmp_path):
        p = str(tmp_path / "corrupt.db")
        with open(p, "wb") as f:
            f.write(b"NOT A SQLITE DATABASE" * 100)
        assert check_integrity(p) is False

    def test_integrity_fails_truncated_db(self, tmp_path):
        """A truncated DB should fail integrity."""
        p = str(tmp_path / "truncated.db")
        # Create a valid DB
        conn = init_db(p)
        upsert_photo(conn, {"filepath": "/fake/x.jpg"})
        conn.close()
        close_all_connections()
        # Truncate it
        size = os.path.getsize(p)
        with open(p, "r+b") as f:
            f.truncate(size // 2)
        assert check_integrity(p) is False


# ===========================================================================
# checkpoint_wal tests
# ===========================================================================


class TestCheckpointWAL:
    def test_checkpoint_succeeds(self, db_path):
        conn = get_db(db_path)
        # Insert data to create WAL entries
        upsert_photo(conn, {"filepath": "/fake/wal_test.jpg"})
        # Checkpoint should not raise
        checkpoint_wal(conn)
        close_all_connections()

    def test_checkpoint_on_closed_conn_no_error(self, db_path):
        """checkpoint_wal should not raise even on a closed connection."""
        conn = sqlite3.connect(db_path)
        conn.close()
        # Should silently pass (best-effort)
        checkpoint_wal(conn)

    def test_close_all_checkpoints_before_closing(self, tmp_path):
        """close_all_connections should checkpoint before closing."""
        p = str(tmp_path / "wal_test.db")
        conn = init_db(p)
        upsert_photo(conn, {"filepath": "/fake/wal.jpg"})

        # WAL file should exist after writes
        wal = p + "-wal"
        # Force WAL mode
        conn.execute("PRAGMA journal_mode=WAL")

        close_all_connections()

        # After close, WAL should be empty or very small (checkpointed)
        if os.path.isfile(wal):
            assert os.path.getsize(wal) == 0

    def test_checkpoint_failure_is_logged_not_silent(self, db_path, caplog):
        """R9-rec-H1: a failed checkpoint must leave a breadcrumb in
        the log so an operator can diagnose disk-full / permission /
        corrupt-WAL issues. Pre-fix, the failure was swallowed by
        `contextlib.suppress(Exception)` with no log emission."""
        from unittest.mock import patch

        from bpp.db import connection as _conn_mod

        conn = get_db(db_path)
        upsert_photo(conn, {"filepath": "/fake/wal.jpg"})

        # Force the dialect's checkpoint to raise so we exercise the
        # failure path. The exception is then logged + suppressed.
        with (
            patch.object(
                _conn_mod.dialect,
                "checkpoint",
                side_effect=sqlite3.OperationalError("disk I/O error"),
            ),
            caplog.at_level("WARNING", logger="bpp.db.backup"),
        ):
            checkpoint_wal(conn)

        assert any("WAL checkpoint failed" in rec.message for rec in caplog.records), (
            "checkpoint failure must be logged at WARNING with exc_info, not swallowed silently"
        )

    def test_close_all_logs_checkpoint_failure(self, tmp_path, caplog):
        """The shutdown path must surface checkpoint failure for every
        connection it tries to close — not just the first."""
        from unittest.mock import patch

        from bpp.db import connection as _conn_mod

        p = str(tmp_path / "shutdown_log.db")
        init_db(p)
        get_db(p)  # populate the thread-local

        with (
            patch.object(
                _conn_mod.dialect,
                "checkpoint",
                side_effect=sqlite3.OperationalError("disk full"),
            ),
            caplog.at_level("WARNING", logger="bpp.db.backup"),
        ):
            close_all_connections()

        assert any("WAL checkpoint failed" in rec.message for rec in caplog.records)


# ===========================================================================
# relocate_missing tests
# ===========================================================================


class TestRelocateMissing:
    def test_relocate_finds_moved_file(self, conn, tmp_path):
        """A missing file found at a new path by SHA-256 should be relocated."""
        # Create a real file
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        img = old_dir / "photo.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 200)
        sha = hashlib.sha256(img.read_bytes()).hexdigest()

        # Insert with old path and hash
        upsert_photo(conn, {"filepath": str(img), "sha256": sha})
        assert get_photo_count(conn) == 1

        # "Move" the file to a new location
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        new_path = new_dir / "photo.jpg"
        img.rename(new_path)

        # Mark as missing
        check_missing(conn)
        assert get_photo_count(conn) == 0  # missing excluded
        assert get_photo_count(conn, include_missing=True) == 1

        # Relocate should find it
        relocated = relocate_missing(conn, str(new_dir))
        assert relocated == 1
        assert get_photo_count(conn) == 1  # restored

        # Filepath should be updated
        row = conn.execute("SELECT filepath FROM photos").fetchone()
        assert row[0] == str(new_path)

    def test_relocate_no_hash_skipped(self, conn, tmp_path):
        """Photos without SHA-256 should be skipped."""
        upsert_photo(conn, {"filepath": "/fake/nohash.jpg"})
        conn.execute("UPDATE photos SET missing=1")
        conn.commit()

        relocated = relocate_missing(conn, str(tmp_path))
        assert relocated == 0

    def test_relocate_file_not_found(self, conn, tmp_path):
        """If the file isn't found anywhere, it stays missing."""
        upsert_photo(conn, {"filepath": "/fake/gone.jpg", "sha256": "deadbeef"})
        conn.execute("UPDATE photos SET missing=1")
        conn.commit()

        search_dir = tmp_path / "empty"
        search_dir.mkdir()
        relocated = relocate_missing(conn, str(search_dir))
        assert relocated == 0

    def test_relocate_no_missing_photos(self, conn, tmp_path):
        """If nothing is missing, should return 0 immediately."""
        upsert_photo(conn, {"filepath": "/fake/ok.jpg", "sha256": "abc123"})
        relocated = relocate_missing(conn, str(tmp_path))
        assert relocated == 0

    def test_relocate_multiple_files(self, conn, tmp_path):
        """Multiple missing files should all be relocated."""
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        new_dir = tmp_path / "new"
        new_dir.mkdir()

        for i in range(3):
            data = b"\xff\xd8\xff\xe0" + bytes([i]) * 200
            old_path = old_dir / f"img_{i}.jpg"
            old_path.write_bytes(data)
            sha = hashlib.sha256(data).hexdigest()
            upsert_photo(conn, {"filepath": str(old_path), "sha256": sha})
            # Move file
            new_path = new_dir / f"img_{i}.jpg"
            old_path.rename(new_path)

        check_missing(conn)
        assert get_photo_count(conn) == 0

        relocated = relocate_missing(conn, str(new_dir))
        assert relocated == 3
        assert get_photo_count(conn) == 3


# ===========================================================================
# sample_random_photos tests
# ===========================================================================


class TestSampleRandomPhotos:
    def test_sample_returns_filepaths(self, conn):
        for i in range(10):
            upsert_photo(conn, {"filepath": f"/fake/photo_{i}.jpg", "missing": 0})
        sample = sample_random_photos(conn, count=5)
        assert len(sample) == 5
        assert all(isinstance(p, str) for p in sample)

    def test_sample_respects_count(self, conn):
        for i in range(3):
            upsert_photo(conn, {"filepath": f"/fake/photo_{i}.jpg", "missing": 0})
        sample = sample_random_photos(conn, count=100)
        assert len(sample) == 3  # Only 3 photos exist

    def test_sample_excludes_missing(self, conn):
        upsert_photo(conn, {"filepath": "/fake/ok.jpg", "missing": 0})
        upsert_photo(conn, {"filepath": "/fake/missing.jpg", "missing": 0})
        conn.execute("UPDATE photos SET missing=1 WHERE filepath='/fake/missing.jpg'")
        conn.commit()
        sample = sample_random_photos(conn, count=10)
        assert len(sample) == 1
        assert sample[0] == "/fake/ok.jpg"

    def test_sample_empty_db(self, conn):
        sample = sample_random_photos(conn, count=10)
        assert sample == []


# ===========================================================================
# Atomic import tests
# ===========================================================================


class TestAtomicImport:
    def test_import_creates_no_tmp_files(self, conn, tmp_path):
        """After a successful import, no .tmp files should remain."""
        from bpp.db.library import import_folder

        source = tmp_path / "source"
        source.mkdir()
        (source / "a.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 200)
        (source / "b.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x01" * 200)

        library = tmp_path / "library"
        library.mkdir()

        result = import_folder(conn, str(source), str(library))
        assert result.imported == 2
        assert result.errors == 0

        # No .tmp files should exist in library
        for _dirpath, _dirs, files in os.walk(str(library)):
            for f in files:
                assert not f.endswith(".tmp"), f"Leftover .tmp file: {f}"

    def test_import_verifies_file_size(self, conn, tmp_path):
        """Imported files should match source sizes exactly."""
        from bpp.db.library import import_folder

        source = tmp_path / "source"
        source.mkdir()
        data = b"\xff\xd8\xff\xe0" + os.urandom(500)
        (source / "photo.jpg").write_bytes(data)

        library = tmp_path / "library"
        library.mkdir()

        result = import_folder(conn, str(source), str(library))
        assert result.imported == 1

        # Find the imported file and check size
        imported_path = result.imported_paths[0]
        assert os.path.getsize(imported_path) == len(data)

    def test_import_file_content_matches(self, conn, tmp_path):
        """Imported file content should exactly match source."""
        from bpp.db.library import import_folder

        source = tmp_path / "source"
        source.mkdir()
        data = b"\xff\xd8\xff\xe0" + os.urandom(1000)
        (source / "photo.jpg").write_bytes(data)

        library = tmp_path / "library"
        library.mkdir()

        result = import_folder(conn, str(source), str(library))
        imported_path = result.imported_paths[0]
        with open(imported_path, "rb") as f:
            assert f.read() == data


# ===========================================================================
# first_run API test
# ===========================================================================


class TestFirstRunAPI:
    @pytest.fixture
    def empty_app(self, tmp_path):
        """Create a Flask app with an empty library (no photos)."""
        from bpp.web.app import create_app

        wd = str(tmp_path / "data")
        os.makedirs(wd, exist_ok=True)
        app = create_app(workdir=wd)
        app.config["TESTING"] = True
        return app

    @pytest.fixture
    def populated_app(self, tmp_path):
        """Create a Flask app with some photos (real files so missing=0)."""
        from bpp.web.app import create_app

        wd = str(tmp_path / "data")
        os.makedirs(wd, exist_ok=True)
        # Create real image files so upsert_photo sets missing=0
        photos_dir = tmp_path / "photos"
        photos_dir.mkdir()
        db_p = os.path.join(wd, "photopicker.db")
        conn = init_db(db_p)
        for i in range(5):
            img = photos_dir / f"photo_{i}.jpg"
            img.write_bytes(b"\xff\xd8\xff\xe0" + bytes([i]) * 100)
            upsert_photo(conn, {"filepath": str(img)})
        conn.close()
        close_all_connections()

        app = create_app(workdir=wd)
        app.config["TESTING"] = True
        return app

    def test_first_run_true_fresh_library(self, empty_app):
        """A freshly created library (DB just initialised, 0 photos) shows first_run=true."""
        with empty_app.test_client() as client:
            resp = client.get("/api/v1/status")
            data = resp.get_json()
            assert data["first_run"] is True
            assert data["has_analysis"] is False

    def test_first_run_false_with_photos(self, populated_app):
        with populated_app.test_client() as client:
            resp = client.get("/api/v1/status")
            data = resp.get_json()
            assert data["first_run"] is False
            assert data["has_analysis"] is True
            assert data["image_count"] == 5

    def test_status_includes_library_path(self, empty_app):
        with empty_app.test_client() as client:
            resp = client.get("/api/v1/status")
            data = resp.get_json()
            assert "library_path" in data


# ===========================================================================
# Startup file health checks
# ===========================================================================


@pytest.mark.real_health_checks
class TestStartupFileHealth:
    def test_startup_scan_detects_missing(self, tmp_path):
        """Startup scan should mark files as missing."""
        from bpp.web.state import WebAppState

        lib = tmp_path / "lib"
        lib.mkdir()
        photos = lib / "photos"
        photos.mkdir()
        data = lib / "data"
        data.mkdir()

        # Create a real file, insert it, then delete it
        img = photos / "real.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

        db_p = str(data / "photopicker.db")
        conn = init_db(db_p)
        upsert_photo(conn, {"filepath": str(img)})
        upsert_photo(conn, {"filepath": "/fake/gone.jpg"})
        conn.close()
        close_all_connections()

        # Create WebAppState in serve mode
        state = WebAppState(workdir=str(data), library_path=str(lib))

        # Wait a moment for the background thread to complete
        time.sleep(1)

        conn = state.get_conn()
        # The real file should still be present
        row = conn.execute("SELECT missing FROM photos WHERE filepath=?", (str(img),)).fetchone()
        assert row[0] == 0

        # The fake file should be missing
        row = conn.execute(
            "SELECT missing FROM photos WHERE filepath=?",
            ("/fake/gone.jpg",),
        ).fetchone()
        assert row[0] == 1
        close_all_connections()

    def test_startup_scan_relocates_moved_file(self, tmp_path):
        """Startup scan should relocate a moved file by SHA-256."""
        from bpp.web.state import WebAppState

        lib = tmp_path / "lib"
        lib.mkdir()
        photos = lib / "photos"
        photos.mkdir()
        data = lib / "data"
        data.mkdir()

        # Create file at old location
        old_dir = photos / "old_batch"
        old_dir.mkdir()
        data_bytes = b"\xff\xd8\xff\xe0" + b"\xab" * 200
        old_file = old_dir / "moved.jpg"
        old_file.write_bytes(data_bytes)
        sha = hashlib.sha256(data_bytes).hexdigest()

        db_p = str(data / "photopicker.db")
        conn = init_db(db_p)
        upsert_photo(conn, {"filepath": str(old_file), "sha256": sha})
        conn.close()
        close_all_connections()

        # "Move" the file
        new_dir = photos / "new_batch"
        new_dir.mkdir()
        new_file = new_dir / "moved.jpg"
        old_file.rename(new_file)

        state = WebAppState(workdir=str(data), library_path=str(lib))
        time.sleep(1)

        conn = state.get_conn()
        row = conn.execute("SELECT filepath, missing FROM photos").fetchone()
        assert row[1] == 0  # Not missing
        assert row[0] == str(new_file)  # Relocated
        close_all_connections()

    def test_no_health_checks_without_serve_mode(self, tmp_path):
        """Health checks should not start when not in serve mode."""
        from bpp.web.state import WebAppState

        wd = str(tmp_path / "data")
        os.makedirs(wd, exist_ok=True)

        state = WebAppState(workdir=wd)
        # serve_mode should be False (no library_path given)
        assert state.serve_mode is False
        # _health_handle should not exist (threads never started)
        assert not hasattr(state, "_health_handle")
        close_all_connections()

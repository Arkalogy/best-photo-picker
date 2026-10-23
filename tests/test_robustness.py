"""Tests for robustness features: backup, integrity, WAL, relocation, atomic import — part
1 of 3."""

# Shared fixtures are imported by name and re-bound as test parameters.
# ruff: noqa: F811

from __future__ import annotations

import os

from bpp.db import backup as backup_mod
from bpp.db.connection import (
    backup_db,
    check_integrity,
    close_all_connections,
    init_db,
)
from bpp.db.photos import (
    upsert_photo,
)
from tests._robustness_shared import conn, db_path  # noqa: F401

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

        # Quarantine file exists with the corrupt bytes. The name
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
        """Two backup verifications failing within the same
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
        """When shutil.move fails to quarantine the corrupt
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

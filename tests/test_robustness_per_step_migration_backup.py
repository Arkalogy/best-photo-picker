"""Tests for robustness features: backup, integrity, WAL, relocation, atomic import — part 2 of 3:
TestPerStepMigrationBackup, TestCheckIntegrity, TestCheckpointWAL."""

# Shared fixtures are imported by name and re-bound as test parameters.
# ruff: noqa: F811

from __future__ import annotations

import hashlib
import os
import sqlite3

from bpp.db.connection import (
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
from tests._robustness_shared import conn, db_path  # noqa: F401

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
            # The rotation takes a `preserve_prev` kwarg; spy must accept
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
        # First call rotates (preserve_prev=False), the next
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
        """A failed checkpoint must leave a breadcrumb in
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

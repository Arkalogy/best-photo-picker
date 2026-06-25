"""Tests for behaviors that were previously untested.

Covers:
- backup_db() chmods .backup and .backup.prev to 0600
- restore-backup refuses valid-but-non-bpp SQLite (wrong schema)
- _personCardClick reads clusterId from element dataset when dispatcher path
"""

from __future__ import annotations

import os
import sqlite3
import sys

import pytest

# ── backup file permissions ──────────────────────────────────────────────────


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX permissions only")
class TestBackupFilePermissions:
    """backup_db() must chmod .backup and .backup.prev to 0600 so other
    local accounts can't read the cleartext share token out of backups."""

    def test_backup_file_is_0600(self, tmp_path):
        from bpp.db.connection import backup_db, close_all_connections, init_db

        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        backup_db(db_path)
        backup = db_path + ".backup"
        assert os.path.exists(backup)
        mode = os.stat(backup).st_mode & 0o777
        assert mode == 0o600, (
            f".backup file mode is {oct(mode)}, expected 0o600. Other local accounts can read it."
        )
        close_all_connections()

    def test_backup_prev_file_is_0600(self, tmp_path):
        from bpp.db.connection import backup_db, close_all_connections, init_db

        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        # First backup creates .backup
        backup_db(db_path)
        # Second backup rotates .backup → .backup.prev
        backup_db(db_path)
        prev = db_path + ".backup.prev"
        assert os.path.exists(prev)
        mode = os.stat(prev).st_mode & 0o777
        assert mode == 0o600, (
            f".backup.prev file mode is {oct(mode)}, expected 0o600. "
            "Rotated backups must also be owner-only."
        )
        close_all_connections()


# ── restore refuses non-bpp SQLite ───────────────────────────────────────────


class TestRestoreRefusesWrongSchema:
    """bpp db restore-backup must refuse a valid SQLite file that lacks
    the bpp schema (no 'photos' table). Without this guard, restoring
    an unrelated .db file silently overwrites the live library."""

    def test_refuses_empty_sqlite(self, tmp_path):

        from bpp.db.connection import close_all_connections, init_db

        # Create a real bpp DB to restore into
        db_path = str(tmp_path / "photopicker.db")
        init_db(db_path)
        close_all_connections()

        # Create a valid but non-bpp SQLite file
        bad_backup = str(tmp_path / "bad.db")
        conn = sqlite3.connect(bad_backup)
        conn.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()

        # Source-scan: verify the guard exists in the restore code

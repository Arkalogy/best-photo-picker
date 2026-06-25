"""R8-H14: critical state changes must leave a breadcrumb in
server.log.

The audit found three operations that mutated user-visible state
without any log evidence on the success path:

  1. `share.approve_device` / `share.revoke_device` — only the
     error path logged. A user reporting "I approved a device but
     it still says pending" had no signal whether the approve had
     committed at all.
  2. `db.schema._migrate` — multi-step migrations ran silently.
     A 6-step v23 -> v29 upgrade left server.log empty.
  3. `commands._do_restore_locked` — success was printed to stdout
     only. CLI scripts that pipe stdout to /dev/null had no
     server.log evidence the restore ran.

The fix is a small set of `log.info(...)` lines on the success
side. This test class locks the contract so future refactors
don't quietly remove them.
"""

from __future__ import annotations

import logging
import os
import sqlite3

import pytest


@pytest.fixture
def conn(tmp_path):
    """Minimal DB with the share_devices schema."""
    db_path = str(tmp_path / "share.db")
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE share_devices (
            id INTEGER PRIMARY KEY,
            fingerprint TEXT UNIQUE,
            ua TEXT,
            ip TEXT,
            first_seen INTEGER,
            last_seen INTEGER,
            trusted_at INTEGER,
            revoked_at INTEGER,
            prev_revoked INTEGER DEFAULT 0
        );
        """
    )
    c.commit()
    return c


def _seed_pending_device(conn, fingerprint: str = "fp-test"):
    cur = conn.execute(
        "INSERT INTO share_devices (fingerprint, ua, ip, first_seen, last_seen) "
        "VALUES (?, ?, ?, ?, ?)",
        (fingerprint, "TestAgent", "127.0.0.1", 1700000000, 1700000000),
    )
    conn.commit()
    return cur.lastrowid


class TestDeviceLifecycleLogging:
    def test_approve_logs_success(self, conn, caplog):
        from bpp.web.share import approve_device

        device_id = _seed_pending_device(conn)
        with caplog.at_level(logging.INFO, logger="bpp.web.share_devices"):
            ok = approve_device(conn, device_id)
        assert ok is True
        approved_logs = [
            r for r in caplog.records if "approved" in r.message and r.levelno == logging.INFO
        ]
        assert approved_logs, (
            f"approve_device must log on success. Got records: "
            f"{[(r.levelname, r.message) for r in caplog.records]}"
        )
        # Specific device_id present in the message
        assert str(device_id) in approved_logs[0].message

    def test_revoke_logs_success(self, conn, caplog):
        from bpp.web.share import approve_device, revoke_device

        device_id = _seed_pending_device(conn)
        approve_device(conn, device_id)  # need a trusted device to revoke

        with caplog.at_level(logging.INFO, logger="bpp.web.share_devices"):
            ok = revoke_device(conn, device_id)
        assert ok is True
        revoked_logs = [
            r for r in caplog.records if "revoked" in r.message and r.levelno == logging.INFO
        ]
        assert revoked_logs
        assert str(device_id) in revoked_logs[0].message

    def test_unknown_device_does_not_log_success(self, conn, caplog):
        """Inverse: when the device doesn't exist, the success log
        must NOT fire (the function returns False without changing
        state)."""
        from bpp.web.share import approve_device, revoke_device

        with caplog.at_level(logging.INFO, logger="bpp.web.share_devices"):
            assert approve_device(conn, 9999) is False
            assert revoke_device(conn, 9999) is False

        approved = [r for r in caplog.records if "approved" in r.message]
        revoked = [r for r in caplog.records if "revoked" in r.message]
        assert not approved
        assert not revoked


class TestMigrationLogging:
    def test_multistep_migration_logs_progress_and_completion(self, tmp_path, caplog):
        """`_migrate()` from a non-zero starting version must log a
        "v18 -> v29" line, a per-step success line, and a completion
        line. Together these let an operator confirm a multi-step
        upgrade happened cleanly without parsing the schema bumps."""
        from bpp.db.connection import close_all_connections, init_db

        # Reuse the v18-seed helper from the multi-step migration test
        from tests.test_migration_multi_step import _create_v18_db

        db_path = str(tmp_path / "test.db")
        _create_v18_db(db_path)

        with caplog.at_level(logging.INFO, logger="bpp.db.schema_migrate"):
            init_db(db_path)
        close_all_connections()

        msgs = [
            r.message
            for r in caplog.records
            if r.name in ("bpp.db.schema", "bpp.db.schema_migrate")
        ]
        # Header line: announces the upgrade range
        assert any("schema migration: v18 ->" in m for m in msgs), (
            f"Expected 'DB schema migration: v18 -> ...' header. Got: {msgs}"
        )
        # Per-step success lines: at least one
        assert any("Migration step v" in m and "committed" in m for m in msgs), (
            f"Expected 'Migration step v.. committed' lines. Got: {msgs}"
        )
        # Completion line
        assert any("schema migration complete" in m for m in msgs), (
            f"Expected 'DB schema migration complete' footer. Got: {msgs}"
        )

    def test_no_op_migration_does_not_spam_logs(self, tmp_path, caplog):
        """Inverse: when the DB is already at SCHEMA_VERSION, none
        of the new R8-H14 lines should fire — they're for upgrade
        events only."""
        from bpp.db.connection import close_all_connections, init_db

        db_path = str(tmp_path / "fresh.db")
        # First init brings a fresh DB to SCHEMA_VERSION; drain the
        # pool so the second init opens a fresh connection rather
        # than reusing the cached (now-stale) one.
        init_db(db_path)
        close_all_connections()

        # Clear records from the first init's migration run — only
        # records emitted by the SECOND init (the no-op) are what
        # this test asserts on. caplog accumulates within a test
        # function unless explicitly cleared.
        caplog.clear()

        # Second init is a no-op — log lines must be silent
        with caplog.at_level(logging.INFO, logger="bpp.db.schema_migrate"):
            init_db(db_path)
        close_all_connections()

        msgs = [
            r.message
            for r in caplog.records
            if r.name in ("bpp.db.schema", "bpp.db.schema_migrate")
        ]
        assert not any("schema migration: v" in m for m in msgs), (
            f"No-op migration must not log a header. Got: {msgs}"
        )
        assert not any("schema migration complete" in m for m in msgs), (
            f"No-op migration must not log completion. Got: {msgs}"
        )


class TestRestoreLogging:
    def test_successful_restore_logs_to_server_log(self, tmp_path, caplog):
        """`_do_restore_locked` succeeds → emits a `log.info` with
        the backup path + moved-aside list. Without this, scripts
        that pipe stdout to /dev/null lose all evidence the restore
        ran."""
        import argparse
        import shutil

        # Build a valid same-version backup so the restore proceeds
        # past the integrity + user_version guards.
        from bpp.commands import _do_restore_locked
        from bpp.db.connection import check_integrity, close_all_connections, init_db
        from bpp.db.schema import SCHEMA_VERSION

        db_path = str(tmp_path / "live.db")
        init_db(db_path)
        close_all_connections()  # drain pool so the file isn't held open

        backup_path = db_path + ".backup"
        shutil.copy2(db_path, backup_path)
        # Confirm user_version on the backup matches SCHEMA_VERSION
        # (init_db already set it on the live DB; the copy preserves it)
        c = sqlite3.connect(backup_path)
        try:
            uv = c.execute("PRAGMA user_version").fetchone()[0]
            assert uv == SCHEMA_VERSION
        finally:
            c.close()

        # Recent mtime so staleness guard doesn't refuse the restore
        os.utime(backup_path, None)

        with caplog.at_level(logging.INFO, logger="bpp.commands"):
            rc = _do_restore_locked(
                argparse.Namespace(
                    previous=False,
                    force=True,
                    yes=True,
                    accept_stale=False,
                ),
                db_path,
                backup_path,
                check_integrity,
            )

        assert rc == 0, f"Restore must succeed for the log assertion to be meaningful (got {rc})"
        # bpp.commands became a package in the v0.1 split; the restore
        # body now logs as `bpp.commands.db_restore`. Accept any
        # logger under the `bpp.commands` prefix so future submodule
        # moves don't break this assertion.
        msgs = [r.message for r in caplog.records if r.name.startswith("bpp.commands")]
        assert any("Database restored from" in m for m in msgs), (
            f"_do_restore_locked must emit a success log. Got: {msgs}"
        )

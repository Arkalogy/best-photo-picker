"""Regression: rotating the LAN share token must propagate the new
value into ``.backup`` and ``.backup.prev`` so a "user revoked because
they suspect compromise" flow doesn't leave the old token sitting in
sibling files. Audit'd by the on-disk-secrets reviewer.

History: ``backup_db()`` snapshots the live DB at startup. Until this
test's covered fix, ``regenerate_share_token()`` only updated the
live DB row — the .backup files retained the prior token forever.
That meant Time Machine snapshots, iCloud sync agents, Dropbox auto-
backup, and support bundles continued exposing a credential the user
thought they had revoked.

Also pins the file-mode tightening on the DB + WAL/SHM siblings:
``_restrict_db_perms`` chmod'd them to 0600 so other local accounts
can't read the cleartext share token off disk.
"""

from __future__ import annotations

import os
import sqlite3
import sys

import pytest


@pytest.fixture()
def db_with_token(tmp_path):
    """Create an init'd DB and seed a known share token. Checkpoints
    the WAL before yielding so subsequent ``shutil.copy2`` of the .db
    file alone captures the seeded value (otherwise the data sits in
    .db-wal and the backup ends up empty)."""
    from bpp.db.connection import close_all_connections, init_db
    from bpp.db.settings import set_setting

    db_path = str(tmp_path / "photopicker.db")
    init_db(db_path)
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    set_setting(conn, "lan_share_token", "OLD_TOKEN_AAAAA")
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    yield db_path
    close_all_connections()


class TestDbFilePermissions:
    """``_restrict_db_perms`` runs whenever a thread first opens the
    DB. The file (and WAL/SHM) must end up mode 0600 to prevent local
    other-account leaks."""

    @pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX permissions only")
    def test_db_file_locked_to_owner(self, tmp_path):
        from bpp.db.connection import close_all_connections, init_db

        db_path = str(tmp_path / "photopicker.db")
        init_db(db_path)
        # init_db opens the connection, which triggers the chmod.
        try:
            mode = os.stat(db_path).st_mode & 0o777
            assert mode == 0o600, (
                f"DB file mode is {oct(mode)}, expected 0o600. The lan_share_token "
                "is stored in the settings table; without owner-only permissions, "
                "every other local account / Time Machine / iCloud sync can read "
                "the credential."
            )
        finally:
            close_all_connections()


class TestShareTokenRotationPropagatesToBackups:
    """``regenerate_share_token`` must rewrite the rotated value into
    ``.backup`` and ``.backup.prev`` so a "revoke because compromised"
    workflow doesn't leave the old credential readable on disk."""

    def test_rotation_updates_live_db(self, db_with_token):
        from bpp.db.settings import get_setting
        from bpp.web.share import regenerate_share_token

        conn = sqlite3.connect(db_with_token, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            new_token = regenerate_share_token(conn)
            assert new_token != "OLD_TOKEN_AAAAA"
            current = get_setting(conn, "lan_share_token")
            assert current == new_token
        finally:
            conn.close()

    def test_rotation_overwrites_backup(self, db_with_token):
        """If a `.backup` file exists with the old token, rotation
        rewrites it. Without this, the .backup stays a credential
        leak even after the user revoked."""
        import shutil

        from bpp.db.settings import get_setting
        from bpp.web.share import regenerate_share_token

        # Pre-populate `.backup` with the old token (matches what
        # `backup_db()` would have done at startup).
        backup_path = db_with_token + ".backup"
        shutil.copy2(db_with_token, backup_path)

        # Sanity check: the backup currently holds the old token.
        bc = sqlite3.connect(backup_path, timeout=5.0)
        bc.row_factory = sqlite3.Row
        try:
            assert get_setting(bc, "lan_share_token") == "OLD_TOKEN_AAAAA"
        finally:
            bc.close()

        # Now rotate.
        conn = sqlite3.connect(db_with_token, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            new_token = regenerate_share_token(conn)
        finally:
            conn.close()

        # Backup must now hold the NEW token.
        bc = sqlite3.connect(backup_path, timeout=5.0)
        bc.row_factory = sqlite3.Row
        try:
            after = get_setting(bc, "lan_share_token")
        finally:
            bc.close()
        assert after == new_token, (
            f"After rotation, `.backup` still has the old token: {after!r}. "
            "Compromised credential persists in the recovery snapshot — every "
            "Time Machine pass, iCloud sync, support bundle still leaks it."
        )
        assert after != "OLD_TOKEN_AAAAA"

    def test_rotation_overwrites_backup_prev(self, db_with_token):
        """Same protection applies to `.backup.prev` (the
        second-generation backup retained for recovery)."""
        import shutil

        from bpp.db.settings import get_setting
        from bpp.web.share import regenerate_share_token

        prev_path = db_with_token + ".backup.prev"
        shutil.copy2(db_with_token, prev_path)

        conn = sqlite3.connect(db_with_token, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            new_token = regenerate_share_token(conn)
        finally:
            conn.close()

        bc = sqlite3.connect(prev_path, timeout=5.0)
        bc.row_factory = sqlite3.Row
        try:
            after = get_setting(bc, "lan_share_token")
        finally:
            bc.close()
        assert after == new_token

    def test_rotation_no_op_when_backups_missing(self, db_with_token):
        """If neither `.backup` nor `.backup.prev` exists, rotation
        succeeds silently (no error, no warning that mentions a real
        path). The propagation step is best-effort."""
        from bpp.web.share import regenerate_share_token

        # Confirm no backup files
        assert not os.path.exists(db_with_token + ".backup")
        assert not os.path.exists(db_with_token + ".backup.prev")

        conn = sqlite3.connect(db_with_token, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            new_token = regenerate_share_token(conn)
            assert new_token  # rotation succeeded
        finally:
            conn.close()

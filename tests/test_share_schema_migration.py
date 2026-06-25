"""Test the v27 → v28 migration path that adds share_devices.

A user upgrading from a previous bpp release will have a v27 DB on
disk. When the new server starts, _migrate must add the share_devices
table and bump user_version to 28 — without touching their existing
data (settings, photos, albums, etc.).

The fixture pattern: create a DB, force-set user_version to 27, drop
the (idempotently created) share_devices table to simulate "this was
made before v28 existed", then run _migrate again and assert the
table reappears with the right schema.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def v27_conn(tmp_path):
    """A DB simulated to be at user_version 27 (pre-share_devices)."""
    from bpp.db.connection import get_db, init_db

    db_path = str(tmp_path / "v27.db")
    init_db(db_path)  # creates everything at current SCHEMA_VERSION
    conn = get_db(db_path)

    # Roll back to v27: drop share_devices, reset user_version. This
    # simulates an existing on-disk DB from before v28.
    conn.execute("DROP TABLE IF EXISTS share_devices")
    conn.execute("PRAGMA user_version = 27")
    conn.commit()
    return conn


class TestMigrationToV28:
    def test_pre_migration_state(self, v27_conn):
        """Sanity check the fixture actually started at v27 with no
        share_devices table."""
        ver = v27_conn.execute("PRAGMA user_version").fetchone()[0]
        assert ver == 27
        tables = {
            r[0]
            for r in v27_conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert "share_devices" not in tables, (
            "fixture didn't drop share_devices — test setup broken"
        )

    def test_migrate_creates_share_devices(self, v27_conn):
        """Running _migrate on a v27 DB must create share_devices and
        advance user_version to 28."""
        from bpp.db.schema import _migrate

        _migrate(v27_conn)

        tables = {
            r[0]
            for r in v27_conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert "share_devices" in tables, "v28 migration must create share_devices table"
        ver = v27_conn.execute("PRAGMA user_version").fetchone()[0]
        assert ver >= 28, f"user_version stuck at {ver} after _migrate"

    def test_share_devices_has_forward_compat_columns(self, v27_conn):
        """Migrated table must include the user_id and scope_json
        columns that future user-account / per-album features depend on
        (see docs/security.md)."""
        from bpp.db.schema import _migrate

        _migrate(v27_conn)
        cols = {r[1] for r in v27_conn.execute("PRAGMA table_info(share_devices)").fetchall()}
        assert "fingerprint" in cols
        assert "trusted_at" in cols
        assert "revoked_at" in cols
        assert "prev_revoked" in cols
        assert "user_id" in cols, "forward-compat user_id column missing"
        assert "scope_json" in cols, "forward-compat scope_json column missing"

    def test_existing_data_preserved_through_migration(self, v27_conn):
        """The migration must not drop any existing data — settings,
        photos, etc. The simplest way to verify: write a settings row
        before migrating, then read it back after."""
        from bpp.db.schema import _migrate

        v27_conn.execute(
            "INSERT INTO settings (key, value) VALUES ('lan_share_token', 'pre-migration-token')"
        )
        v27_conn.commit()

        _migrate(v27_conn)

        row = v27_conn.execute(
            "SELECT value FROM settings WHERE key = 'lan_share_token'"
        ).fetchone()
        assert row is not None and row[0] == "pre-migration-token", (
            "v28 migration must not clobber existing settings"
        )

    def test_idempotent(self, v27_conn):
        """Running _migrate twice in a row must not fail or duplicate."""
        from bpp.db.schema import _migrate

        _migrate(v27_conn)
        _migrate(v27_conn)  # should be a no-op
        ver = v27_conn.execute("PRAGMA user_version").fetchone()[0]
        assert ver >= 28

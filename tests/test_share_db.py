"""Tests for the persistent LAN share state in DB settings.

These cover the DB layer in isolation — no Flask, no HTTP. The
contract:
- `get_share_token` always returns a string; auto-creates one on first
  call so callers don't need to special-case bootstrap.
- `regenerate_share_token` returns a fresh value; the next `get_…` call
  reflects it. The new token is different from the old (entropy check).
- LAN sharing flag persists across calls. Default is off.
- The share token is independent of `WebAppState.auth_token` (the app
  session token rotated on every server boot). Persistence is the
  whole point — share URLs need to survive restarts.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def conn(tmp_path):
    """File-backed DB initialized with full schema."""
    from bpp.db.connection import get_db, init_db

    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    c = get_db(db_path)
    yield c


class TestShareToken:
    def test_first_call_creates_token(self, conn):
        from bpp.web.share import get_share_token

        token = get_share_token(conn)
        assert isinstance(token, str)
        assert len(token) >= 32  # at least 128 bits hex-encoded

    def test_subsequent_calls_return_same_token(self, conn):
        from bpp.web.share import get_share_token

        first = get_share_token(conn)
        second = get_share_token(conn)
        assert first == second

    def test_persists_across_connections(self, conn):
        """Second connection to the same DB file sees the same token."""
        from bpp.db.connection import get_db
        from bpp.web.share import get_share_token

        first = get_share_token(conn)
        path = conn.execute("PRAGMA database_list").fetchone()["file"]
        c2 = get_db(path)
        second = get_share_token(c2)
        assert first == second

    def test_regenerate_returns_new_token(self, conn):
        from bpp.web.share import get_share_token, regenerate_share_token

        old = get_share_token(conn)
        new = regenerate_share_token(conn)
        assert new != old
        # And subsequent reads return the new one
        assert get_share_token(conn) == new


class TestLanSharingFlag:
    def test_default_is_disabled(self, conn):
        from bpp.web.share import is_lan_sharing_enabled

        assert is_lan_sharing_enabled(conn) is False

    def test_enable_persists(self, conn):
        from bpp.web.share import is_lan_sharing_enabled, set_lan_sharing_enabled

        set_lan_sharing_enabled(conn, True)
        assert is_lan_sharing_enabled(conn) is True

    def test_disable_persists(self, conn):
        from bpp.web.share import is_lan_sharing_enabled, set_lan_sharing_enabled

        set_lan_sharing_enabled(conn, True)
        set_lan_sharing_enabled(conn, False)
        assert is_lan_sharing_enabled(conn) is False

"""Tests for the trusted-devices DB layer (Tier 2 TOFU pairing).

Contract:
- A new fingerprint creates a *pending* device row (no trusted_at yet).
- Approving sets trusted_at; revoking sets revoked_at.
- A revoked fingerprint that reconnects flips back to pending but keeps
  a `prev_revoked` flag so the owner UI can warn ("previously revoked").
- Pending rows older than the TTL are auto-pruned to keep the table tidy.
- The helper layer is forward-compatible: a `user_id` column is present
  but NULL today, so a future user-account migration can backfill.
"""

from __future__ import annotations

import time

import pytest


@pytest.fixture
def conn(tmp_path):
    from bpp.db.connection import get_db, init_db

    db_path = str(tmp_path / "devices.db")
    init_db(db_path)
    return get_db(db_path)


# ─── Pending creation + idempotence ────────────────────────────────


class TestFindOrCreatePending:
    def test_first_call_creates_pending(self, conn):
        from bpp.web.share import find_or_create_pending_device

        d = find_or_create_pending_device(
            conn, fingerprint="fp-A", name="iPhone Safari", ip="192.168.1.5"
        )
        assert d["fingerprint"] == "fp-A"
        assert d["name"] == "iPhone Safari"
        assert d["ip_at_pair"] == "192.168.1.5"
        assert d["trusted_at"] is None
        assert d["revoked_at"] is None
        assert d["first_seen"] > 0
        assert d["last_seen"] >= d["first_seen"]

    def test_second_call_same_fingerprint_returns_existing(self, conn):
        from bpp.web.share import find_or_create_pending_device

        first = find_or_create_pending_device(
            conn, fingerprint="fp-A", name="iPhone", ip="192.168.1.5"
        )
        second = find_or_create_pending_device(
            conn, fingerprint="fp-A", name="iPhone", ip="192.168.1.5"
        )
        assert first["id"] == second["id"]

    def test_second_call_bumps_last_seen(self, conn):
        from bpp.web.share import find_or_create_pending_device

        first = find_or_create_pending_device(
            conn, fingerprint="fp-A", name="iPhone", ip="192.168.1.5"
        )
        time.sleep(0.02)
        second = find_or_create_pending_device(
            conn, fingerprint="fp-A", name="iPhone", ip="192.168.1.5"
        )
        assert second["last_seen"] >= first["last_seen"]

    def test_raises_loudly_on_insert_failure(self, conn):
        """If the INSERT silently fails (no rowid returned), we must
        raise a RuntimeError instead of letting None propagate to a
        caller that does `row['fingerprint']` and crashes confusingly.
        Defense in depth — this path isn't expected today, but a future
        schema bug or disk-full scenario should fail loudly."""
        from bpp.web.share import find_or_create_pending_device

        # Wrap the real connection so INSERT returns a fake cursor
        # with lastrowid=0 (SQLite's "no rowid" signal). Every other
        # call delegates to the real connection so the SELECT
        # pre-check still works correctly.
        class _FaultyConn:
            def __init__(self, real):
                self._real = real

            def execute(self, sql, *args, **kw):
                if "INSERT INTO share_devices" in sql:

                    class _NoRowid:
                        lastrowid = 0

                    return _NoRowid()
                return self._real.execute(sql, *args, **kw)

            def commit(self):
                self._real.commit()

        with pytest.raises(RuntimeError, match="no row id"):
            find_or_create_pending_device(_FaultyConn(conn), "fp-X", "iPhone", "192.168.1.5")


# ─── Approve / revoke state machine ────────────────────────────────


class TestApproveAndRevoke:
    def test_approve_sets_trusted_at(self, conn):
        from bpp.web.share import (
            approve_device,
            find_or_create_pending_device,
            get_device_by_fingerprint,
        )

        d = find_or_create_pending_device(conn, fingerprint="fp-A", name="iPhone", ip="192.168.1.5")
        approve_device(conn, d["id"])
        d2 = get_device_by_fingerprint(conn, "fp-A")
        assert d2["trusted_at"] is not None
        assert d2["revoked_at"] is None

    def test_is_device_trusted_pending(self, conn):
        from bpp.web.share import find_or_create_pending_device, is_device_trusted

        find_or_create_pending_device(conn, "fp-A", "iPhone", "192.168.1.5")
        assert is_device_trusted(conn, "fp-A") is False

    def test_is_device_trusted_after_approve(self, conn):
        from bpp.web.share import (
            approve_device,
            find_or_create_pending_device,
            is_device_trusted,
        )

        d = find_or_create_pending_device(conn, "fp-A", "iPhone", "192.168.1.5")
        approve_device(conn, d["id"])
        assert is_device_trusted(conn, "fp-A") is True

    def test_revoke_clears_trust(self, conn):
        from bpp.web.share import (
            approve_device,
            find_or_create_pending_device,
            is_device_trusted,
            revoke_device,
        )

        d = find_or_create_pending_device(conn, "fp-A", "iPhone", "192.168.1.5")
        approve_device(conn, d["id"])
        revoke_device(conn, d["id"])
        assert is_device_trusted(conn, "fp-A") is False


# ─── Re-pair after revoke ──────────────────────────────────────────


class TestRevokedReconnect:
    """A revoked device that reconnects with the same cookie stays
    revoked. Soft-revival is now an *explicit* user action (tap the
    'Request access again' button), not a side-effect of page loads.
    This prevents notification spam on the Mac when a phone with a
    stale cookie navigates to the URL."""

    def test_reconnect_after_revoke_stays_revoked(self, conn):
        from bpp.web.share import (
            approve_device,
            find_or_create_pending_device,
            is_device_trusted,
            revoke_device,
        )

        d = find_or_create_pending_device(conn, "fp-A", "iPhone", "192.168.1.5")
        approve_device(conn, d["id"])
        revoke_device(conn, d["id"])

        # Phone reconnects with same fingerprint — DB row stays revoked.
        # last_seen bumps so owner UI can still show recency, but the
        # device is NOT moved back to pending until user explicitly asks.
        d2 = find_or_create_pending_device(conn, "fp-A", "iPhone", "192.168.1.5")
        assert d2["id"] == d["id"]
        assert d2["revoked_at"] is not None, (
            "find_or_create_pending_device must NOT auto-revive revoked rows"
        )
        assert is_device_trusted(conn, "fp-A") is False


class TestApproveRevokeAtomic:
    """Concurrent approve + revoke on the same device must end in a
    single coherent state, never a half-applied row (e.g. trusted_at
    AND revoked_at both set, or both NULL after a revoke). Locked in
    at the helper level via BEGIN IMMEDIATE."""

    def test_helper_returns_false_for_unknown_id(self, conn):
        from bpp.web.share import approve_device, revoke_device

        assert approve_device(conn, 999_999) is False
        assert revoke_device(conn, 999_999) is False

    def test_helper_returns_true_on_success(self, conn):
        from bpp.web.share import (
            approve_device,
            find_or_create_pending_device,
            revoke_device,
        )

        d = find_or_create_pending_device(conn, "fp-A", "iPhone", "192.168.1.5")
        assert approve_device(conn, d["id"]) is True
        assert revoke_device(conn, d["id"]) is True

    def test_concurrent_approve_revoke_yields_consistent_state(self, tmp_path):
        """Two threads, two independent connections, race approve vs
        revoke on the same device. Whichever wins the IMMEDIATE lock
        commits first; the loser sees the post-commit state and writes
        on top. Final row is never both-set or both-NULL — it's whatever
        the second writer produced, atomically."""
        import threading

        from bpp.db.connection import get_db, init_db
        from bpp.web.share import (
            approve_device,
            find_or_create_pending_device,
            get_device_by_fingerprint,
            revoke_device,
        )

        db_path = str(tmp_path / "race.db")
        init_db(db_path)
        setup_conn = get_db(db_path)
        d = find_or_create_pending_device(setup_conn, "fp-A", "iPhone", "192.168.1.5")
        device_id = d["id"]

        # 50 trials to make a logic bug surface; 1 is enough to verify
        # the contract on a single sequencing.
        for _ in range(50):
            barrier = threading.Barrier(2)

            # Bind `barrier` via a default-arg so each iteration's
            # closure captures *its* barrier, not a shared one.
            def do_approve(barrier=barrier):
                conn = get_db(db_path)
                barrier.wait()
                approve_device(conn, device_id)

            def do_revoke(barrier=barrier):
                conn = get_db(db_path)
                barrier.wait()
                revoke_device(conn, device_id)

            t1 = threading.Thread(target=do_approve)
            t2 = threading.Thread(target=do_revoke)
            t1.start()
            t2.start()
            t1.join()
            t2.join()

            final = get_device_by_fingerprint(setup_conn, "fp-A")
            # The row must reflect exactly one of the two operations,
            # not a smear of both. Either trusted (approve won last) or
            # revoked (revoke won last) — never both, never neither.
            trusted = final["trusted_at"] is not None
            revoked = final["revoked_at"] is not None
            assert trusted ^ revoked, (
                f"Inconsistent post-race state: "
                f"trusted_at={final['trusted_at']}, revoked_at={final['revoked_at']}"
            )


class TestRequestAccess:
    """Explicit re-request flow — phone user taps 'Request access again'
    after a revoke. The DB call is `request_access(conn, fp)`."""

    def test_request_access_revives_revoked_row(self, conn):
        from bpp.web.share import (
            approve_device,
            find_or_create_pending_device,
            get_device_by_fingerprint,
            request_access,
            revoke_device,
        )

        d = find_or_create_pending_device(conn, "fp-A", "iPhone", "192.168.1.5")
        approve_device(conn, d["id"])
        revoke_device(conn, d["id"])

        result = request_access(conn, "fp-A")
        assert result is not None
        assert result["trusted_at"] is None
        assert result["revoked_at"] is None
        assert result["prev_revoked"] == 1  # sticky cue

        d_final = get_device_by_fingerprint(conn, "fp-A")
        assert d_final["trusted_at"] is None
        assert d_final["revoked_at"] is None

    def test_request_access_idempotent_on_pending(self, conn):
        from bpp.web.share import find_or_create_pending_device, request_access

        find_or_create_pending_device(conn, "fp-A", "iPhone", "192.168.1.5")
        result = request_access(conn, "fp-A")
        assert result is not None
        assert result["trusted_at"] is None
        assert result["revoked_at"] is None

    def test_request_access_no_demote_for_trusted(self, conn):
        """A trusted device calling /pair/request must not be demoted
        to pending — they're already approved. Idempotent no-op."""
        from bpp.web.share import (
            approve_device,
            find_or_create_pending_device,
            request_access,
        )

        d = find_or_create_pending_device(conn, "fp-A", "iPhone", "192.168.1.5")
        approve_device(conn, d["id"])
        result = request_access(conn, "fp-A")
        assert result is not None
        assert result["trusted_at"] is not None  # still trusted

    def test_request_access_unknown_fingerprint_returns_none(self, conn):
        from bpp.web.share import request_access

        assert request_access(conn, "fp-NOPE") is None

    def test_request_access_after_revoke_keeps_sticky_flag(self, conn):
        from bpp.web.share import (
            approve_device,
            find_or_create_pending_device,
            get_device_by_fingerprint,
            request_access,
            revoke_device,
        )

        d = find_or_create_pending_device(conn, "fp-A", "iPhone", "192.168.1.5")
        approve_device(conn, d["id"])
        revoke_device(conn, d["id"])
        request_access(conn, "fp-A")
        approve_device(conn, d["id"])
        d_final = get_device_by_fingerprint(conn, "fp-A")
        # Sticky flag persists across the full revoke → request → re-approve cycle.
        assert d_final["prev_revoked"] == 1


# ─── Pending TTL pruning ───────────────────────────────────────────


class TestPruneExpiredPending:
    def test_old_pending_removed(self, conn):
        from bpp.web.share import (
            find_or_create_pending_device,
            get_device_by_fingerprint,
            prune_expired_pending,
        )

        find_or_create_pending_device(conn, "fp-A", "iPhone", "192.168.1.5")
        # Antedate the row to 25h ago
        conn.execute(
            "UPDATE share_devices SET first_seen = ?, last_seen = ? WHERE fingerprint = ?",
            (int(time.time()) - 25 * 3600, int(time.time()) - 25 * 3600, "fp-A"),
        )
        conn.commit()
        prune_expired_pending(conn, ttl_seconds=24 * 3600)
        assert get_device_by_fingerprint(conn, "fp-A") is None

    def test_recent_pending_kept(self, conn):
        from bpp.web.share import (
            find_or_create_pending_device,
            get_device_by_fingerprint,
            prune_expired_pending,
        )

        find_or_create_pending_device(conn, "fp-A", "iPhone", "192.168.1.5")
        prune_expired_pending(conn, ttl_seconds=24 * 3600)
        assert get_device_by_fingerprint(conn, "fp-A") is not None

    def test_trusted_devices_never_pruned(self, conn):
        """Even if last_seen is ancient, a trusted device stays. That row
        is the user's intentional 'I trust this phone' decision."""
        from bpp.web.share import (
            approve_device,
            find_or_create_pending_device,
            get_device_by_fingerprint,
            prune_expired_pending,
        )

        d = find_or_create_pending_device(conn, "fp-A", "iPhone", "192.168.1.5")
        approve_device(conn, d["id"])
        conn.execute(
            "UPDATE share_devices SET last_seen = ? WHERE fingerprint = ?",
            (int(time.time()) - 365 * 86400, "fp-A"),
        )
        conn.commit()
        prune_expired_pending(conn, ttl_seconds=24 * 3600)
        assert get_device_by_fingerprint(conn, "fp-A") is not None


# ─── Listing ───────────────────────────────────────────────────────


class TestListDevices:
    def test_empty_initially(self, conn):
        from bpp.web.share import list_devices

        result = list_devices(conn)
        assert result == {"pending": [], "trusted": []}

    def test_separates_pending_and_trusted(self, conn):
        from bpp.web.share import (
            approve_device,
            find_or_create_pending_device,
            list_devices,
        )

        d1 = find_or_create_pending_device(conn, "fp-A", "iPhone", "192.168.1.5")
        find_or_create_pending_device(conn, "fp-B", "iPad", "192.168.1.6")
        approve_device(conn, d1["id"])

        result = list_devices(conn)
        assert len(result["trusted"]) == 1
        assert result["trusted"][0]["fingerprint"] == "fp-A"
        assert len(result["pending"]) == 1
        assert result["pending"][0]["fingerprint"] == "fp-B"

    def test_revoked_devices_omitted_unless_reconnecting(self, conn):
        """Once revoked, the device is no longer surfaced in either list
        until / unless the phone reconnects (which moves it back to pending)."""
        from bpp.web.share import (
            approve_device,
            find_or_create_pending_device,
            list_devices,
            revoke_device,
        )

        d = find_or_create_pending_device(conn, "fp-A", "iPhone", "192.168.1.5")
        approve_device(conn, d["id"])
        revoke_device(conn, d["id"])

        result = list_devices(conn)
        assert result == {"pending": [], "trusted": []}


# ─── Forward-compat: user_id column present ────────────────────────


class TestForwardCompat:
    def test_user_id_column_exists(self, conn):
        cols = {r[1] for r in conn.execute("PRAGMA table_info(share_devices)").fetchall()}
        assert "user_id" in cols, (
            "share_devices.user_id is required for future user accounts "
            "migration — see docs/security.md"
        )

    def test_scope_json_column_exists(self, conn):
        cols = {r[1] for r in conn.execute("PRAGMA table_info(share_devices)").fetchall()}
        assert "scope_json" in cols, (
            "share_devices.scope_json is required for future per-album "
            "sharing — see docs/security.md"
        )

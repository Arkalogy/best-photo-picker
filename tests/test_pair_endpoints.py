"""Tests for the pairing flow endpoints.

The phone, while it has a fingerprint cookie but isn't yet approved,
polls /api/share/pair/status. The Mac-side has buttons that POST to
/api/share/devices/<id>/approve and /revoke.

Contract:
- /api/share/pair/status: returns {state: pending|trusted|revoked|unknown}
  - "unknown" when there's no fingerprint cookie (or no row for it)
  - readable while pending (this is the one auth-free API path)
- /api/share/devices: list of pending + trusted (Mac UI consumes this)
- /api/share/devices/<id>/approve: 200 + updated state
- /api/share/devices/<id>/revoke: 200 + updated state
- Both mutating endpoints reject calls from un-trusted LAN clients
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture
def app(tmp_path):
    """App with TESTING=False and LAN sharing enabled."""
    from bpp.web.app import create_app
    from bpp.web.share import (
        _reset_pair_request_buckets_for_tests,
        set_lan_sharing_enabled,
    )

    # The pair-request rate limiter is process-wide; reset between tests
    # so a previous test's burst doesn't bleed into the next.
    _reset_pair_request_buckets_for_tests()

    workdir = str(tmp_path / "workdir")
    os.makedirs(workdir)
    app = create_app(workdir=workdir)
    app.config["TESTING"] = False
    ctx = app.extensions["bpp"]
    with app.app_context():
        set_lan_sharing_enabled(ctx.get_conn(), True)
    return app


# ─── /api/share/pair/status ─────────────────────────────────────────


class TestPairStatusEndpoint:
    def test_unknown_when_no_cookie(self, app):
        client = app.test_client()
        r = client.get(
            "/api/v1/share/pair/status",
            environ_overrides={"REMOTE_ADDR": "192.168.1.5"},
        )
        assert r.status_code == 200
        assert r.get_json()["state"] == "unknown"

    def test_pending_when_cookie_matches_pending_device(self, app):
        from bpp.web.share import find_or_create_pending_device

        ctx = app.extensions["bpp"]
        with app.app_context():
            find_or_create_pending_device(ctx.get_conn(), "fp-A", "iPhone", "192.168.1.5")

        client = app.test_client()
        client.set_cookie("bpp_share_fp", "fp-A")
        r = client.get(
            "/api/v1/share/pair/status",
            environ_overrides={"REMOTE_ADDR": "192.168.1.5"},
        )
        assert r.status_code == 200
        assert r.get_json()["state"] == "pending"

    def test_trusted_when_approved(self, app):
        from bpp.web.share import approve_device, find_or_create_pending_device

        ctx = app.extensions["bpp"]
        with app.app_context():
            d = find_or_create_pending_device(ctx.get_conn(), "fp-A", "iPhone", "192.168.1.5")
            approve_device(ctx.get_conn(), d["id"])

        client = app.test_client()
        client.set_cookie("bpp_share_fp", "fp-A")
        r = client.get(
            "/api/v1/share/pair/status",
            environ_overrides={"REMOTE_ADDR": "192.168.1.5"},
        )
        assert r.get_json()["state"] == "trusted"

    def test_revoked_when_revoked(self, app):
        from bpp.web.share import (
            approve_device,
            find_or_create_pending_device,
            revoke_device,
        )

        ctx = app.extensions["bpp"]
        with app.app_context():
            d = find_or_create_pending_device(ctx.get_conn(), "fp-A", "iPhone", "192.168.1.5")
            approve_device(ctx.get_conn(), d["id"])
            revoke_device(ctx.get_conn(), d["id"])

        client = app.test_client()
        client.set_cookie("bpp_share_fp", "fp-A")
        r = client.get(
            "/api/v1/share/pair/status",
            environ_overrides={"REMOTE_ADDR": "192.168.1.5"},
        )
        assert r.get_json()["state"] == "revoked"


# ─── /api/share/pair/request ────────────────────────────────────────


class TestPairRequestEndpoint:
    """Phone taps 'Request access again' on the revoked page → POST
    flips the row back to pending. Must be reachable for non-trusted
    LAN clients (allow-listed in authorize_request)."""

    def test_revoked_to_pending_via_request(self, app):
        from bpp.web.share import (
            approve_device,
            find_or_create_pending_device,
            get_device_by_fingerprint,
            revoke_device,
        )

        ctx = app.extensions["bpp"]
        with app.app_context():
            d = find_or_create_pending_device(ctx.get_conn(), "fp-A", "iPhone", "192.168.1.5")
            approve_device(ctx.get_conn(), d["id"])
            revoke_device(ctx.get_conn(), d["id"])

        client = app.test_client()
        client.set_cookie("bpp_share_fp", "fp-A")
        r = client.post(
            "/api/v1/share/pair/request",
            environ_overrides={"REMOTE_ADDR": "192.168.1.5"},
        )
        assert r.status_code == 200
        assert r.get_json()["state"] == "pending"

        with app.app_context():
            d_final = get_device_by_fingerprint(ctx.get_conn(), "fp-A")
        assert d_final["trusted_at"] is None
        assert d_final["revoked_at"] is None

    def test_request_without_cookie_returns_400(self, app):
        client = app.test_client()
        r = client.post(
            "/api/v1/share/pair/request",
            environ_overrides={"REMOTE_ADDR": "192.168.1.5"},
        )
        assert r.status_code == 400

    def test_request_unknown_fingerprint_returns_404(self, app):
        client = app.test_client()
        client.set_cookie("bpp_share_fp", "fp-UNKNOWN")
        r = client.post(
            "/api/v1/share/pair/request",
            environ_overrides={"REMOTE_ADDR": "192.168.1.5"},
        )
        assert r.status_code == 404

    def test_request_idempotent_on_pending(self, app):
        from bpp.web.share import find_or_create_pending_device

        ctx = app.extensions["bpp"]
        with app.app_context():
            find_or_create_pending_device(ctx.get_conn(), "fp-A", "iPhone", "192.168.1.5")

        client = app.test_client()
        client.set_cookie("bpp_share_fp", "fp-A")
        r1 = client.post(
            "/api/v1/share/pair/request",
            environ_overrides={"REMOTE_ADDR": "192.168.1.5"},
        )
        r2 = client.post(
            "/api/v1/share/pair/request",
            environ_overrides={"REMOTE_ADDR": "192.168.1.5"},
        )
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.get_json()["state"] == "pending"
        assert r2.get_json()["state"] == "pending"

    def test_rate_limit_blocks_after_burst(self, app):
        """11th request from the same IP within a second returns 429,
        defending the pending-requests list against a flood."""
        from bpp.web.share import (
            _reset_pair_request_buckets_for_tests,
            find_or_create_pending_device,
        )

        _reset_pair_request_buckets_for_tests()
        ctx = app.extensions["bpp"]
        with app.app_context():
            find_or_create_pending_device(ctx.get_conn(), "fp-A", "iPhone", "192.168.1.5")

        client = app.test_client()
        client.set_cookie("bpp_share_fp", "fp-A")
        # 10 succeed, 11th fails. Bucket capacity = 10.
        for i in range(10):
            r = client.post(
                "/api/v1/share/pair/request",
                environ_overrides={"REMOTE_ADDR": "192.168.1.5"},
            )
            assert r.status_code == 200, f"request {i + 1} should succeed"
        r = client.post(
            "/api/v1/share/pair/request",
            environ_overrides={"REMOTE_ADDR": "192.168.1.5"},
        )
        assert r.status_code == 429

    def test_rate_limit_independent_per_ip(self, app):
        """One spammy IP doesn't deny other LAN devices."""
        from bpp.web.share import (
            _reset_pair_request_buckets_for_tests,
            find_or_create_pending_device,
        )

        _reset_pair_request_buckets_for_tests()
        ctx = app.extensions["bpp"]
        with app.app_context():
            find_or_create_pending_device(ctx.get_conn(), "fp-A", "iPhone", "192.168.1.5")
            find_or_create_pending_device(ctx.get_conn(), "fp-B", "iPad", "192.168.1.6")

        client = app.test_client()
        client.set_cookie("bpp_share_fp", "fp-A")
        # Burn out IP 1's bucket
        for _ in range(10):
            client.post(
                "/api/v1/share/pair/request",
                environ_overrides={"REMOTE_ADDR": "192.168.1.5"},
            )
        r1 = client.post(
            "/api/v1/share/pair/request",
            environ_overrides={"REMOTE_ADDR": "192.168.1.5"},
        )
        assert r1.status_code == 429

        # Different IP still works
        client.set_cookie("bpp_share_fp", "fp-B")
        r2 = client.post(
            "/api/v1/share/pair/request",
            environ_overrides={"REMOTE_ADDR": "192.168.1.6"},
        )
        assert r2.status_code == 200

    def test_request_on_trusted_returns_trusted(self, app):
        """Already-approved device hitting the request endpoint → no-op,
        reports current state. Don't demote."""
        from bpp.web.share import approve_device, find_or_create_pending_device

        ctx = app.extensions["bpp"]
        with app.app_context():
            d = find_or_create_pending_device(ctx.get_conn(), "fp-A", "iPhone", "192.168.1.5")
            approve_device(ctx.get_conn(), d["id"])

        client = app.test_client()
        client.set_cookie("bpp_share_fp", "fp-A")
        r = client.post(
            "/api/v1/share/pair/request",
            environ_overrides={"REMOTE_ADDR": "192.168.1.5"},
        )
        assert r.status_code == 200
        assert r.get_json()["state"] == "trusted"


# ─── /api/share/devices (list) ──────────────────────────────────────


@pytest.fixture
def share_app(tmp_path):
    """TESTING=True app — used to test endpoints that require auth bypass."""
    from bpp.web.app import create_app
    from bpp.web.share import set_lan_sharing_enabled

    workdir = str(tmp_path / "workdir")
    os.makedirs(workdir)
    app = create_app(workdir=workdir)
    app.config["TESTING"] = True
    ctx = app.extensions["bpp"]
    with app.app_context():
        set_lan_sharing_enabled(ctx.get_conn(), True)
    return app


class TestDevicesListEndpoint:
    def test_lists_pending_and_trusted(self, share_app):
        from bpp.web.share import approve_device, find_or_create_pending_device

        ctx = share_app.extensions["bpp"]
        with share_app.app_context():
            d1 = find_or_create_pending_device(ctx.get_conn(), "fp-A", "iPhone", "192.168.1.5")
            find_or_create_pending_device(ctx.get_conn(), "fp-B", "iPad", "192.168.1.6")
            approve_device(ctx.get_conn(), d1["id"])

        r = share_app.test_client().get("/api/v1/share/devices")
        assert r.status_code == 200
        data = r.get_json()
        assert len(data["trusted"]) == 1
        assert data["trusted"][0]["fingerprint"] == "fp-A"
        assert len(data["pending"]) == 1
        assert data["pending"][0]["fingerprint"] == "fp-B"


# ─── /api/share/devices/<id>/approve & /revoke ──────────────────────


class TestDevicesMutationEndpoints:
    def test_approve_makes_device_trusted(self, share_app):
        from bpp.web.share import find_or_create_pending_device, is_device_trusted

        ctx = share_app.extensions["bpp"]
        with share_app.app_context():
            d = find_or_create_pending_device(ctx.get_conn(), "fp-A", "iPhone", "192.168.1.5")

        r = share_app.test_client().post(f"/api/v1/share/devices/{d['id']}/approve")
        assert r.status_code == 200

        with share_app.app_context():
            assert is_device_trusted(ctx.get_conn(), "fp-A") is True

    def test_revoke_kills_trusted_device(self, share_app):
        from bpp.web.share import (
            approve_device,
            find_or_create_pending_device,
            is_device_trusted,
        )

        ctx = share_app.extensions["bpp"]
        with share_app.app_context():
            d = find_or_create_pending_device(ctx.get_conn(), "fp-A", "iPhone", "192.168.1.5")
            approve_device(ctx.get_conn(), d["id"])

        r = share_app.test_client().post(f"/api/v1/share/devices/{d['id']}/revoke")
        assert r.status_code == 200

        with share_app.app_context():
            assert is_device_trusted(ctx.get_conn(), "fp-A") is False

    def test_approve_unknown_id_returns_404(self, share_app):
        r = share_app.test_client().post("/api/v1/share/devices/999999/approve")
        assert r.status_code == 404

    def test_revoke_unknown_id_returns_404(self, share_app):
        r = share_app.test_client().post("/api/v1/share/devices/999999/revoke")
        assert r.status_code == 404

    def test_lan_device_cannot_approve_other_devices(self, share_app):
        """Owner-only escalation guard: a paired LAN device must NOT be
        able to approve other pending devices (it would let one phone
        unilaterally trust other phones / TVs / future-self after a key
        rotation)."""
        from bpp.web.share import (
            approve_device,
            find_or_create_pending_device,
            get_share_token,
        )

        ctx = share_app.extensions["bpp"]
        with share_app.app_context():
            # Trusted LAN device A
            a = find_or_create_pending_device(ctx.get_conn(), "fp-A", "iPhone-A", "192.168.1.5")
            approve_device(ctx.get_conn(), a["id"])
            # Pending LAN device B
            b = find_or_create_pending_device(ctx.get_conn(), "fp-B", "iPhone-B", "192.168.1.6")
            share_token = get_share_token(ctx.get_conn())

        # Disable TESTING bypass so the real auth path runs and the
        # request is classified as LAN_DEVICE (not LOCAL_APP).
        share_app.config["TESTING"] = False
        try:
            client = share_app.test_client()
            client.set_cookie("bpp_share_fp", "fp-A", domain="localhost")
            r = client.post(
                f"/api/v1/share/devices/{b['id']}/approve",
                headers={"X-Auth-Token": share_token},
                environ_base={"REMOTE_ADDR": "192.168.1.5"},
            )
        finally:
            share_app.config["TESTING"] = True

        assert r.status_code == 403, (
            f"LAN device A approving device B must be 403, got {r.status_code}"
        )
        assert "owner-only" in r.get_json()["error"].lower()

    def test_lan_device_cannot_revoke_other_devices(self, share_app):
        """Same threat model as approve — a paired phone revoking
        siblings is a denial-of-service / lock-out vector."""
        from bpp.web.share import (
            approve_device,
            find_or_create_pending_device,
            get_share_token,
        )

        ctx = share_app.extensions["bpp"]
        with share_app.app_context():
            a = find_or_create_pending_device(ctx.get_conn(), "fp-A", "iPhone-A", "192.168.1.5")
            approve_device(ctx.get_conn(), a["id"])
            b = find_or_create_pending_device(ctx.get_conn(), "fp-B", "iPhone-B", "192.168.1.6")
            approve_device(ctx.get_conn(), b["id"])
            share_token = get_share_token(ctx.get_conn())

        share_app.config["TESTING"] = False
        try:
            client = share_app.test_client()
            client.set_cookie("bpp_share_fp", "fp-A", domain="localhost")
            r = client.post(
                f"/api/v1/share/devices/{b['id']}/revoke",
                headers={"X-Auth-Token": share_token},
                environ_base={"REMOTE_ADDR": "192.168.1.5"},
            )
        finally:
            share_app.config["TESTING"] = True

        assert r.status_code == 403


# ─── Index serves pair page when LAN client is untrusted ────────────


class TestIndexServesPairPage:
    def test_lan_untrusted_gets_pair_html(self, app):
        """Phone with fingerprint cookie but no approval lands on the
        pairing page, NOT the full SPA. Critical: the SPA shouldn't
        even download for an untrusted device — defense in depth."""
        from bpp.web.share import find_or_create_pending_device

        ctx = app.extensions["bpp"]
        with app.app_context():
            find_or_create_pending_device(ctx.get_conn(), "fp-A", "iPhone", "192.168.1.5")

        client = app.test_client()
        client.set_cookie("bpp_share_fp", "fp-A")
        r = client.get(
            "/",
            environ_overrides={"REMOTE_ADDR": "192.168.1.5"},
        )
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        # Pair page has a clear marker
        assert "pair-waiting" in body or "Waiting for owner" in body, (
            f"Expected pair page; got body starting with: {body[:200]}"
        )

    def test_lan_trusted_gets_full_app(self, app):
        from bpp.web.share import (
            approve_device,
            find_or_create_pending_device,
        )

        ctx = app.extensions["bpp"]
        with app.app_context():
            d = find_or_create_pending_device(ctx.get_conn(), "fp-A", "iPhone", "192.168.1.5")
            approve_device(ctx.get_conn(), d["id"])

        client = app.test_client()
        client.set_cookie("bpp_share_fp", "fp-A")
        r = client.get(
            "/",
            environ_overrides={"REMOTE_ADDR": "192.168.1.5"},
        )
        # Full SPA has the toolbar wrapper, sidebar, etc.
        body = r.get_data(as_text=True)
        assert "toolbar" in body

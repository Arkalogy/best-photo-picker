"""Tests for the extracted `authorize_request()` policy function.

The middleware in app.py used to inline all the auth logic. We split it
into a single decision function so:
- It's testable without spinning up Flask
- OSS contributors can swap it out (OAuth, JWT, LDAP) without touching
  the request-handling plumbing

Contract:
- Returns one of: ALLOW, DENY, PAIR_REQUIRED
- ALLOW: request can proceed
- DENY: 403, log it
- PAIR_REQUIRED: redirect to / so the phone hits the pairing page
  (covered in Phase 4); only happens for non-loopback requests with a
  valid share token but an untrusted fingerprint
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import pytest


@dataclass
class FakeRequest:
    """Minimal stand-in for a Flask request — only fields we use."""

    path: str = "/api/v1/status"
    remote_addr: str = "127.0.0.1"
    headers: dict[str, str] | None = None
    args: dict[str, str] | None = None
    cookies: dict[str, str] | None = None

    def __post_init__(self):
        self.headers = self.headers or {}
        self.args = self.args or {}
        self.cookies = self.cookies or {}


@pytest.fixture
def ctx(tmp_path):
    """A WebAppState-like context with the share helpers wired."""
    from bpp.web.app import create_app
    from bpp.web.share import set_lan_sharing_enabled

    workdir = str(tmp_path / "workdir")
    os.makedirs(workdir)
    app = create_app(workdir=workdir)
    c = app.extensions["bpp"]
    with app.app_context():
        set_lan_sharing_enabled(c.get_conn(), True)
    # Stash app for context-using tests
    c._test_app = app
    return c


def _authorize(ctx, req: FakeRequest):
    """Convenience wrapper: pushes app context for DB access."""
    from bpp.web.share import authorize_request

    with ctx._test_app.app_context():
        return authorize_request(req, ctx)


# ─── Loopback always allowed (Tauri / local browser) ────────────────


class TestLoopbackAllowed:
    def test_loopback_with_app_token_allowed(self, ctx):
        req = FakeRequest(
            path="/api/v1/status",
            remote_addr="127.0.0.1",
            headers={"X-Auth-Token": ctx.auth_token},
        )
        from bpp.web.share import AuthDecision

        assert _authorize(ctx, req).decision == AuthDecision.ALLOW

    def test_loopback_static_no_token_needed(self, ctx):
        from bpp.web.share import AuthDecision

        req = FakeRequest(path="/static/css/app.css", remote_addr="127.0.0.1")
        assert _authorize(ctx, req).decision == AuthDecision.ALLOW

    def test_loopback_index_no_token_needed(self, ctx):
        from bpp.web.share import AuthDecision

        req = FakeRequest(path="/", remote_addr="127.0.0.1")
        assert _authorize(ctx, req).decision == AuthDecision.ALLOW


# ─── LAN gate: when sharing is OFF, non-loopback always denied ──────


class TestLanGateOff:
    def test_lan_request_denied_when_sharing_off(self, ctx):
        from bpp.web.share import AuthDecision, set_lan_sharing_enabled

        with ctx._test_app.app_context():
            set_lan_sharing_enabled(ctx.get_conn(), False)
        req = FakeRequest(
            path="/api/v1/status",
            remote_addr="192.168.1.5",
            headers={"X-Auth-Token": ctx.auth_token},
        )
        assert _authorize(ctx, req).decision == AuthDecision.DENY


# ─── LAN with sharing ON: needs trusted device ──────────────────────


class TestLanRequiresTrustedDevice:
    def test_share_token_alone_is_not_enough(self, ctx):
        """The whole point of Tier 2: the URL token isn't sufficient.
        The owner must approve the device first."""
        from bpp.web.share import AuthDecision, get_share_token

        with ctx._test_app.app_context():
            share_token = get_share_token(ctx.get_conn())
        req = FakeRequest(
            path="/api/v1/status",
            remote_addr="192.168.1.5",
            headers={"X-Auth-Token": share_token},
            cookies={"bpp_share_fp": "fp-NEW"},
        )
        # No device record at all yet — definitely not trusted
        assert _authorize(ctx, req).decision == AuthDecision.PAIR_REQUIRED

    def test_pending_device_returns_pair_required(self, ctx):
        from bpp.web.share import (
            AuthDecision,
            find_or_create_pending_device,
            get_share_token,
        )

        with ctx._test_app.app_context():
            share_token = get_share_token(ctx.get_conn())
            find_or_create_pending_device(ctx.get_conn(), "fp-A", "iPhone", "192.168.1.5")
        req = FakeRequest(
            path="/api/v1/status",
            remote_addr="192.168.1.5",
            headers={"X-Auth-Token": share_token},
            cookies={"bpp_share_fp": "fp-A"},
        )
        assert _authorize(ctx, req).decision == AuthDecision.PAIR_REQUIRED

    def test_trusted_device_allowed(self, ctx):
        from bpp.web.share import (
            AuthDecision,
            approve_device,
            find_or_create_pending_device,
            get_share_token,
        )

        with ctx._test_app.app_context():
            share_token = get_share_token(ctx.get_conn())
            d = find_or_create_pending_device(ctx.get_conn(), "fp-A", "iPhone", "192.168.1.5")
            approve_device(ctx.get_conn(), d["id"])
        req = FakeRequest(
            path="/api/v1/status",
            remote_addr="192.168.1.5",
            headers={"X-Auth-Token": share_token},
            cookies={"bpp_share_fp": "fp-A"},
        )
        assert _authorize(ctx, req).decision == AuthDecision.ALLOW

    def test_revoked_device_denied(self, ctx):
        from bpp.web.share import (
            AuthDecision,
            approve_device,
            find_or_create_pending_device,
            get_share_token,
            revoke_device,
        )

        with ctx._test_app.app_context():
            share_token = get_share_token(ctx.get_conn())
            d = find_or_create_pending_device(ctx.get_conn(), "fp-A", "iPhone", "192.168.1.5")
            approve_device(ctx.get_conn(), d["id"])
            revoke_device(ctx.get_conn(), d["id"])
        req = FakeRequest(
            path="/api/v1/status",
            remote_addr="192.168.1.5",
            headers={"X-Auth-Token": share_token},
            cookies={"bpp_share_fp": "fp-A"},
        )
        # Revoked → not allowed; treated as DENY (not pair_required)
        # because the user explicitly said no
        assert _authorize(ctx, req).decision == AuthDecision.DENY

    def test_pair_polling_endpoint_allowed_during_pending(self, ctx):
        """Phone needs to poll /api/share/pair/status while waiting —
        otherwise it can't see the approval. Allow this one path even
        when the device isn't trusted yet."""
        from bpp.web.share import AuthDecision, find_or_create_pending_device

        with ctx._test_app.app_context():
            find_or_create_pending_device(ctx.get_conn(), "fp-A", "iPhone", "192.168.1.5")
        req = FakeRequest(
            path="/api/v1/share/pair/status",
            remote_addr="192.168.1.5",
            cookies={"bpp_share_fp": "fp-A"},
        )
        assert _authorize(ctx, req).decision == AuthDecision.ALLOW

    def test_pair_request_endpoint_allowed_for_revoked_device(self, ctx):
        """Phone in revoked state needs to call /api/share/pair/request
        to ask for re-approval. Mirrors the status-path allow-list."""
        from bpp.web.share import (
            AuthDecision,
            approve_device,
            find_or_create_pending_device,
            revoke_device,
        )

        with ctx._test_app.app_context():
            d = find_or_create_pending_device(ctx.get_conn(), "fp-A", "iPhone", "192.168.1.5")
            approve_device(ctx.get_conn(), d["id"])
            revoke_device(ctx.get_conn(), d["id"])
        req = FakeRequest(
            path="/api/v1/share/pair/request",
            remote_addr="192.168.1.5",
            cookies={"bpp_share_fp": "fp-A"},
        )
        assert _authorize(ctx, req).decision == AuthDecision.ALLOW

    def test_index_allowed_for_lan_so_phone_can_load_pair_page(self, ctx):
        from bpp.web.share import AuthDecision

        req = FakeRequest(
            path="/",
            remote_addr="192.168.1.5",
            cookies={"bpp_share_fp": "fp-A"},
        )
        assert _authorize(ctx, req).decision == AuthDecision.ALLOW


# ─── Token mismatch ─────────────────────────────────────────────────


class TestTokenMismatch:
    def test_wrong_token_denied(self, ctx):
        from bpp.web.share import AuthDecision

        req = FakeRequest(
            path="/api/v1/status",
            remote_addr="192.168.1.5",
            headers={"X-Auth-Token": "wrong"},
            cookies={"bpp_share_fp": "fp-A"},
        )
        assert _authorize(ctx, req).decision == AuthDecision.DENY


# ─── Principal identity (the seam for future multi-user evolution) ──


class TestPrincipalIdentity:
    """authorize_request returns an AuthResult with a Principal naming
    *who* authenticated. Today there's one user, but the dataclass shape
    is the seam future OAuth / per-user / API-key flows slot into."""

    def test_loopback_app_token_is_local_app(self, ctx):
        from bpp.web.share import PRINCIPAL_LOCAL_APP, AuthDecision

        req = FakeRequest(
            path="/api/v1/status",
            remote_addr="127.0.0.1",
            headers={"X-Auth-Token": ctx.auth_token},
        )
        result = _authorize(ctx, req)
        assert result.decision == AuthDecision.ALLOW
        assert result.principal is not None
        assert result.principal.kind == PRINCIPAL_LOCAL_APP

    def test_lan_with_trusted_device_is_lan_device(self, ctx):
        from bpp.web.share import (
            PRINCIPAL_LAN_DEVICE,
            AuthDecision,
            approve_device,
            find_or_create_pending_device,
            get_share_token,
        )

        with ctx._test_app.app_context():
            share_token = get_share_token(ctx.get_conn())
            d = find_or_create_pending_device(ctx.get_conn(), "fp-A", "iPhone", "192.168.1.5")
            approve_device(ctx.get_conn(), d["id"])

        req = FakeRequest(
            path="/api/v1/status",
            remote_addr="192.168.1.5",
            headers={"X-Auth-Token": share_token},
            cookies={"bpp_share_fp": "fp-A"},
        )
        result = _authorize(ctx, req)
        assert result.decision == AuthDecision.ALLOW
        assert result.principal is not None
        assert result.principal.kind == PRINCIPAL_LAN_DEVICE
        # The fingerprint travels on the principal — used for audit
        # logging, future per-device scopes, etc.
        assert result.principal.fingerprint == "fp-A"

    def test_static_path_is_anonymous(self, ctx):
        from bpp.web.share import PRINCIPAL_ANONYMOUS, AuthDecision

        req = FakeRequest(path="/static/css/app.css", remote_addr="127.0.0.1")
        result = _authorize(ctx, req)
        assert result.decision == AuthDecision.ALLOW
        assert result.principal is not None
        assert result.principal.kind == PRINCIPAL_ANONYMOUS

    def test_pair_status_path_is_anonymous(self, ctx):
        from bpp.web.share import PRINCIPAL_ANONYMOUS, AuthDecision

        req = FakeRequest(
            path="/api/v1/share/pair/status",
            remote_addr="192.168.1.5",
        )
        result = _authorize(ctx, req)
        assert result.decision == AuthDecision.ALLOW
        assert result.principal is not None
        assert result.principal.kind == PRINCIPAL_ANONYMOUS

    def test_share_token_from_loopback_treated_as_local_app(self, ctx):
        """The loopback escape hatch lets a local dev test the share
        flow without a phone. The principal kind reflects who's actually
        on the line — local dev machine — not 'lan_device'."""
        from bpp.web.share import (
            PRINCIPAL_LOCAL_APP,
            AuthDecision,
            get_share_token,
        )

        with ctx._test_app.app_context():
            share_token = get_share_token(ctx.get_conn())

        req = FakeRequest(
            path="/api/v1/status",
            remote_addr="127.0.0.1",
            headers={"X-Auth-Token": share_token},
        )
        result = _authorize(ctx, req)
        assert result.decision == AuthDecision.ALLOW
        assert result.principal is not None
        assert result.principal.kind == PRINCIPAL_LOCAL_APP

    def test_deny_results_have_no_principal(self, ctx):
        from bpp.web.share import AuthDecision

        req = FakeRequest(
            path="/api/v1/status",
            remote_addr="192.168.1.5",
            headers={"X-Auth-Token": "wrong"},
        )
        result = _authorize(ctx, req)
        assert result.decision == AuthDecision.DENY
        assert result.principal is None

    def test_pair_required_results_have_no_principal(self, ctx):
        from bpp.web.share import AuthDecision, get_share_token

        with ctx._test_app.app_context():
            share_token = get_share_token(ctx.get_conn())

        req = FakeRequest(
            path="/api/v1/status",
            remote_addr="192.168.1.5",
            headers={"X-Auth-Token": share_token},
        )
        result = _authorize(ctx, req)
        assert result.decision == AuthDecision.PAIR_REQUIRED
        assert result.principal is None

    def test_principal_reserves_user_id_for_future_multi_user(self):
        """The Principal dataclass MUST keep `user_id` and `scopes`
        fields available — that's the v2-multi-user contract this seam
        exists to enable. Removing them is a breaking change."""
        from dataclasses import fields

        from bpp.web.share import Principal

        names = {f.name for f in fields(Principal)}
        assert "user_id" in names, (
            "Principal.user_id is reserved for future multi-user — see "
            "docs/security.md. Don't remove it."
        )
        assert "scopes" in names, (
            "Principal.scopes is reserved for future fine-grained perms — "
            "see docs/security.md. Don't remove it."
        )


# ─── Media routes (/thumb, /photo, /video) — same gate as /api ──────


class TestMediaPathAuth:
    """`/thumb/<h>`, `/photo/<h>`, `/video/<h>` serve raw bytes from the
    library. They must be gated identically to `/api/*` — token + (for
    LAN) a paired/trusted fingerprint. Without this gate, any LAN client
    that learns or guesses a `path_hash` reads everyone's photos.
    """

    @pytest.mark.parametrize("prefix", ["/thumb/", "/photo/", "/video/"])
    def test_lan_no_token_denied(self, ctx, prefix):
        from bpp.web.share import AuthDecision

        req = FakeRequest(
            path=f"{prefix}abc123",
            remote_addr="192.168.1.5",
        )
        assert _authorize(ctx, req).decision == AuthDecision.DENY

    @pytest.mark.parametrize("prefix", ["/thumb/", "/photo/", "/video/"])
    def test_lan_share_token_no_fingerprint_pair_required(self, ctx, prefix):
        from bpp.web.share import AuthDecision, get_share_token

        with ctx._test_app.app_context():
            share_token = get_share_token(ctx.get_conn())
        req = FakeRequest(
            path=f"{prefix}abc123",
            remote_addr="192.168.1.5",
            args={"_token": share_token},
        )
        assert _authorize(ctx, req).decision == AuthDecision.PAIR_REQUIRED

    @pytest.mark.parametrize("prefix", ["/thumb/", "/photo/", "/video/"])
    def test_lan_paired_trusted_allowed(self, ctx, prefix):
        from bpp.web.share import (
            PRINCIPAL_LAN_DEVICE,
            AuthDecision,
            approve_device,
            find_or_create_pending_device,
            get_share_token,
        )

        with ctx._test_app.app_context():
            share_token = get_share_token(ctx.get_conn())
            d = find_or_create_pending_device(ctx.get_conn(), "fp-A", "iPhone", "192.168.1.5")
            approve_device(ctx.get_conn(), d["id"])
        req = FakeRequest(
            path=f"{prefix}abc123",
            remote_addr="192.168.1.5",
            args={"_token": share_token},
            cookies={"bpp_share_fp": "fp-A"},
        )
        result = _authorize(ctx, req)
        assert result.decision == AuthDecision.ALLOW
        assert result.principal is not None
        assert result.principal.kind == PRINCIPAL_LAN_DEVICE

    @pytest.mark.parametrize("prefix", ["/thumb/", "/photo/", "/video/"])
    def test_lan_paired_revoked_denied(self, ctx, prefix):
        from bpp.web.share import (
            AuthDecision,
            approve_device,
            find_or_create_pending_device,
            get_share_token,
            revoke_device,
        )

        with ctx._test_app.app_context():
            share_token = get_share_token(ctx.get_conn())
            d = find_or_create_pending_device(ctx.get_conn(), "fp-A", "iPhone", "192.168.1.5")
            approve_device(ctx.get_conn(), d["id"])
            revoke_device(ctx.get_conn(), d["id"])
        req = FakeRequest(
            path=f"{prefix}abc123",
            remote_addr="192.168.1.5",
            args={"_token": share_token},
            cookies={"bpp_share_fp": "fp-A"},
        )
        assert _authorize(ctx, req).decision == AuthDecision.DENY

    @pytest.mark.parametrize("prefix", ["/thumb/", "/photo/", "/video/"])
    def test_loopback_with_app_token_allowed(self, ctx, prefix):
        from bpp.web.share import PRINCIPAL_LOCAL_APP, AuthDecision

        req = FakeRequest(
            path=f"{prefix}abc123",
            remote_addr="127.0.0.1",
            args={"_token": ctx.auth_token},
        )
        result = _authorize(ctx, req)
        assert result.decision == AuthDecision.ALLOW
        assert result.principal is not None
        assert result.principal.kind == PRINCIPAL_LOCAL_APP

    @pytest.mark.parametrize("prefix", ["/thumb/", "/photo/", "/video/"])
    def test_loopback_no_token_denied(self, ctx, prefix):
        """Regression: loopback without a token used to be ALLOW
        (anonymous bucket). After the gate extension, raw media on
        loopback also requires the app token. Defends against a malicious
        local app on the host scraping localhost:5001."""
        from bpp.web.share import AuthDecision

        req = FakeRequest(
            path=f"{prefix}abc123",
            remote_addr="127.0.0.1",
        )
        assert _authorize(ctx, req).decision == AuthDecision.DENY


# ─── R6-L2: constant-time token comparison ──────────────────────────


class TestConstantTimeTokenCompare:
    """The auth path must compare tokens through hmac.compare_digest
    so a timing oracle on the LAN can't probe the token byte-by-byte.
    With 256-bit secrets this is mostly defense-in-depth, but free
    to enforce — and removes a primitive-level review flag."""

    def test_authorize_request_uses_compare_digest(self, ctx, monkeypatch):
        """Sentinel: monkeypatch hmac.compare_digest in the share
        module's namespace and confirm authorize_request() invokes it
        on the candidate token."""
        from bpp.web import share
        from bpp.web.share import AuthDecision

        calls: list[tuple[str, str]] = []

        def _spy(a, b):
            calls.append((a, b))
            return a == b

        monkeypatch.setattr(share.hmac, "compare_digest", _spy)

        req = FakeRequest(
            path="/api/v1/status",
            remote_addr="127.0.0.1",
            headers={"X-Auth-Token": ctx.auth_token},
        )
        result = _authorize(ctx, req)
        assert result.decision == AuthDecision.ALLOW
        assert calls, "hmac.compare_digest was not consulted on the auth path"
        # Both sides should be the candidate token + a configured token,
        # never one empty side (which would short-circuit and skip
        # constant-time semantics).
        for cand, expected in calls:
            assert cand, "compare_digest called with empty candidate"
            assert expected, "compare_digest called with empty expected"

    def test_empty_token_does_not_auth_when_no_secret_configured(self, ctx, monkeypatch):
        """Edge case the truthy-guard in `_token_equals` defends
        against: if the configured app/share token were ever falsy
        (None / ""), a request with an empty token must NOT pass —
        otherwise both sides match and the principal would auth as
        LOCAL_APP without a real secret on either side."""
        from bpp.web import share
        from bpp.web.share import AuthDecision

        # Stub both expected tokens to empty so a candidate "" would
        # equal the expected "" under naive == compare. The fix's
        # truthy guard makes _token_equals refuse the empty-vs-empty
        # match, so the decision is DENY.
        monkeypatch.setattr(ctx, "auth_token", "")
        monkeypatch.setattr(share, "get_share_token", lambda conn: "")

        req = FakeRequest(
            path="/api/v1/status",
            remote_addr="127.0.0.1",
            headers={"X-Auth-Token": ""},
        )
        assert _authorize(ctx, req).decision == AuthDecision.DENY

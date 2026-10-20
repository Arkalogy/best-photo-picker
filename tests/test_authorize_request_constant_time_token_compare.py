"""Tests for the extracted `authorize_request()` policy function — part 2 of 2:
TestConstantTimeTokenCompare."""

# Shared fixtures are imported by name and re-bound as test parameters.
# ruff: noqa: F811

from __future__ import annotations

from tests._authorize_request_shared import (
    FakeRequest,
    _authorize,
    ctx,  # noqa: F401
)

# ─── Constant-time token comparison ──────────────────────────


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

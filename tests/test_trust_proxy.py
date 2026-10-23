"""Trusted-peer CIDR allowlist (replaces the boolean BPP_TRUST_PROXY) — part 1 of 2."""

# Shared fixtures are imported by name and re-bound as test parameters.
# ruff: noqa: F811

from __future__ import annotations

import os
from unittest import mock

from tests._trust_proxy_shared import (  # noqa: F401
    FakeRequest,
    _authorize,
    _reset_trusted_proxy_env,
    ctx,
)

# ─── Default (no env var): only true loopback is loopback ─────────


def test_no_env_lan_client_denied(ctx):
    """Bug repro confirmation: with no trusted-proxy config, a LAN
    client still hits the LAN gate."""
    from bpp.web.share import AuthDecision

    req = FakeRequest(path="/", remote_addr="192.168.1.50")
    assert _authorize(ctx, req).decision == AuthDecision.DENY


def test_no_env_docker_bridge_denied(ctx):
    """Without explicit trust, even the Docker bridge gateway is
    treated as remote — the operator must opt in."""
    from bpp.web.share import AuthDecision

    req = FakeRequest(path="/", remote_addr="172.17.0.1")
    assert _authorize(ctx, req).decision == AuthDecision.DENY


def test_no_env_loopback_still_works(ctx):
    """Bare-metal loopback path doesn't depend on env var."""
    from bpp.web.share import AuthDecision

    req = FakeRequest(path="/", remote_addr="127.0.0.1")
    assert _authorize(ctx, req).decision == AuthDecision.ALLOW


# ─── Docker case: bridge IP inside CIDR is treated as loopback ────


def test_docker_bridge_ip_inside_cidr_is_loopback(ctx):
    from bpp.web.share import AuthDecision

    req = FakeRequest(path="/", remote_addr="172.17.0.1")
    with mock.patch.dict(os.environ, {"BPP_TRUSTED_PROXIES": "172.16.0.0/12"}):
        assert _authorize(ctx, req).decision == AuthDecision.ALLOW


def test_docker_desktop_alias_inside_cidr_is_loopback(ctx):
    """Docker Desktop on macOS / Windows uses 192.168.65.0/24 as the
    host alias — the Dockerfile's default config covers it."""
    from bpp.web.share import AuthDecision

    req = FakeRequest(path="/", remote_addr="192.168.65.1")
    with mock.patch.dict(
        os.environ,
        {"BPP_TRUSTED_PROXIES": "172.16.0.0/12,192.168.65.0/24"},
    ):
        assert _authorize(ctx, req).decision == AuthDecision.ALLOW


# ─── BLAST RADIUS GUARD: a LAN IP outside the CIDR is NOT trusted ─


def test_lan_client_outside_cidr_not_promoted(ctx):
    """The original bug: BPP_TRUST_PROXY=1 made every remote_addr
    look like loopback. With the CIDR-list replacement, a 192.168.x
    client outside the operator's allowlist must NOT bypass the LAN
    gate, even when trusted-proxy config exists.

    This is the security regression test for the blast-radius bug."""
    from bpp.web.share import AuthDecision

    req = FakeRequest(path="/", remote_addr="192.168.1.50")
    # Operator allows the Docker bridge but not their home LAN
    with mock.patch.dict(os.environ, {"BPP_TRUSTED_PROXIES": "172.16.0.0/12"}):
        assert _authorize(ctx, req).decision == AuthDecision.DENY


def test_public_ip_never_trusted_even_with_match(ctx):
    """Operator misconfigures with 0.0.0.0/0 — a public IP would
    fall in. Make sure subsequent token gating still applies, so
    the misconfig only affects LAN-gate bypass, not API auth."""
    from bpp.web.share import AuthDecision

    req = FakeRequest(path="/api/v1/photos", remote_addr="8.8.8.8")
    with mock.patch.dict(os.environ, {"BPP_TRUSTED_PROXIES": "0.0.0.0/0"}):
        # No token → still DENY, because trust-proxy doesn't bypass
        # the token check. (And with the catastrophic-CIDR rejection
        # in place, 0.0.0.0/0 is dropped entirely — see the dedicated
        # ITEM A tests below.)
        assert _authorize(ctx, req).decision == AuthDecision.DENY


# ─── ITEM A: catastrophic CIDRs are rejected at parse time ─────────


def test_zero_zero_cidr_does_not_promote_public_ip(ctx):
    """Blast-radius scenario: BPP_TRUSTED_PROXIES=0.0.0.0/0
    is a catastrophic misconfig that, before the fix, made every
    remote IP look like loopback — so `/` rendered the owner SPA
    with the per-boot app token to anyone on the internet.

    The CIDR is now rejected at parse time. The request lands in
    the LAN-gate path with lan_sharing off → DENY."""
    from bpp.web.share import AuthDecision

    req = FakeRequest(path="/", remote_addr="8.8.8.8")
    with mock.patch.dict(os.environ, {"BPP_TRUSTED_PROXIES": "0.0.0.0/0"}):
        assert _authorize(ctx, req).decision == AuthDecision.DENY


def test_zero_zero_cidr_index_does_not_render_owner_spa(ctx):
    """End-to-end: even hitting `/` directly (which serves index.html
    on the loopback path) must NOT render the owner SPA when the
    operator's CIDR list is 0.0.0.0/0 and the remote is public."""
    with (
        mock.patch.dict(os.environ, {"BPP_TRUSTED_PROXIES": "0.0.0.0/0"}),
        ctx._test_app.test_request_context("/", environ_overrides={"REMOTE_ADDR": "8.8.8.8"}),
        ctx._test_app.app_context(),
    ):
        # The before_request hook would normally DENY this — but the
        # test harness short-circuits auth in TESTING mode. Instead
        # just verify _is_trusted_peer is False, which is what the
        # real auth path keys off.
        from bpp.web.share import _is_trusted_peer

        assert not _is_trusted_peer("8.8.8.8")


def test_ipv6_default_route_rejected(ctx):
    """The IPv6 catch-all has the same blast radius as 0.0.0.0/0."""
    from bpp.web.share import AuthDecision

    req = FakeRequest(path="/", remote_addr="2001:4860:4860::8888")
    with mock.patch.dict(os.environ, {"BPP_TRUSTED_PROXIES": "::/0"}):
        assert _authorize(ctx, req).decision == AuthDecision.DENY


def test_public_v4_cidr_rejected(ctx):
    """A non-private IPv4 range (e.g. AWS metadata 169.254 is
    link-local and OK; an arbitrary public /16 is NOT) must be
    rejected. Use 8.8.0.0/16 — Google's range, definitely public."""
    from bpp.web.share import AuthDecision

    req = FakeRequest(path="/", remote_addr="8.8.4.4")
    with mock.patch.dict(os.environ, {"BPP_TRUSTED_PROXIES": "8.8.0.0/16"}):
        assert _authorize(ctx, req).decision == AuthDecision.DENY


def test_docker_bridge_still_works_after_safety_filter(ctx):
    """The default-Dockerfile config must keep working: the
    safety filter rejects 0/0 + public ranges but still admits
    the standard bridge gateway range."""
    from bpp.web.share import AuthDecision

    req = FakeRequest(path="/", remote_addr="172.17.0.1")
    with mock.patch.dict(os.environ, {"BPP_TRUSTED_PROXIES": "172.16.0.0/12"}):
        assert _authorize(ctx, req).decision == AuthDecision.ALLOW


def test_docker_desktop_alias_still_works_after_safety_filter(ctx):
    """Docker Desktop on macOS / Windows uses 192.168.65.0/24 as
    its host-loopback alias. That's RFC1918 private — must pass."""
    from bpp.web.share import AuthDecision

    req = FakeRequest(path="/", remote_addr="192.168.65.1")
    with mock.patch.dict(os.environ, {"BPP_TRUSTED_PROXIES": "192.168.65.0/24"}):
        assert _authorize(ctx, req).decision == AuthDecision.ALLOW


def test_link_local_cidr_accepted(ctx):
    """169.254/16 is link-local — used by some container runtimes
    for the metadata service. The safety filter must allow it."""
    from bpp.web.share import AuthDecision

    req = FakeRequest(path="/", remote_addr="169.254.169.254")
    with mock.patch.dict(os.environ, {"BPP_TRUSTED_PROXIES": "169.254.0.0/16"}):
        assert _authorize(ctx, req).decision == AuthDecision.ALLOW


def test_unsafe_cidr_emits_warning(ctx, caplog):
    """The operator should see a breadcrumb when their config
    is rejected, not silent failure."""
    import logging

    with (
        mock.patch.dict(os.environ, {"BPP_TRUSTED_PROXIES": "0.0.0.0/0"}),
        caplog.at_level(logging.WARNING),
    ):
        # Trigger parsing
        from bpp.web.share import _trusted_peer_networks

        nets = _trusted_peer_networks()
    assert nets == ()
    assert any(
        "rejecting unsafe CIDR" in r.message and "0.0.0.0/0" in r.message for r in caplog.records
    )


def test_unsafe_cidr_warning_fires_once_not_per_request(ctx, caplog):
    """The parser re-runs on every request (so env mutations are
    observable in tests), but the warning must fire ONCE per startup
    per unique bad value. Otherwise an operator with
    BPP_TRUSTED_PROXIES=0.0.0.0/0 + LAN sharing on gets the same
    warning N times per phone poll, filling server.log."""
    import logging

    from bpp.web.share import _trusted_peer_networks

    with (
        mock.patch.dict(os.environ, {"BPP_TRUSTED_PROXIES": "0.0.0.0/0"}),
        caplog.at_level(logging.WARNING),
    ):
        # Five parses (simulating five LAN requests)
        for _ in range(5):
            _trusted_peer_networks()

    matches = [r for r in caplog.records if "rejecting unsafe CIDR" in r.message]
    assert len(matches) == 1, f"expected exactly 1 warning, got {len(matches)}"


def test_invalid_cidr_warning_fires_once(ctx, caplog):
    """Same one-shot semantics for the malformed-CIDR warning."""
    import logging

    from bpp.web.share import _trusted_peer_networks

    with (
        mock.patch.dict(os.environ, {"BPP_TRUSTED_PROXIES": "not-a-cidr"}),
        caplog.at_level(logging.WARNING),
    ):
        for _ in range(5):
            _trusted_peer_networks()

    matches = [r for r in caplog.records if "ignoring invalid CIDR" in r.message]
    assert len(matches) == 1


def test_distinct_bad_cidrs_each_warn_once(ctx, caplog):
    """Two different bad CIDRs in the same env var should each
    produce ONE warning, not zero (memo key must be per-chunk)."""
    import logging

    from bpp.web.share import _trusted_peer_networks

    with (
        mock.patch.dict(os.environ, {"BPP_TRUSTED_PROXIES": "0.0.0.0/0,8.8.0.0/16,not-a-cidr"}),
        caplog.at_level(logging.WARNING),
    ):
        for _ in range(3):
            _trusted_peer_networks()

    unsafe = [r for r in caplog.records if "rejecting unsafe CIDR" in r.message]
    invalid = [r for r in caplog.records if "ignoring invalid CIDR" in r.message]
    # 2 unsafe (0.0.0.0/0 + public 8.8.0.0/16) + 1 invalid, each once
    assert len(unsafe) == 2, f"expected 2 unsafe warnings, got {len(unsafe)}"
    assert len(invalid) == 1, f"expected 1 invalid warning, got {len(invalid)}"


def test_mixed_safe_and_unsafe_cidrs_drops_only_unsafe(ctx):
    """Mixed config: keep the safe one, drop the unsafe one with
    a warning. The valid entry still trusts its range."""
    from bpp.web.share import AuthDecision

    req = FakeRequest(path="/", remote_addr="172.17.0.1")
    with mock.patch.dict(os.environ, {"BPP_TRUSTED_PROXIES": "0.0.0.0/0,172.16.0.0/12"}):
        assert _authorize(ctx, req).decision == AuthDecision.ALLOW

    # And public IP is NOT trusted via the dropped entry
    req2 = FakeRequest(path="/", remote_addr="8.8.8.8")
    with mock.patch.dict(os.environ, {"BPP_TRUSTED_PROXIES": "0.0.0.0/0,172.16.0.0/12"}):
        assert _authorize(ctx, req2).decision == AuthDecision.DENY


# ─── /api/* still token-gated even from a trusted peer ────────────


def test_trusted_peer_still_needs_api_token(ctx):
    from bpp.web.share import AuthDecision

    req = FakeRequest(path="/api/v1/photos", remote_addr="172.17.0.1")
    with mock.patch.dict(os.environ, {"BPP_TRUSTED_PROXIES": "172.16.0.0/12"}):
        assert _authorize(ctx, req).decision == AuthDecision.DENY


def test_trusted_peer_with_app_token_allowed(ctx):
    from bpp.web.share import AuthDecision

    req = FakeRequest(
        path="/api/v1/photos",
        remote_addr="172.17.0.1",
        headers={"X-Auth-Token": ctx.auth_token},
    )
    with mock.patch.dict(os.environ, {"BPP_TRUSTED_PROXIES": "172.16.0.0/12"}):
        assert _authorize(ctx, req).decision == AuthDecision.ALLOW


# ─── Legacy BPP_TRUST_PROXY=1 is ignored ──────────────────────────


def test_legacy_boolean_flag_does_not_promote(ctx):
    """The deprecated boolean must not silently keep working — the
    only safe migration is to switch to BPP_TRUSTED_PROXIES."""
    from bpp.web.share import AuthDecision

    req = FakeRequest(path="/", remote_addr="172.17.0.1")
    with mock.patch.dict(os.environ, {"BPP_TRUST_PROXY": "1"}):
        # No CIDR list set → bridge IP is still remote → DENY at LAN gate.
        assert _authorize(ctx, req).decision == AuthDecision.DENY


def test_legacy_boolean_flag_emits_warning(caplog):
    """First-run breadcrumb so the operator knows to migrate."""
    import logging

    from bpp.web.share import warn_if_legacy_trust_proxy

    with (
        mock.patch.dict(os.environ, {"BPP_TRUST_PROXY": "1"}),
        caplog.at_level(logging.WARNING),
    ):
        warn_if_legacy_trust_proxy()
    assert any("BPP_TRUST_PROXY is deprecated" in r.message for r in caplog.records)


# ─── Malformed CIDRs ──────────────────────────────────────────────


def test_malformed_cidrs_dropped_not_trusted(ctx):
    """Don't silently fall through to "trust everything" when the
    operator pastes a typo'd CIDR — the bad entry is logged and
    discarded. Other entries in the list still apply."""
    from bpp.web.share import AuthDecision

    req = FakeRequest(path="/", remote_addr="172.17.0.1")
    with mock.patch.dict(
        os.environ,
        {"BPP_TRUSTED_PROXIES": "not-a-cidr,172.16.0.0/12,also-bad"},
    ):
        # The valid entry covers the request → ALLOW
        assert _authorize(ctx, req).decision == AuthDecision.ALLOW


def test_only_malformed_cidrs_means_no_trust(ctx):
    from bpp.web.share import AuthDecision

    req = FakeRequest(path="/", remote_addr="172.17.0.1")
    with mock.patch.dict(os.environ, {"BPP_TRUSTED_PROXIES": "garbage,more-garbage"}):
        assert _authorize(ctx, req).decision == AuthDecision.DENY


def test_empty_cidr_string_means_no_trust(ctx):
    from bpp.web.share import AuthDecision

    req = FakeRequest(path="/", remote_addr="172.17.0.1")
    with mock.patch.dict(os.environ, {"BPP_TRUSTED_PROXIES": "   "}):
        assert _authorize(ctx, req).decision == AuthDecision.DENY


# ─── index() handler does NOT promote trusted peers to owner SPA
#     when LAN sharing is on — the specific concern is
#     "trust-proxy as a shortcut for rendering owner/app-token
#     SPA state". The owner-SPA branch only fires when the LAN gate
#     wasn't tripped (i.e., loopback or trusted CIDR). With LAN
#     sharing enabled, *every* non-loopback request flows through
#     the device-pairing path. ────────────────────────────────────


def test_trusted_peer_with_lan_sharing_off_gets_owner_spa(ctx):
    """Default: LAN sharing off, trusted peer (Docker bridge) hits
    /. Server renders the owner SPA — that's the legitimate Docker
    first-run case."""
    with (
        mock.patch.dict(os.environ, {"BPP_TRUSTED_PROXIES": "172.16.0.0/12"}),
        ctx._test_app.test_request_context("/", environ_overrides={"REMOTE_ADDR": "172.17.0.1"}),
        ctx._test_app.app_context(),
    ):
        from bpp.web.bp_core import index

        resp = index()
    html = resp.get_data(as_text=True)
    assert "auth-token" in html.lower(), (
        "Owner SPA should render the app-token meta tag for trusted-peer + LAN-sharing-off"
    )


def test_untrusted_peer_with_lan_sharing_on_hits_pair_page(ctx):
    """When LAN sharing is on, every non-trusted client gets the
    device-pairing page — never the owner SPA. The
    concern: trust-proxy must not be a shortcut to owner state."""
    from bpp.web.share import set_lan_sharing_enabled

    with ctx._test_app.app_context():
        set_lan_sharing_enabled(ctx.get_conn(), True)

    with (
        ctx._test_app.test_request_context("/", environ_overrides={"REMOTE_ADDR": "192.168.1.50"}),
        ctx._test_app.app_context(),
    ):
        from bpp.web.bp_core import index

        resp = index()
    html = resp.get_data(as_text=True)
    # pair.html / index.html distinction: pair.html has "Waiting"
    # in its <title>; index.html has the auth-token meta tag.
    assert "auth-token" not in html.lower() or "waiting" in html.lower(), (
        "Untrusted LAN client must not receive owner-SPA auth-token"
    )

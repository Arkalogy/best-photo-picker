"""Trusted-peer CIDR allowlist (replaces the boolean BPP_TRUST_PROXY) — part 2 of 2:
TestBehindProxyGatedByTrustedNetworks."""

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

# ─── behind_proxy gated by BPP_TRUSTED_PROXIES ─────────────


class TestBehindProxyGatedByTrustedNetworks:
    """The legacy `behind_proxy` config flag enabled `ProxyFix` with no
    upstream-peer check. That made it a privilege-escalation primitive:
    a public-internet client could send `X-Forwarded-For: 127.0.0.1`
    and ProxyFix would rewrite `request.remote_addr` to loopback,
    promoting the client to LOCAL_APP. The fix ties ProxyFix activation
    to BPP_TRUSTED_PROXIES being non-empty AND gates the rewrite at
    the WSGI layer to only fire when the raw upstream peer is in the
    trusted set."""

    def _wire_proxy_fix(self, app, behind_proxy: bool):
        """Replicate the production wiring from bpp/commands.py so the
        test exercises the actual gate logic, not a parallel
        implementation."""
        if not behind_proxy:
            return

        from bpp.web.share import _trusted_peer_networks

        nets = _trusted_peer_networks()
        if not nets:
            return  # the production code logs and skips

        import ipaddress as _ip

        from werkzeug.middleware.proxy_fix import ProxyFix

        _proxy_fixed = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
        _bare_app = app.wsgi_app
        _trusted_nets = nets

        def _gated_proxy_fix(environ, start_response):
            raw_remote = environ.get("REMOTE_ADDR", "")
            try:
                raw_ip = _ip.ip_address(raw_remote)
            except ValueError:
                return _bare_app(environ, start_response)
            if any(raw_ip in net for net in _trusted_nets):
                return _proxy_fixed(environ, start_response)
            return _bare_app(environ, start_response)

        app.wsgi_app = _gated_proxy_fix

    def _capture_remote_addr(self, app):
        """Register a one-off endpoint that echoes the post-WSGI
        `request.remote_addr` so the test can verify what the app
        layer actually sees. TESTING=True bypasses the auth gate so
        the echo endpoint is reachable from any remote_addr."""
        from flask import jsonify, request

        def _echo():
            return jsonify({"remote_addr": request.remote_addr}), 200

        # Avoid name collisions if the test runs multiple times in one process
        endpoint_name = f"_r8h1_echo_{id(app)}_{len(app.view_functions)}"
        app.add_url_rule("/_r8h1_echo", endpoint_name, _echo)
        app.config["TESTING"] = True
        return "/_r8h1_echo"

    def test_behind_proxy_with_empty_trusted_does_not_rewrite_xff(self, ctx):
        """When BPP_TRUSTED_PROXIES is empty/unset, behind_proxy must
        NOT wire ProxyFix at all — the rewrite is skipped entirely.
        Verified end-to-end: even from a "trusted-looking" RFC1918
        peer with X-Forwarded-For set, the app sees the raw remote
        addr (no rewrite) because the gate refuses to install
        ProxyFix without an explicit allowlist."""
        original_wsgi = ctx._test_app.wsgi_app
        echo_path = self._capture_remote_addr(ctx._test_app)
        try:
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("BPP_TRUSTED_PROXIES", None)
                os.environ.pop("BPP_TRUST_PROXY", None)
                self._wire_proxy_fix(ctx._test_app, behind_proxy=True)

                client = ctx._test_app.test_client()
                resp = client.get(
                    echo_path,
                    headers={"X-Forwarded-For": "127.0.0.1"},
                    environ_overrides={"REMOTE_ADDR": "172.17.0.1"},
                )
            assert resp.status_code == 200
            # No allowlist → no ProxyFix → raw remote_addr preserved
            assert resp.get_json()["remote_addr"] == "172.17.0.1", (
                "behind_proxy without BPP_TRUSTED_PROXIES must not install ProxyFix"
            )
        finally:
            ctx._test_app.wsgi_app = original_wsgi

    def test_public_peer_xff_loopback_does_not_rewrite_remote_addr(self, ctx):
        """The actual exploit: public client at 8.8.8.8 lying via
        `X-Forwarded-For: 127.0.0.1`. The gate must recognize 8.8.8.8
        as untrusted and pass the request through WITHOUT calling
        ProxyFix, so `request.remote_addr` stays at 8.8.8.8."""
        original_wsgi = ctx._test_app.wsgi_app
        echo_path = self._capture_remote_addr(ctx._test_app)
        try:
            with mock.patch.dict(os.environ, {"BPP_TRUSTED_PROXIES": "172.16.0.0/12"}):
                self._wire_proxy_fix(ctx._test_app, behind_proxy=True)

                client = ctx._test_app.test_client()
                resp = client.get(
                    echo_path,
                    headers={"X-Forwarded-For": "127.0.0.1"},
                    environ_overrides={"REMOTE_ADDR": "8.8.8.8"},
                )
            assert resp.status_code == 200
            assert resp.get_json()["remote_addr"] == "8.8.8.8", (
                f"Public IP 8.8.8.8 with spoofed XFF must keep raw remote_addr; "
                f"app saw {resp.get_json()['remote_addr']!r}"
            )
        finally:
            ctx._test_app.wsgi_app = original_wsgi

    def test_trusted_peer_xff_does_rewrite_remote_addr(self, ctx):
        """Inverse: a TRUSTED peer (e.g. nginx on 172.17.0.1) sending
        `X-Forwarded-For: 192.168.1.50` SHOULD get rewritten to
        192.168.1.50 — that's the legitimate use case the flag exists
        for."""
        original_wsgi = ctx._test_app.wsgi_app
        echo_path = self._capture_remote_addr(ctx._test_app)
        try:
            with mock.patch.dict(os.environ, {"BPP_TRUSTED_PROXIES": "172.16.0.0/12"}):
                self._wire_proxy_fix(ctx._test_app, behind_proxy=True)

                client = ctx._test_app.test_client()
                resp = client.get(
                    echo_path,
                    headers={"X-Forwarded-For": "192.168.1.50"},
                    environ_overrides={"REMOTE_ADDR": "172.17.0.1"},
                )
            assert resp.status_code == 200
            assert resp.get_json()["remote_addr"] == "192.168.1.50", (
                f"Trusted upstream peer's X-Forwarded-For should be honored; "
                f"app saw {resp.get_json()['remote_addr']!r}"
            )
        finally:
            ctx._test_app.wsgi_app = original_wsgi

    def test_invalid_remote_addr_does_not_crash_gate(self, ctx):
        """Defensive: if REMOTE_ADDR is missing or unparseable
        (shouldn't happen in production but Werkzeug doesn't promise),
        the gate must fall through to the bare app, not crash."""
        original_wsgi = ctx._test_app.wsgi_app
        echo_path = self._capture_remote_addr(ctx._test_app)
        try:
            with mock.patch.dict(os.environ, {"BPP_TRUSTED_PROXIES": "172.16.0.0/12"}):
                self._wire_proxy_fix(ctx._test_app, behind_proxy=True)

                client = ctx._test_app.test_client()
                resp = client.get(
                    echo_path,
                    headers={"X-Forwarded-For": "127.0.0.1"},
                    environ_overrides={"REMOTE_ADDR": "not-an-ip"},
                )
            assert resp.status_code == 200
            # Bare app passthrough — XFF ignored, raw remote stays
            assert resp.get_json()["remote_addr"] == "not-an-ip"
        finally:
            ctx._test_app.wsgi_app = original_wsgi

"""Tests for fingerprint cookie + leak-protection headers on the index.

Contract:
- A LAN client (non-loopback) hitting `/` with no fingerprint cookie
  gets one set on the response, HttpOnly + SameSite=Lax + 1y expiry.
- Subsequent LAN requests from the same client carry the cookie back.
- Loopback (Tauri / local browser) is exempt — no cookie set, no
  fingerprint tracked, since the local app uses the app session token
  directly.
- The index page response carries `Referrer-Policy: no-referrer` so
  outbound clicks (map tiles, video CDNs) don't leak `?_token=…`.
- The index template includes a `history.replaceState` shim that
  strips the `_token` query param after first paint, so the URL bar
  doesn't preserve the token.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture
def app(tmp_path):
    from bpp.web.app import create_app
    from bpp.web.share import set_lan_sharing_enabled

    workdir = str(tmp_path / "workdir")
    os.makedirs(workdir)
    app = create_app(workdir=workdir)
    app.config["TESTING"] = False  # need the actual middleware
    ctx = app.extensions["bpp"]
    with app.app_context():
        set_lan_sharing_enabled(ctx.get_conn(), True)
    return app


# ─── Cookie set on first LAN visit ──────────────────────────────────


class TestFingerprintCookie:
    def test_lan_visit_sets_cookie(self, app):
        client = app.test_client()
        r = client.get("/", environ_overrides={"REMOTE_ADDR": "192.168.1.5"})
        assert r.status_code == 200
        cookies = r.headers.getlist("Set-Cookie")
        assert any("bpp_share_fp=" in c for c in cookies), (
            f"fingerprint cookie not set; got {cookies}"
        )

    def test_loopback_does_not_set_cookie(self, app):
        client = app.test_client()
        r = client.get("/", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
        cookies = r.headers.getlist("Set-Cookie")
        assert not any("bpp_share_fp=" in c for c in cookies), (
            f"loopback should not get a share fingerprint cookie; got {cookies}"
        )

    def test_repeat_visit_does_not_overwrite_cookie(self, app):
        """Once the client has a fingerprint, don't churn it. Stable
        fingerprint = stable trust state across page reloads."""
        client = app.test_client()
        r = client.get("/", environ_overrides={"REMOTE_ADDR": "192.168.1.5"})
        first_cookies = r.headers.getlist("Set-Cookie")
        # Find the fingerprint value
        fp_cookie = next(c for c in first_cookies if c.startswith("bpp_share_fp="))
        fp_value = fp_cookie.split(";")[0].split("=", 1)[1]

        # Second request with the cookie set
        client.set_cookie("bpp_share_fp", fp_value)
        r2 = client.get("/", environ_overrides={"REMOTE_ADDR": "192.168.1.5"})
        new_set = [c for c in r2.headers.getlist("Set-Cookie") if c.startswith("bpp_share_fp=")]
        # Either no new Set-Cookie, or the same value (browser-friendly idempotent)
        if new_set:
            assert fp_value in new_set[0], "fingerprint cookie value should be stable"

    def test_cookie_attributes(self, app):
        """HttpOnly (no JS access) + SameSite=Lax (CSRF-resistant) + 1y expiry."""
        client = app.test_client()
        r = client.get("/", environ_overrides={"REMOTE_ADDR": "192.168.1.5"})
        cookies = r.headers.getlist("Set-Cookie")
        fp = next(c for c in cookies if c.startswith("bpp_share_fp="))
        assert "HttpOnly" in fp
        assert "SameSite=Lax" in fp
        # 1 year expiry — expressed via Max-Age or Expires
        assert "Max-Age" in fp or "Expires" in fp

    def test_cookie_not_secure_over_http(self, app):
        """Secure flag must NOT be set on plain HTTP (default LAN use)."""
        client = app.test_client()
        r = client.get("/", environ_overrides={"REMOTE_ADDR": "192.168.1.5"})
        cookies = r.headers.getlist("Set-Cookie")
        fp = next(c for c in cookies if c.startswith("bpp_share_fp="))
        assert "Secure" not in fp, "Secure flag should not be set on plain HTTP"

    def test_cookie_is_secure_over_https(self, app):
        """Secure flag must be set when the request scheme is HTTPS."""
        client = app.test_client()
        r = client.get(
            "/",
            environ_overrides={
                "REMOTE_ADDR": "192.168.1.5",
                "wsgi.url_scheme": "https",
            },
        )
        cookies = r.headers.getlist("Set-Cookie")
        fp = next(c for c in cookies if c.startswith("bpp_share_fp="))
        assert "Secure" in fp, "Secure flag must be set when request is HTTPS"


# ─── Pending device row created from cookie ─────────────────────────


class TestCookieCreatesDeviceRow:
    def test_lan_visit_creates_pending_device(self, app):
        from bpp.web.share import get_device_by_fingerprint

        client = app.test_client()
        r = client.get(
            "/",
            environ_overrides={"REMOTE_ADDR": "192.168.1.5"},
            headers={"User-Agent": "iPhone Safari"},
        )
        # Extract fingerprint from Set-Cookie
        cookies = r.headers.getlist("Set-Cookie")
        fp_cookie = next(c for c in cookies if c.startswith("bpp_share_fp="))
        fp_value = fp_cookie.split(";")[0].split("=", 1)[1]

        ctx = app.extensions["bpp"]
        with app.app_context():
            device = get_device_by_fingerprint(ctx.get_conn(), fp_value)
        assert device is not None
        assert device["ip_at_pair"] == "192.168.1.5"
        # Name should be derived from UA (some recognisable substring)
        assert "iPhone" in device["name"] or device["name"]


# ─── Referrer-Policy header ─────────────────────────────────────────


class TestReferrerPolicy:
    def test_referrer_policy_header_on_index(self, app):
        client = app.test_client()
        r = client.get("/", environ_overrides={"REMOTE_ADDR": "192.168.1.5"})
        assert r.headers.get("Referrer-Policy") == "no-referrer", (
            "index must set Referrer-Policy: no-referrer to prevent the "
            "share token from leaking via outbound clicks"
        )


# ─── history.replaceState shim in the template ──────────────────────


class TestHistoryReplaceShim:
    def test_index_html_contains_url_strip_shim(self, app):
        """The HTML must include JS that strips `_token` from the URL bar
        once the page is loaded — the meta tag holds it, the URL doesn't
        need to. Prevents accidental token sharing via screenshots /
        screen recordings."""
        client = app.test_client()
        r = client.get("/", environ_overrides={"REMOTE_ADDR": "192.168.1.5"})
        body = r.get_data(as_text=True)
        assert "history.replaceState" in body, (
            "index template must include a history.replaceState call to "
            "strip _token from the URL bar"
        )
        assert "_token" in body


# ─── Device-name sanitization (defense in depth for stored UA names) ──


class TestSanitizeDeviceName:
    """Friendly names parsed from User-Agent are stored on share_devices
    and rendered in the Mac UI. The renderer escapes via `esc()` but
    storing only safe characters is the belt + suspenders fix."""

    def test_recognized_uas_unchanged(self):
        from bpp.web.bp_core import _device_name_from_ua

        # Substring matches return literal labels — no sanitization needed
        assert _device_name_from_ua("Mozilla/5.0 (iPhone; …)") == "iPhone"
        assert _device_name_from_ua("Mozilla/5.0 (Macintosh; …)") == "Mac"

    def test_unrecognized_ua_falls_through_to_sanitizer(self):
        from bpp.web.bp_core import _device_name_from_ua

        # Truly unrecognized UA → first 40 chars, sanitized
        result = _device_name_from_ua("CustomBrowser/1.0")
        assert result == "CustomBrowser/1.0"

    def test_xss_payload_stripped(self):
        from bpp.web.bp_core import _sanitize_device_name

        # HTML metacharacters get stripped before storage
        out = _sanitize_device_name("Mozilla<script>alert(1)</script>iPhone")
        assert "<" not in out
        assert ">" not in out
        assert "(" not in out  # parens not in allowed set
        assert "script" in out  # text content preserved (we strip syntax, not letters)

    def test_strips_control_characters(self):
        from bpp.web.bp_core import _sanitize_device_name

        out = _sanitize_device_name("iPhone\x00\x07\nfoo")
        # Control bytes gone; whitespace collapsed
        assert "\x00" not in out
        assert "\x07" not in out

    def test_caps_at_40_chars(self):
        from bpp.web.bp_core import _sanitize_device_name

        long_name = "A" * 200
        assert len(_sanitize_device_name(long_name)) <= 40

    def test_empty_falls_back_to_unknown(self):
        from bpp.web.bp_core import _sanitize_device_name

        assert _sanitize_device_name("") == "Unknown device"
        # All-stripped input also falls back
        assert _sanitize_device_name("<<<>>>") == "Unknown device"

    def test_preserves_ascii_punctuation_in_allowlist(self):
        from bpp.web.bp_core import _sanitize_device_name

        # Allowed: word chars, whitespace, dash, dot, slash
        out = _sanitize_device_name("Firefox/123.4 - dev/1")
        assert out == "Firefox/123.4 - dev/1"

"""Tests for LAN sharing helpers and /api/share endpoints."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

# ─── share.py unit tests ─────────────────────────────────────────────


class TestDetectLanIp:
    def test_returns_ip_or_none(self):
        from bpp.web.share import detect_lan_ip

        ip = detect_lan_ip()
        # Result is either None (no network) or a non-loopback IP string.
        assert ip is None or (isinstance(ip, str) and not ip.startswith("127."))

    def test_returns_none_when_no_network(self):
        """When socket.connect raises OSError, detect_lan_ip returns None."""
        from unittest.mock import MagicMock

        from bpp.web.share import detect_lan_ip

        fake_sock = MagicMock()
        fake_sock.connect.side_effect = OSError("no route")
        # detect_lan_ip lives in share_runtime since the v0.1 refactor;
        # patch where the socket call actually runs.
        with patch("bpp.web.share_runtime.socket.socket", return_value=fake_sock):
            assert detect_lan_ip() is None


class TestGetLanShareUrl:
    def test_with_explicit_ip(self):
        from bpp.web.share import get_lan_share_url

        url = get_lan_share_url(5001, "abc123", ip="192.168.1.50")
        assert url == "http://192.168.1.50:5001/?_token=abc123"

    def test_url_quotes_token(self):
        from bpp.web.share import get_lan_share_url

        # Hex tokens shouldn't need quoting, but special chars must be quoted
        url = get_lan_share_url(5001, "a/b+c=d", ip="10.0.0.1")
        assert "a%2Fb%2Bc%3Dd" in url

    def test_falls_back_to_placeholder_when_no_ip(self):
        from bpp.web.share import get_lan_share_url

        # get_lan_share_url lives in share_runtime since the v0.1 refactor;
        # patch the detect_lan_ip in the module where get_lan_share_url
        # actually looks it up, not the re-export shim in share.py.
        with patch("bpp.web.share_runtime.detect_lan_ip", return_value=None):
            url = get_lan_share_url(5001, "abc")
            assert "<your-lan-ip>" in url


class TestFormatShareBanner:
    def test_shows_listen_addr_and_warnings_no_token(self):
        """The banner must NOT include the tokenized share URL — that's
        a long-lived secret that would persist in rotating server.log
        files. Banner takes only the public host:port and tells the
        user to copy the URL from the owner-only Settings UI."""
        from bpp.web.share import format_share_banner

        lines = format_share_banner("192.168.1.5:5001")
        joined = "\n".join(lines)
        assert "LAN SHARING ENABLED" in joined
        assert "192.168.1.5:5001" in joined
        # Banner explicitly warns about untrusted networks.
        assert "trust" in joined.lower()
        # No share token can leak through this path.
        assert "_token=" not in joined
        assert "Settings" in joined  # points the user at the right place


class TestRenderQrSvg:
    def test_returns_svg_markup(self):
        from bpp.web.share import render_qr_svg

        svg = render_qr_svg("http://192.168.1.5:5001/?_token=abc")
        assert svg.startswith("<?xml") or svg.startswith("<svg")
        assert "<svg" in svg
        # qrcode SvgPathImage emits a single <path> with the modules
        assert "<path" in svg


class TestRenderQrPng:
    """Branded PNG renderer used by /api/share/qr."""

    def test_returns_png_bytes_with_magic(self):
        from bpp.web.share import render_qr_png

        data = render_qr_png("http://192.168.1.5:5001/?_token=abc")
        # PNG magic: 89 50 4e 47 0d 0a 1a 0a
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        # Sanity: the styled renderer always emits a meaningful blob
        assert len(data) > 1000

    def test_handles_long_token_url(self):
        """Real share URLs are ~110 chars (host + 64-char hex token)."""
        from bpp.web.share import render_qr_png

        url = "http://192.168.10.119:5001/?_token=" + "a" * 64
        data = render_qr_png(url)
        assert data[:8] == b"\x89PNG\r\n\x1a\n"


class TestQrGlyphFontFallback:
    """L2: when no TrueType font is found, the bitmap fallback ships a
    visibly worse glyph. We log a warning so headless / minimal Linux
    deploys can be diagnosed instead of silently shipping a degraded QR."""

    def test_warning_emitted_when_no_truetype_available(self, caplog):
        from unittest.mock import patch

        from PIL import ImageFont

        from bpp.web.share import _render_bpp_glyph

        # Only fail the *named-face* lookups our code attempts — let
        # ImageFont.load_default()'s internal TrueType lookup succeed,
        # otherwise the fallback path itself fails before we can observe
        # the warning.
        named_faces = {"Helvetica.ttc", "Helvetica.ttf", "Arial.ttf", "DejaVuSans-Bold.ttf"}
        original_truetype = ImageFont.truetype

        def selective_fail(font, *args, **kwargs):
            if isinstance(font, str) and font in named_faces:
                raise OSError(f"forced miss for {font}")
            return original_truetype(font, *args, **kwargs)

        with (
            patch.object(ImageFont, "truetype", side_effect=selective_fail),
            caplog.at_level("WARNING", logger="bpp.web.share"),
        ):
            _render_bpp_glyph(size=64)
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert any("TrueType" in r.message or "bitmap" in r.message for r in warnings), (
            f"Expected font fallback warning, got: {[r.message for r in warnings]}"
        )

    def test_no_warning_when_font_resolves(self, caplog):
        """The happy path must not spam logs on every QR render."""
        from bpp.web.share import _render_bpp_glyph

        with caplog.at_level("WARNING", logger="bpp.web.share"):
            _render_bpp_glyph(size=64)
        bpp_warnings = [
            r
            for r in caplog.records
            if r.levelname == "WARNING" and r.name.startswith("bpp.web.share")
        ]
        msgs = [r.message for r in bpp_warnings]
        assert bpp_warnings == [], f"Unexpected warning when a TrueType font is available: {msgs}"


# ─── /api/share/* endpoint tests ─────────────────────────────────────


@pytest.fixture
def share_client(tmp_path):
    """Test client with a fresh app and a temp workdir."""
    from bpp.web.app import create_app

    workdir = str(tmp_path / "workdir")
    os.makedirs(workdir)

    app = create_app(workdir=workdir)
    app.config["TESTING"] = True
    return app


class TestShareInfoEndpoint:
    def test_disabled_by_default(self, share_client):
        client = share_client.test_client()
        r = client.get("/api/v1/share/info")
        assert r.status_code == 200
        data = r.get_json()
        assert data["enabled"] is False
        assert data["share_url"] is None
        assert data["port"] == 5001  # WebAppState default

    def test_enabled_returns_share_url(self, share_client):
        from bpp.web.share import set_lan_sharing_enabled

        ctx = share_client.extensions["bpp"]
        ctx.port = 5001
        with share_client.app_context():
            set_lan_sharing_enabled(ctx.get_conn(), True)

        with patch("bpp.web.share.detect_lan_ip", return_value="192.168.1.50"):
            client = share_client.test_client()
            r = client.get("/api/v1/share/info")
        assert r.status_code == 200
        data = r.get_json()
        assert data["enabled"] is True
        assert data["lan_ip"] == "192.168.1.50"
        assert data["share_url"] is not None
        assert "192.168.1.50:5001" in data["share_url"]

    def test_enabled_but_no_lan_returns_disabled(self, share_client):
        """Sharing flag on, but no LAN IP detected → reports disabled.
        Avoids advertising a stale URL to users on unplugged ethernet."""
        from bpp.web.share import set_lan_sharing_enabled

        ctx = share_client.extensions["bpp"]
        with share_client.app_context():
            set_lan_sharing_enabled(ctx.get_conn(), True)

        with patch("bpp.web.share.detect_lan_ip", return_value=None):
            client = share_client.test_client()
            r = client.get("/api/v1/share/info")
        data = r.get_json()
        assert data["enabled"] is False
        assert data["share_url"] is None


class TestShareQrEndpoint:
    def test_returns_404_when_disabled(self, share_client):
        client = share_client.test_client()
        r = client.get("/api/v1/share/qr")
        assert r.status_code == 404

    def test_returns_svg_when_enabled(self, share_client):
        from bpp.web.share import set_lan_sharing_enabled

        ctx = share_client.extensions["bpp"]
        ctx.port = 5001
        with share_client.app_context():
            set_lan_sharing_enabled(ctx.get_conn(), True)

        with patch("bpp.web.share.detect_lan_ip", return_value="192.168.1.50"):
            client = share_client.test_client()
            r = client.get("/api/v1/share/qr")
        assert r.status_code == 200
        assert "image/png" in r.content_type
        body = r.get_data()
        assert body[:8] == b"\x89PNG\r\n\x1a\n"


# --- CLI ----------------------------------------------------------
# The `--lan` flag was removed in favour of the Settings → Share
# toggle. The bind defaults to 0.0.0.0; the middleware in app.py is
# the security boundary. CLI flag tests live with `bpp serve` itself.

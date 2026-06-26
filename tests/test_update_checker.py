"""Tests for the update checker module and API endpoints."""

from __future__ import annotations

import json
import os
from unittest import mock

import pytest

from bpp.web import update_checker
from bpp.web.app import create_app


@pytest.fixture
def app(tmp_path):
    workdir = str(tmp_path / "workdir")
    os.makedirs(workdir)
    a = create_app(workdir=workdir)
    a.config["TESTING"] = True
    return a


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def _clear_cache():
    """Reset the update checker cache between tests."""
    update_checker._cached_result = None
    update_checker._cached_at = 0
    yield
    update_checker._cached_result = None
    update_checker._cached_at = 0


class TestParseVersion:
    def test_simple(self):
        assert update_checker._parse_version("1.2.3") == (1, 2, 3)

    def test_with_v_prefix(self):
        assert update_checker._parse_version("v1.2.3") == (1, 2, 3)

    def test_two_part(self):
        assert update_checker._parse_version("1.0") == (1, 0)


class TestCheckForUpdate:
    def test_no_update_available(self):
        release = {
            "tag_name": "v0.1.0",
            "html_url": "https://github.com/test/repo/releases/tag/v0.1.0",
            "body": "No changes",
        }
        with mock.patch.object(update_checker, "_fetch_latest_release", return_value=release):
            result = update_checker.check_for_update(force=True)

        assert result["status"] == "ok"
        assert result["available"] is False
        assert result["current"] == "0.1.0"
        assert result["latest"] == "0.1.0"

    def test_update_available(self):
        release = {
            "tag_name": "v9.9.9",
            "html_url": "https://github.com/test/repo/releases/tag/v9.9.9",
            "body": "Big update",
        }
        with mock.patch.object(update_checker, "_fetch_latest_release", return_value=release):
            result = update_checker.check_for_update(force=True)

        assert result["status"] == "ok"
        assert result["available"] is True
        assert result["latest"] == "9.9.9"
        assert result["url"] == "https://github.com/test/repo/releases/tag/v9.9.9"
        assert result["release_notes"] == "Big update"

    def test_404_reports_error_not_up_to_date(self):
        """Regression: GitHub returning 404 (private repo, unauthenticated)
        used to silently report "up to date" — the UI lied to the user
        about an actual failure. Now it must surface status=error."""
        from urllib.error import HTTPError

        exc = HTTPError("u", 404, "Not Found", {}, None)  # type: ignore[arg-type]
        with mock.patch.object(update_checker, "_fetch_latest_release", side_effect=exc):
            result = update_checker.check_for_update(force=True)

        assert result["status"] == "error", "404 must NOT be reported as up-to-date"
        assert result["error"] == "not_found"
        assert result["available"] is False
        assert "private" in result["error_message"].lower(), (
            f"error_message should hint at private-repo cause, got: {result['error_message']!r}"
        )

    def test_403_reports_rate_limit(self):
        from urllib.error import HTTPError

        exc = HTTPError("u", 403, "rate limited", {}, None)  # type: ignore[arg-type]
        with mock.patch.object(update_checker, "_fetch_latest_release", side_effect=exc):
            result = update_checker.check_for_update(force=True)

        assert result["status"] == "error"
        assert result["error"] == "rate_limited"
        assert "rate limit" in result["error_message"].lower()

    def test_url_error_reports_network(self):
        from urllib.error import URLError

        with mock.patch.object(
            update_checker,
            "_fetch_latest_release",
            side_effect=URLError("DNS fail"),
        ):
            result = update_checker.check_for_update(force=True)

        assert result["status"] == "error"
        assert result["error"] == "network_error"

    def test_unknown_exception_still_reports_error_not_ok(self):
        """Generic Exception must NOT degrade silently to status=ok."""
        with mock.patch.object(
            update_checker, "_fetch_latest_release", side_effect=Exception("boom")
        ):
            result = update_checker.check_for_update(force=True)

        assert result["status"] == "error"
        assert result["error"] == "unknown_error"
        assert result["available"] is False
        assert result["current"] == "0.1.0"

    def test_empty_response_from_size_cap_reports_error(self):
        """_fetch_latest_release returns {} when the response exceeds the
        size cap. That MUST be reported as malformed, not "up to date"."""
        with mock.patch.object(update_checker, "_fetch_latest_release", return_value={}):
            result = update_checker.check_for_update(force=True)

        assert result["status"] == "error", (
            "Empty fetch result (size-cap refusal) must not be reported as up-to-date"
        )
        assert result["error"] == "malformed"

    def test_error_cache_ttl_is_short(self):
        """Errors should cache for 5 min (not 24h) — transient issues
        recover quickly, but we still avoid hammering on a sustained
        outage."""
        assert update_checker._CACHE_TTL_ERROR == 300
        assert update_checker._CACHE_TTL_OK == 86400

    def test_cache_hit(self):
        release = {
            "tag_name": "v9.9.9",
            "html_url": "https://example.com",
            "body": "",
        }
        with mock.patch.object(update_checker, "_fetch_latest_release", return_value=release) as m:
            update_checker.check_for_update(force=True)
            # Second call should use cache
            result = update_checker.check_for_update(force=False)

        assert m.call_count == 1
        assert result["available"] is True

    def test_force_bypasses_cache(self):
        release = {
            "tag_name": "v9.9.9",
            "html_url": "https://example.com",
            "body": "",
        }
        with mock.patch.object(update_checker, "_fetch_latest_release", return_value=release) as m:
            update_checker.check_for_update(force=True)
            update_checker.check_for_update(force=True)

        assert m.call_count == 2


class TestVersionEndpoint:
    def test_returns_version(self, client):
        resp = client.get("/api/v1/version")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "version" in data
        assert data["version"] == "0.1.0"


class TestUpdateCheckEndpoint:
    def test_returns_update_info(self, client):
        release = {
            "tag_name": "v9.9.9",
            "html_url": "https://example.com",
            "body": "notes",
        }
        with mock.patch.object(update_checker, "_fetch_latest_release", return_value=release):
            resp = client.get("/api/v1/update/check?force=1")

        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["available"] is True
        assert data["latest"] == "9.9.9"

    def test_force_param(self, client):
        release = {
            "tag_name": "v0.1.0",
            "html_url": "https://example.com",
            "body": "",
        }
        with mock.patch.object(update_checker, "_fetch_latest_release", return_value=release) as m:
            client.get("/api/v1/update/check?force=true")
            client.get("/api/v1/update/check?force=true")

        assert m.call_count == 2


# ─── R9-supply-M1: response-size cap on the GitHub fetch ──────────────


class _FakeResp:
    """Minimal stand-in for the urlopen context manager."""

    def __init__(self, body: bytes, content_length: str | None = None):
        import io

        self._buf = io.BytesIO(body)
        self._cl = content_length

    @property
    def headers(self):
        cl = self._cl

        class _H:
            def get(self, key, default=None):
                if key.lower() == "content-length":
                    return cl
                return default

        return _H()

    def read(self, n=-1):
        return self._buf.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


class TestResponseSizeCap:
    """A compromised upstream / proxy must not be able to OOM the
    server with an unbounded JSON payload. The 10s timeout bounds
    *connection* time, not *read* time."""

    def test_typical_response_succeeds(self):
        body = b'{"tag_name":"v0.2.0","html_url":"https://x","body":"ok"}'
        with mock.patch(
            "bpp.web.update_checker.urllib.request.urlopen",
            return_value=_FakeResp(body, content_length=str(len(body))),
        ):
            result = update_checker._fetch_latest_release()
        assert result["tag_name"] == "v0.2.0"

    def test_oversized_content_length_refused(self):
        cap = update_checker._MAX_RESPONSE_BYTES
        with mock.patch(
            "bpp.web.update_checker.urllib.request.urlopen",
            return_value=_FakeResp(
                b'{"tag_name":"v0.2.0"}',
                content_length=str(cap * 2),
            ),
        ):
            result = update_checker._fetch_latest_release()
        assert result == {}, "Content-Length above cap must short-circuit"

    def test_lying_server_truncated(self):
        """Server omits / lies about Content-Length but streams a
        body bigger than the cap. Bounded read still refuses."""
        cap = update_checker._MAX_RESPONSE_BYTES
        oversized = b'{"data":"' + b"x" * (cap * 2) + b'"}'
        with mock.patch(
            "bpp.web.update_checker.urllib.request.urlopen",
            return_value=_FakeResp(oversized, content_length=None),
        ):
            result = update_checker._fetch_latest_release()
        assert result == {}, (
            "body larger than the cap must be refused even if Content-Length was missing or lied"
        )

    def test_malformed_content_length_falls_through(self):
        """Garbage in Content-Length shouldn't crash — fall through
        to bounded read."""
        body = b'{"tag_name":"v0.2.0"}'
        with mock.patch(
            "bpp.web.update_checker.urllib.request.urlopen",
            return_value=_FakeResp(body, content_length="not-a-number"),
        ):
            result = update_checker._fetch_latest_release()
        assert result["tag_name"] == "v0.2.0"


# ─── R9-supply-M2: endpoint must reject paired LAN devices ────────────


class TestUpdateCheckOwnerOnly:
    """The README claim of "opt-in" privacy is only as strong as the
    server-side gate. Without `@requires_local_app`, a paired LAN phone
    can call `/api/v1/update/check?force=1` and force the host to reach
    api.github.com on demand — which contradicts the SECURITY.md story
    that the user controls when network calls happen."""

    def test_lan_device_blocked(self, tmp_path):
        from bpp.web.share import (
            approve_device,
            find_or_create_pending_device,
            get_share_token,
            set_lan_sharing_enabled,
        )

        workdir = str(tmp_path / "wd_lan_update")
        os.makedirs(workdir)
        a = create_app(workdir=workdir)
        a.config["TESTING"] = False  # Real auth middleware

        ctx = a.extensions["bpp"]
        with a.app_context():
            set_lan_sharing_enabled(ctx.get_conn(), True)
            token = get_share_token(ctx.get_conn())
            d = find_or_create_pending_device(ctx.get_conn(), "fp-update", "Phone", "192.168.1.50")
            approve_device(ctx.get_conn(), d["id"])

        c = a.test_client()
        c.set_cookie("bpp_share_fp", "fp-update")
        r = c.get(
            "/api/v1/update/check",
            headers={"X-Auth-Token": token},
            environ_overrides={"REMOTE_ADDR": "192.168.1.50"},
        )
        assert r.status_code == 403, (
            f"LAN device must not be able to force a GitHub fetch; got {r.status_code} {r.data!r}"
        )

    def test_loopback_owner_allowed(self, tmp_path):
        workdir = str(tmp_path / "wd_owner_update")
        os.makedirs(workdir)
        a = create_app(workdir=workdir)
        a.config["TESTING"] = False

        ctx = a.extensions["bpp"]
        c = a.test_client()
        # Stub the network call so we don't hit the real GitHub API.
        with mock.patch(
            "bpp.web.update_checker._fetch_latest_release",
            return_value={"tag_name": "v0.1.0", "html_url": "", "body": ""},
        ):
            r = c.get(
                "/api/v1/update/check",
                headers={"X-Auth-Token": ctx.auth_token},
                environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
            )
        assert r.status_code == 200

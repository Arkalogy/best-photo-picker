"""TDD tests for C-1: session auth token on API endpoints."""

from __future__ import annotations

import json
import os

import pytest

from bpp.web.app import create_app


def _make_analysis(n: int = 3) -> list[dict]:
    items = []
    for i in range(n):
        items.append(
            {
                "filepath": f"/tmp/test_photos/img_{i:03d}.jpg",
                "date": f"2024-01-{(i % 28) + 1:02d}T12:00:00",
                "date_day": f"2024-01-{(i % 28) + 1:02d}",
                "date_month": "2024-01",
                "file_size": 1024,
                "file_mtime": 1700000000.0,
                "blur_raw": 100.0,
                "blur_score": 0.5,
                "exposure_score": 0.5,
                "face_score": 0.5,
                "face_count": 0,
                "largest_face_ratio": 0.0,
                "face_center_dist": 0.0,
                "composition_score": 0.5,
                "aggregate_score": 0.5,
            }
        )
    return items


@pytest.fixture
def auth_app(tmp_path):
    workdir = str(tmp_path / "workdir")
    os.makedirs(workdir)
    with open(os.path.join(workdir, "analysis.json"), "w") as f:
        json.dump(_make_analysis(), f)
    app = create_app(workdir=workdir)
    # Do NOT set TESTING=True — we need the auth middleware active
    return app


@pytest.fixture
def token(auth_app):
    """Extract the auth token from the app's WebAppState."""
    ctx = auth_app.extensions["bpp"]
    return ctx.auth_token


@pytest.fixture
def client(auth_app):
    return auth_app.test_client()


class TestAuthTokenGeneration:
    """Auth token must be generated at startup and be cryptographically random."""

    def test_token_exists_on_state(self, auth_app):
        ctx = auth_app.extensions["bpp"]
        assert hasattr(ctx, "auth_token")
        assert isinstance(ctx.auth_token, str)

    def test_token_is_sufficient_length(self, token):
        # At least 32 hex chars (128 bits)
        assert len(token) >= 32

    def test_token_is_unique_per_instance(self, tmp_path):
        workdir1 = str(tmp_path / "w1")
        workdir2 = str(tmp_path / "w2")
        os.makedirs(workdir1)
        os.makedirs(workdir2)
        app1 = create_app(workdir=workdir1)
        app2 = create_app(workdir=workdir2)
        t1 = app1.extensions["bpp"].auth_token
        t2 = app2.extensions["bpp"].auth_token
        assert t1 != t2


class TestAuthTokenEnforcement:
    """API endpoints must reject requests without valid token."""

    def test_index_page_allowed_without_token(self, client):
        """The HTML page itself must be accessible (it contains the token)."""
        resp = client.get("/")
        assert resp.status_code == 200

    def test_static_files_allowed_without_token(self, client):
        """Static assets (JS, CSS) must be accessible without token."""
        resp = client.get("/static/js/globals.js")
        # 200 or 304 — both are fine
        assert resp.status_code in (200, 304)

    def test_api_rejected_without_token(self, client):
        """API calls without token must get 403."""
        resp = client.get("/api/v1/status")
        assert resp.status_code == 403
        data = resp.get_json()
        assert "error" in data

    def test_api_rejected_with_wrong_token(self, client):
        resp = client.get("/api/v1/status", headers={"X-Auth-Token": "wrong-token-value"})
        assert resp.status_code == 403

    def test_api_allowed_with_correct_header(self, client, token):
        resp = client.get("/api/v1/status", headers={"X-Auth-Token": token})
        assert resp.status_code == 200

    def test_api_allowed_with_query_param(self, client, token):
        """SSE endpoints can't send headers, so token via query param must work."""
        resp = client.get(f"/api/v1/status?_token={token}")
        assert resp.status_code == 200

    def test_post_endpoint_requires_token(self, client, token):
        # Without token
        resp = client.put(
            "/api/v1/settings",
            data=json.dumps({"theme": "dark"}),
            content_type="application/json",
        )
        assert resp.status_code == 403

        # With token
        resp = client.put(
            "/api/v1/settings",
            data=json.dumps({"theme": "dark"}),
            content_type="application/json",
            headers={"X-Auth-Token": token},
        )
        assert resp.status_code == 200

    def test_delete_endpoint_requires_token(self, client, token):
        resp = client.delete("/api/v1/analysis-cache")
        assert resp.status_code == 403

        resp = client.delete("/api/v1/analysis-cache", headers={"X-Auth-Token": token})
        # 200 or 404 — just not 403
        assert resp.status_code != 403


class TestTokenInjectedInHTML:
    """The index page must include the token so JS can use it."""

    def test_token_in_index_html(self, client, token):
        resp = client.get("/")
        html = resp.data.decode()
        assert token in html

    def test_token_in_meta_tag(self, client, token):
        resp = client.get("/")
        html = resp.data.decode()
        assert f'<meta name="auth-token" content="{token}"' in html

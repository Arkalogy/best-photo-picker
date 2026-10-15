"""The Dockerfile HEALTHCHECK must probe a path the auth gate lets through.

The probe runs inside the container with no auth token. An earlier
version probed ``/api/v1/health``, which (like every ``/api/*`` route)
requires the per-boot app session token, so it got a 403 on every run
and the container was marked unhealthy forever. These tests read the
probe URL straight out of the Dockerfile and replay it against a real
app with the auth gate ON (``TESTING`` left unset).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlparse

import pytest

DOCKERFILE = Path(__file__).resolve().parent.parent / "Dockerfile"


def _healthcheck_url() -> str:
    text = DOCKERFILE.read_text()
    # Join backslash-continued lines so the HEALTHCHECK instruction is one line.
    joined = text.replace("\\\n", " ")
    lines = [ln for ln in joined.splitlines() if ln.lstrip().startswith("HEALTHCHECK")]
    assert len(lines) == 1, f"expected exactly one HEALTHCHECK instruction, got {lines!r}"
    match = re.search(r"https?://[^'\"\s]+", lines[0])
    assert match, f"no URL in HEALTHCHECK line: {lines[0]!r}"
    return match.group(0)


@pytest.fixture
def gated_client(tmp_path, monkeypatch):
    """App with the real auth gate (TESTING not set) and no trusted proxies."""
    monkeypatch.delenv("BPP_TRUSTED_PROXIES", raising=False)
    from bpp.web.app import create_app

    workdir = tmp_path / "workdir"
    os.makedirs(workdir)
    app = create_app(workdir=str(workdir))
    assert not app.config.get("TESTING")
    return app.test_client()


def test_healthcheck_targets_loopback_port_5001():
    parsed = urlparse(_healthcheck_url())
    assert parsed.hostname in ("127.0.0.1", "localhost")
    assert parsed.port == 5001


def test_healthcheck_path_is_not_behind_the_token_gate():
    path = urlparse(_healthcheck_url()).path
    gated = ("/api/", "/thumb/", "/photo/", "/video/")
    assert not path.startswith(gated), f"{path} requires an auth token; the probe sends none"


def test_healthcheck_path_returns_200_without_token(gated_client):
    path = urlparse(_healthcheck_url()).path
    resp = gated_client.get(path, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert resp.status_code == 200


def test_api_health_rejects_tokenless_probe(gated_client):
    """Why the probe can't use /api/v1/health: no token -> 403."""
    resp = gated_client.get("/api/v1/health", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert resp.status_code == 403

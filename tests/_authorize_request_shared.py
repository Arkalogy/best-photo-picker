"""Shared fixtures and helpers of the test_authorize_request.py suite (split for the
500-LOC cap)."""

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

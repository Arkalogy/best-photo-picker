"""Shared fixtures and helpers of the test_trust_proxy.py suite (split for the 500-LOC cap)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from unittest import mock

import pytest


@dataclass
class FakeRequest:
    path: str = "/"
    remote_addr: str = "172.17.0.1"
    headers: dict[str, str] | None = None
    args: dict[str, str] | None = None
    cookies: dict[str, str] | None = None

    def __post_init__(self):
        self.headers = self.headers or {}
        self.args = self.args or {}
        self.cookies = self.cookies or {}


@pytest.fixture
def ctx(tmp_path):
    """A WebAppState-like context. LAN sharing left at default (off)."""
    from bpp.web.app import create_app

    workdir = str(tmp_path / "workdir")
    os.makedirs(workdir)
    app = create_app(workdir=workdir)
    c = app.extensions["bpp"]
    c._test_app = app
    return c


def _authorize(ctx, req):
    from bpp.web.share import authorize_request

    with ctx._test_app.app_context():
        return authorize_request(req, ctx)


@pytest.fixture(autouse=True)
def _reset_trusted_proxy_env():
    """Each test starts from a clean env; tests opt into specific values.
    Also clear the once-per-startup CIDR warning memo so each test
    can independently observe the warning behavior."""
    from bpp.web.share import _reset_warned_cidrs_for_tests

    _reset_warned_cidrs_for_tests()
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("BPP_TRUSTED_PROXIES", None)
        os.environ.pop("BPP_TRUST_PROXY", None)
        yield
    _reset_warned_cidrs_for_tests()

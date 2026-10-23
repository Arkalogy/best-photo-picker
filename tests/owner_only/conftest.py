"""Fixtures for the owner-only endpoint tests: a real-auth app
(TESTING=False) and a paired LAN device."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _reset_rate_limit_buckets():
    """The destructive-endpoint rate limiter is global module state
    (`bpp.web.share._DESTRUCTIVE_BUCKETS`). Without resetting between
    tests, a parametrized sweep over 60+ POST cases trips the
    60 req/min cap and subsequent tests get 429 instead of the
    expected 403."""
    from bpp.web.share import _reset_pair_request_buckets_for_tests

    _reset_pair_request_buckets_for_tests()
    yield
    _reset_pair_request_buckets_for_tests()


@pytest.fixture
def app(tmp_path):
    """Fresh Flask app with the auth middleware active."""
    from bpp.web.app import create_app

    workdir = str(tmp_path / "workdir")
    os.makedirs(workdir)
    app = create_app(workdir=workdir)
    app.config["TESTING"] = False
    return app


@pytest.fixture
def lan_device(app):
    """Approve a LAN device (PRINCIPAL_LAN_DEVICE) and return
    (share_token, fingerprint_cookie). Caller uses these to make
    requests as that paired-but-not-owner device."""
    from bpp.web.share import (
        approve_device,
        find_or_create_pending_device,
        get_share_token,
        set_lan_sharing_enabled,
    )

    ctx = app.extensions["bpp"]
    with app.app_context():
        set_lan_sharing_enabled(ctx.get_conn(), True)
        token = get_share_token(ctx.get_conn())
        d = find_or_create_pending_device(ctx.get_conn(), "fp-lan-1", "Phone", "192.168.1.50")
        approve_device(ctx.get_conn(), d["id"])
    return token, "fp-lan-1"

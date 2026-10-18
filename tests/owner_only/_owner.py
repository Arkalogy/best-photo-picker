"""Requests as the local owner, for the owner-only endpoint tests."""

from __future__ import annotations


def as_loopback(app, method, path, **kwargs):
    """Make a request as the local owner (LOCAL_APP principal)."""
    ctx = app.extensions["bpp"]
    client = app.test_client()
    headers = kwargs.pop("headers", {}) or {}
    headers["X-Auth-Token"] = ctx.auth_token
    return client.open(
        path,
        method=method,
        headers=headers,
        environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
        **kwargs,
    )


# ─── /api/settings PUT ─────────────────────────────────────────────

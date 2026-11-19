"""The loopback boot key: who may open the owner page on 127.0.0.1."""

from __future__ import annotations

from typing import Any

from flask import request

from bpp.utils.logging import get_logger
from bpp.web.loopback_peer import peer_is_another_account
from bpp.web.share import _token_equals

log = get_logger(__name__)


def presents_boot_key(ctx: Any) -> bool:
    """Whether this loopback request carries the boot secret as the
    ``_boot`` query parameter. Never a cookie: browsers send cookies to
    every port of 127.0.0.1, which would hand the key to any other
    local listener. The page keeps the key in ``sessionStorage`` (scoped
    to the origin, port included) and re-presents it on reload."""
    secret = ctx.boot_secret
    if not secret:
        return True
    if not _token_equals(request.args.get("_boot") or "", secret):
        return False
    # The key can be read off the launch command by other accounts (Linux
    # shows every account the browser launcher's arguments): it counts
    # only on a connection this account made.
    env = request.environ
    if "werkzeug.proxy_fix.orig" in env:
        # Behind the owner's reverse proxy the socket is the proxy's, made
        # by its service account: it says nothing about who is asking.
        return True
    if peer_is_another_account(
        env.get("REMOTE_ADDR"), _port(env.get("REMOTE_PORT")), _port(env.get("SERVER_PORT"))
    ):
        log.warning("Refused the boot key on a connection made by another account on this computer")
        return False
    return True


def _port(value: Any) -> int | None:
    text = str(value or "")
    return int(text) if text.isascii() and text.isdigit() else None

"""The request auth boundary: every request passes
:func:`bpp.web.share.authorize_request`, plus the library binding
(``X-Library-Id``) and the per-principal rate limit on destructive
calls. Split out of ``app.py`` for the 500-line cap.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

from flask import Flask, jsonify, request

from bpp.utils.logging import get_logger

if TYPE_CHECKING:
    from bpp.web.state import WebAppState

log = get_logger(__name__)


def loggable(text: str, limit: int = 200) -> str:
    """Text from a request, safe to put in a log line: control characters
    (line breaks among them) shown escaped, and cut to *limit*. A caller
    must not be able to start a log line of its own wording."""
    shown = "".join(c if c.isprintable() else repr(c)[1:-1] for c in text[: limit + 1])
    return shown if len(text) <= limit else shown[:limit] + "…"


class _RefusalLog:
    """How often refusals from one address are written down: a handful a
    minute. An address that keeps knocking can't push everything else out
    of the log."""

    PER_MINUTE = 5
    MAX_ADDRESSES = 256

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seen: dict[str, list[float]] = {}

    def should_log(self, address: str) -> bool:
        now = time.monotonic()
        with self._lock:
            recent = [t for t in self._seen.get(address, ()) if now - t < 60]
            allowed = len(recent) < self.PER_MINUTE
            if allowed:
                recent.append(now)
            if address not in self._seen and len(self._seen) >= self.MAX_ADDRESSES:
                self._seen.pop(next(iter(self._seen)))
            self._seen[address] = recent
            return allowed


_refusals = _RefusalLog()


def _install_auth_boundary(app: Flask, ctx: WebAppState) -> None:
    """Auth boundary: delegates to ``bpp.web.share.authorize_request()``.

    Splitting the policy out makes it unit-testable, swappable for
    OAuth/JWT in the future, and easier to reason about. See
    docs/security.md for the trust model and threat assumptions.
    """

    @app.before_request
    def _check_auth_token():
        # A page echoes the id of the library it was rendered for. After a
        # library switch, a tab still showing the old library must not
        # delete or edit rows that happen to share ids in the new one.
        sent = request.headers.get("X-Library-Id")
        if (
            sent
            and sent != getattr(ctx, "library_id", sent)
            and request.method in ("POST", "PUT", "DELETE", "PATCH")
            and request.path.startswith("/api/")
        ):
            return jsonify(
                error="This tab shows a different library than the app is using now; "
                "reload the page",
                code="library_changed",
            ), 409
        if app.config.get("TESTING"):
            # Even in TESTING (which bypasses auth), seed g.bpp_principal
            # with LOCAL_APP so endpoints that gate on principal.kind
            # (owner-only routes like device approve/revoke) can run in
            # tests using the default Flask test_client (loopback).
            from flask import g

            from bpp.web.share import PRINCIPAL_LOCAL_APP, Principal

            g.bpp_principal = Principal(kind=PRINCIPAL_LOCAL_APP)
            return None

        from bpp.web.share import (
            PRINCIPAL_LAN_DEVICE,
            AuthDecision,
            authorize_request,
            record_share_access,
        )

        # Before the check: the answer must come from the library the
        # device was checked against (see _same_library_as_approved).
        library_at_auth = getattr(ctx, "library_id", None)
        result = authorize_request(request, ctx)
        # Stash on flask.g so blueprint handlers (and any future
        # logging / per-resource scopes) can reach the principal that
        # authenticated this request without re-running the auth check.
        from flask import g

        g.bpp_principal = result.principal
        g.bpp_library_id = library_at_auth

        if result.decision == AuthDecision.ALLOW:
            # Best-effort audit trail for share-token authentications.
            # The LAN_DEVICE principal is exactly what we want to log —
            # no string matching on tokens, just check the principal kind.
            principal = result.principal
            if principal is not None and principal.kind == PRINCIPAL_LAN_DEVICE:
                record_share_access(
                    ctx.get_conn(),
                    ip=request.remote_addr or "",
                    user_agent=request.headers.get("User-Agent", ""),
                    fingerprint=principal.fingerprint,
                )
            # Per-principal rate limit on destructive /api/ endpoints.
            # Defends against runaway-loop clients + a hostile paired
            # device slamming permanent-delete / analyze. 60 req/minute
            # burst, 60 steady. Read-only GETs aren't gated — those
            # are mostly served from cache.
            if request.method in ("POST", "PUT", "DELETE", "PATCH") and request.path.startswith(
                "/api/"
            ):
                from bpp.web.e2e_fixture import is_e2e_fixture_library
                from bpp.web.share import consume_destructive_token

                # The Playwright suite fires far more mutations/min than a
                # human, draining the 60/min bucket and 429-ing later
                # mutating specs. Bypass only for sentinel-marked e2e
                # fixture libraries — a real user library never carries
                # the sentinel, so production limits are untouched.
                key = (
                    principal.fingerprint
                    if principal is not None and principal.fingerprint
                    else (principal.kind if principal is not None else "anonymous")
                )
                if key == "anonymous":
                    # Per address: one host can't spend the allowance every
                    # other unpaired device needs to ask for access.
                    key = f"anonymous:{request.remote_addr or ''}"
                if not is_e2e_fixture_library() and not consume_destructive_token(key):
                    return jsonify(error="Rate limited"), 429
            return None

        if result.decision == AuthDecision.PAIR_REQUIRED:
            # Phone has a valid share token but isn't trusted yet. For
            # API requests, signal this so the JS knows to redirect the
            # phone to the pairing page. For HTML, the index is exempt
            # so the phone naturally lands on the pair UI on next nav.
            return jsonify(
                error="Pairing required",
                pair_required=True,
            ), 403

        # Auth-rejected log level: when the request is from localhost
        # (the owner's browser hitting an API before the SPA has
        # wired up the auth-token meta tag — common on every fresh
        # page load), demote to DEBUG. Auth rejections from a remote
        # IP stay at WARNING since those are genuine signals (paired
        # device with bad token, attacker probing the LAN).
        from bpp.web.share import is_loopback_address

        is_localhost = is_loopback_address(request.remote_addr)
        if is_localhost:
            log.debug("Auth rejected: %s %s", request.remote_addr, loggable(request.path))
        elif _refusals.should_log(request.remote_addr or ""):
            log.warning("Auth rejected: %s %s", request.remote_addr, loggable(request.path))
        return jsonify(error="Forbidden"), 403

    @app.after_request
    def _same_library_as_approved(response):
        """A device is approved per library. If the owner switched
        library while its request was being answered, the answer was
        built from the new one: don't send it."""
        from flask import g

        from bpp.web.share import PRINCIPAL_LAN_DEVICE

        principal = getattr(g, "bpp_principal", None)
        if (
            principal is not None
            and principal.kind == PRINCIPAL_LAN_DEVICE
            and getattr(g, "bpp_library_id", None) != getattr(ctx, "library_id", None)
        ):
            # The same response object, emptied: the hooks that already ran
            # (security headers) keep their work, the body and anything
            # that would let it be cached do not.
            response.direct_passthrough = False
            response.set_data(
                '{"error": "The library changed; reload the page", "code": "library_changed"}'
            )
            response.status_code = 409
            response.mimetype = "application/json"
            for header in ("ETag", "Last-Modified", "Expires", "Content-Disposition"):
                response.headers.pop(header, None)
            response.headers["Cache-Control"] = "no-store"
        return response

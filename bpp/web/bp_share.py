"""Share blueprint: LAN sharing setup, device pairing, QR rendering.

Lives in its own ~230-line file matching the public API shape,
keeping the share/pairing surface separate from status / settings
/ install handlers.

The owner-only endpoints (toggle, revoke, devices/approve,
devices/revoke) use the @requires_local_app decorator from
bpp.web.share.
"""

from __future__ import annotations

from flask import Blueprint, Response, jsonify, request

from bpp.errors import BppError, NotFoundError, ValidationError
from bpp.web.share import requires_local_app
from bpp.web.state import get_ctx

bp = Blueprint("share", __name__)


@bp.get("/api/v1/share/info")
@requires_local_app
def api_share_info() -> tuple[Response, int]:
    """LAN sharing info: enabled flag (DB-persisted) + share URL.

    The share URL embeds the *persistent* share token (not the app
    session token), so URLs handed to phones survive server restarts.

    L6: the response body contains the share token as part of the URL
    query string. This is *necessary* for the URL to function when
    pasted/scanned, but we set ``Cache-Control: no-store`` so the
    response isn't cached by any intermediary or shown in browser back-
    forward state. The bare token is *not* returned as a separate field.
    """
    from bpp.web.share import (
        detect_lan_ip,
        get_lan_share_url,
        get_share_token,
        is_lan_sharing_enabled,
        recent_share_access,
    )

    ctx = get_ctx()
    conn = ctx.get_conn()
    lan_ip = detect_lan_ip()
    enabled = bool(is_lan_sharing_enabled(conn) and lan_ip)
    share_url = get_lan_share_url(ctx.port, get_share_token(conn), ip=lan_ip) if enabled else None
    resp = jsonify(
        {
            "enabled": enabled,
            "lan_ip": lan_ip,
            "port": ctx.port,
            "share_url": share_url,
            "recent_access": recent_share_access(conn, limit=10),
        }
    )
    resp.headers["Cache-Control"] = "no-store"
    return resp, 200


@bp.post("/api/v1/share/toggle")
@requires_local_app
def api_share_toggle() -> tuple[Response, int]:
    """Flip LAN sharing on/off. Persists across restarts.

    LOCAL_APP-only — a paired LAN device must NOT be able to disable
    its own access channel (or worse, enable LAN sharing for an
    owner who never opted in).

    refuses to ENABLE sharing while the server is bound
    loopback-only. `--host` defaults to `127.0.0.1` when
    sharing is off; flipping sharing ON via this endpoint at
    runtime would persist the flag and surface a LAN URL in the
    UI, but the bind address is fixed at startup so no phone could
    actually connect. Returning 409 forces the user to restart the
    server (which will then resolve the host via the now-enabled
    flag, or via an explicit `--host=0.0.0.0`).

    Disabling sharing is always allowed — the disable path doesn't
    care about the bind host.
    """
    from bpp.web.share import set_lan_sharing_enabled

    body = request.get_json(silent=True) or {}
    if "enabled" not in body:
        raise ValidationError("missing 'enabled' field", field="enabled")
    enabled = bool(body["enabled"])
    ctx = get_ctx()

    # Loopback-only bind + enable request = restart-required 409.
    # The check is by-string-prefix because the bind can be `127.0.0.1`,
    # `::1`, or `localhost`. WebAppState defaults
    # `bound_host = "127.0.0.1"` (fail-closed sentinel), so this
    # branch fires unconditionally when enabling — no `is not None`
    # fallthrough that would silently bypass the gate. Lowercase the
    # value before comparing so `--host=LOCALHOST` still trips it.
    if enabled:
        loopback_prefixes = ("127.", "::1", "localhost")
        if ctx.bound_host.lower().startswith(loopback_prefixes):
            return jsonify(
                {
                    "error": (
                        "Restart required to enable LAN sharing — server "
                        f"is bound to {ctx.bound_host} (loopback only). "
                        "Restart with `--host=0.0.0.0` or restart with "
                        "sharing already enabled in the database."
                    ),
                    "restart_required": True,
                }
            ), 409

    set_lan_sharing_enabled(ctx.get_conn(), enabled)
    return jsonify({"enabled": enabled}), 200


@bp.post("/api/v1/share/revoke")
@requires_local_app
def api_share_revoke() -> tuple[Response, int]:
    """Rotate the share token. All existing share URLs become invalid.

    Returns the fresh share URL so the UI can re-render in place.

    LOCAL_APP-only — a paired LAN device rotating the share token
    would invalidate every other paired device's bookmark, including
    the owner's other phone. That's a denial-of-service / lock-out
    vector indistinguishable from device approve/revoke.
    """
    from bpp.web.share import (
        detect_lan_ip,
        get_lan_share_url,
        is_lan_sharing_enabled,
        regenerate_share_token,
    )

    ctx = get_ctx()
    conn = ctx.get_conn()
    new_token = regenerate_share_token(conn)
    lan_ip = detect_lan_ip()
    share_url = (
        get_lan_share_url(ctx.port, new_token, ip=lan_ip)
        if (is_lan_sharing_enabled(conn) and lan_ip)
        else None
    )
    resp = jsonify({"share_url": share_url})
    resp.headers["Cache-Control"] = "no-store"  # L6: token embedded in URL
    return resp, 200


@bp.get("/api/v1/share/pair/status")
def api_share_pair_status() -> tuple[Response, int]:
    """Phone polls this while pairing. Reports its own state.

    States:
      - unknown: no fingerprint cookie or no row for it (phone should
        scan QR or reload)
      - pending: row exists, owner hasn't approved yet
      - trusted: approved — phone reloads to full SPA
      - revoked: owner blocked this device — terminal state until
        cookies are cleared and the user re-scans
    """
    from bpp.web.share import FP_COOKIE_NAME, get_device_by_fingerprint

    ctx = get_ctx()
    fp = request.cookies.get(FP_COOKIE_NAME)
    if not fp:
        return jsonify({"state": "unknown"}), 200
    device = get_device_by_fingerprint(ctx.get_conn(), fp)
    if device is None:
        return jsonify({"state": "unknown"}), 200
    if device["revoked_at"] is not None:
        return jsonify({"state": "revoked"}), 200
    if device["trusted_at"] is not None:
        return jsonify({"state": "trusted"}), 200
    return jsonify({"state": "pending"}), 200


@bp.post("/api/v1/share/pair/request")
def api_share_pair_request() -> tuple[Response, int]:
    """Phone explicitly asks for access again after a revoke.

    Allowed for any LAN client with a fingerprint cookie (see
    authorize_request). Flips a revoked row back to pending; idempotent
    on already-pending or already-trusted rows. Returns the resulting
    state so the phone JS can transition the pair page.

    Rate-limited per-IP (10/min) to defend against flooding the owner's
    pending-requests list with random fingerprints.
    """
    from bpp.web.share import (
        FP_COOKIE_NAME,
        consume_pair_request_token,
        request_access,
    )

    ip = request.remote_addr or ""
    if not consume_pair_request_token(ip):
        exc = BppError("Too many requests", code="rate_limited")
        exc.http_status = 429  # type: ignore[misc]
        raise exc

    ctx = get_ctx()
    fp = request.cookies.get(FP_COOKIE_NAME)
    if not fp:
        raise ValidationError("no fingerprint cookie", field="fingerprint")
    result = request_access(ctx.get_conn(), fp)
    if result is None:
        raise NotFoundError("unknown device")
    if result["revoked_at"] is not None:
        state = "revoked"
    elif result["trusted_at"] is not None:
        state = "trusted"
    else:
        state = "pending"
    return jsonify({"state": state}), 200


@bp.get("/api/v1/share/devices")
@requires_local_app
def api_share_devices() -> tuple[Response, int]:
    """Mac UI: list devices for the Settings → Share Devices section.

    LOCAL_APP-only — a paired LAN device should not be able to
    enumerate the full device roster (other paired phones / pending
    requests). Approve and revoke are already owner-only; listing
    consistently joins them. A LAN device that needs to know its
    own pairing state uses /api/v1/share/pair/status, which returns
    only its own row.
    """
    from bpp.web.share import list_devices

    ctx = get_ctx()
    return jsonify(list_devices(ctx.get_conn())), 200


@bp.post("/api/v1/share/devices/<int:device_id>/approve")
@requires_local_app
def api_share_devices_approve(device_id: int) -> tuple[Response, int]:
    """Owner approves a pending device. One click — no code typing.

    The existence check + UPDATE happen atomically inside `approve_device`
    (BEGIN IMMEDIATE) so a concurrent revoke can't race past us.

    LOCAL_APP-only: the owner authorizes pairings from the Mac UI, not
    the phone. Without this gate a paired phone could approve other
    pending devices to expand its LAN influence.
    """
    from bpp.web.share import approve_device

    ctx = get_ctx()
    if not approve_device(ctx.get_conn(), device_id):
        raise NotFoundError("Unknown device", device_id=device_id)
    return jsonify({"ok": True, "id": device_id}), 200


@bp.post("/api/v1/share/devices/<int:device_id>/revoke")
@requires_local_app
def api_share_devices_revoke(device_id: int) -> tuple[Response, int]:
    """Owner revokes a device. Effective immediately — next request 403s.

    Atomic — see approve. Concurrent approve+revoke can no longer race
    each other to a half-applied state.

    LOCAL_APP-only: same threat model as approve — a paired phone
    revoking siblings is a denial-of-service / lock-out vector.
    """
    from bpp.web.share import revoke_device

    ctx = get_ctx()
    if not revoke_device(ctx.get_conn(), device_id):
        raise NotFoundError("Unknown device", device_id=device_id)
    return jsonify({"ok": True, "id": device_id}), 200


@bp.get("/api/v1/share/qr")
@requires_local_app
def api_share_qr() -> tuple[Response, int]:
    """Branded PNG QR code for the LAN share URL. 404 when sharing is off.

    Rendered with BPP-blue rounded modules + embedded logo. PNG (not
    SVG) because the embedded logo is a raster — see render_qr_png for
    the rationale.
    """
    from bpp.web.share import (
        detect_lan_ip,
        get_lan_share_url,
        get_share_token,
        is_lan_sharing_enabled,
        render_qr_png,
    )

    ctx = get_ctx()
    conn = ctx.get_conn()
    lan_ip = detect_lan_ip()
    if not (is_lan_sharing_enabled(conn) and lan_ip):
        raise NotFoundError("LAN sharing not enabled")
    share_url = get_lan_share_url(ctx.port, get_share_token(conn), ip=lan_ip)
    png = render_qr_png(share_url)
    resp = Response(png, mimetype="image/png")
    # The QR encodes the live share URL with the long-lived `_token=…`
    # query param. Without no-store, browser/proxy disk caches and the
    # back-forward cache can persist the bytes — a cached QR is still
    # valid (and re-scannable) long after the token would otherwise
    # have rotated. Mirrors the no-store on /api/v1/share/info.
    resp.headers["Cache-Control"] = "no-store"
    return resp, 200

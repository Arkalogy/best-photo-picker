"""LAN sharing helpers — detect local network IP, build shareable URL.

LAN sharing is gated on the persisted `lan_sharing_enabled` setting
(toggled from Settings → Share in the app). When the toggle is on AND
the server was started with `--host 0.0.0.0` (or the equivalent in
Docker / `bpp serve --host`), non-loopback clients can reach the SPA
after pairing through the QR-code flow. The Settings → Share tab
renders a QR with the URL + share token embedded so phones can scan
instead of typing.

(Historical note: there was never a `bpp serve --lan` flag — LAN
binding has always been controlled by `--host` plus the in-app
toggle.)
"""

from __future__ import annotations

import hmac

from bpp.utils.logging import get_logger

log = get_logger(__name__)

# DB settings keys for LAN sharing state. Stored as strings (the
# settings table is generic key/value).


# ──────────────────────────────────────────────────────────────────
# Auth policy: a single function the middleware delegates to. Splitting
# the policy from the request-handling plumbing means it's unit-testable
# and OSS contributors can swap in OAuth/JWT/LDAP without re-doing the
# Flask integration.
# ──────────────────────────────────────────────────────────────────
import enum  # noqa: E402
import re  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402

from bpp.web.share_token import (  # noqa: E402, F401 - re-exported
    _propagate_token_rotation_to_backups,
    get_share_token,
    regenerate_share_token,
)

PAIR_STATUS_PATH = "/api/v1/share/pair/status"
PAIR_REQUEST_PATH = "/api/v1/share/pair/request"
FP_COOKIE_NAME = "bpp_share_fp"
FP_COOKIE_MAX_AGE = 365 * 24 * 3600  # 1 year


_DEVICE_NAME_ALLOWED = re.compile(r"[^\w\s\-./]", re.ASCII)


def _sanitize_device_name(name: str) -> str:
    """Restrict a device-name candidate to a safe printable subset.

    Strips characters outside `[A-Za-z0-9_ \\-./]`, collapses extra
    whitespace, caps at 40 chars, and falls back to "Unknown device"
    for empty/all-stripped input. Defense in depth — the Settings UI
    already renders names through `esc()`, but storing only safe
    bytes means a future renderer that forgets to escape can't be
    blamed for XSS.
    """
    if not name:
        return "Unknown device"
    cleaned = _DEVICE_NAME_ALLOWED.sub("", name)
    cleaned = " ".join(cleaned.split())  # collapse whitespace runs
    cleaned = cleaned[:40].strip()
    return cleaned or "Unknown device"


# UA → friendly name registry. Adding a new device class = one tuple.
# Order matters — first match wins, so keep the most specific UA tokens
# first (e.g., iPad before Android, since some Android tablets ship
# with "iPad" in the UA via desktop-mode toggling).
_UA_DEVICE_NAMES: tuple[tuple[str, str], ...] = (
    ("iPhone", "iPhone"),
    ("iPad", "iPad"),
    ("Android", "Android"),
    ("Macintosh", "Mac"),
    ("Windows", "Windows"),
    ("Linux", "Linux"),
)


def _device_name_from_ua(ua: str) -> str:
    """Best-effort friendly name for the Mac UI: 'iPhone', 'Mac', etc.

    Substring-match the registry above and return the hand-curated
    label. Unrecognized UAs fall through to a sanitized 40-char
    prefix of the raw header (see `_sanitize_device_name`).
    """
    if not ua:
        return "Unknown device"
    for needle, label in _UA_DEVICE_NAMES:
        if needle in ua:
            return label
    return _sanitize_device_name(ua)


# ── Owner-only endpoint guard ──────────────────────────────────────


def _require_local_app() -> tuple[object, int] | None:
    """Block non-loopback / non-app-token requests. Used on owner-only
    endpoints (device approve/revoke) so a paired phone on the LAN
    can't escalate by approving itself or revoking siblings.

    Prefer the `@requires_local_app` decorator below for new endpoints
    — it puts the requirement on a line above the route declaration
    where it's visually unmissable. This bare predicate is kept for
    flows that need to inspect the result (e.g., conditional logic
    branching on whether the caller is the owner)."""
    from flask import g, jsonify

    principal = getattr(g, "bpp_principal", None)
    if principal is None or principal.kind != PRINCIPAL_LOCAL_APP:
        return jsonify({"error": "Owner-only endpoint", "code": "owner_only"}), 403
    return None


def principal_is_local_app() -> bool:
    """Return True if the request principal is LOCAL_APP.

    Filter-style predicate: use this when an endpoint wants to
    *include or strip* a sensitive field rather than reject the
    whole request. For routes that should
    return 403 to non-owner callers, use the `@requires_local_app`
    decorator instead — same security guarantee, more visible.

    lifted from `bpp.web.bp_core` to `share.py` so blueprints
    that use it (`bp_core`, `bp_health`, `bp_analysis`) don't have
    to import a private predicate from a sibling blueprint —
    `_principal_is_local_app` belongs next to `PRINCIPAL_LOCAL_APP`,
    not buried in any one route file."""
    from flask import g

    principal = getattr(g, "bpp_principal", None)
    return principal is not None and principal.kind == PRINCIPAL_LOCAL_APP


def requires_local_app(view_func):
    """Decorator: 403 unless the request principal is LOCAL_APP.

    Replaces the three-line inline guard:

        blocked = _require_local_app()
        if blocked is not None:
            return blocked

    with a single line above the route:

        @bp.post("/api/v1/whatever")
        @requires_local_app
        def handler(...):
            ...

    The decorator sits BELOW the @bp.* route decorator so Flask
    registers the wrapped function. Visually unmissable in a code
    review, and impossible to forget the `if blocked is not None`
    branch (which an inline call's caller could omit and silently
    leak the endpoint to LAN devices).
    """
    from functools import wraps

    @wraps(view_func)
    def wrapper(*args, **kwargs):
        blocked = _require_local_app()
        if blocked is not None:
            return blocked
        return view_func(*args, **kwargs)

    return wrapper


class AuthDecision(enum.Enum):
    ALLOW = "allow"
    DENY = "deny"
    PAIR_REQUIRED = "pair_required"  # phone must complete pairing flow first


# ──────────────────────────────────────────────────────────────────
# Principal: who authenticated. Today there's one user, but the auth
# layer already speaks two schemes (local app session vs LAN share
# token + paired device). Naming them as principals lets future
# multi-user / OAuth / API-key flows slot into the same shape:
#
#   Principal(kind="user", user_id=42, scopes=("photo:read",))
#
# without rewriting `authorize_request`'s signature. AuthResult bundles
# the decision with the principal so callers can also do scoped logic
# ("log who saw this", "track per-device access", "scope a query") —
# the seam exists, even though no caller uses it yet.
# ──────────────────────────────────────────────────────────────────

# String constants for the kind field. Adding a new kind is a constant +
# a branch in the function below; no caller needs to change.
PRINCIPAL_LOCAL_APP = "local_app"  # Tauri webview / loopback with app token
PRINCIPAL_LAN_DEVICE = "lan_device"  # Phone via share token + trusted fingerprint
PRINCIPAL_ANONYMOUS = "anonymous"  # Static / index / pair-status / pair-request


@dataclass(frozen=True)
class Principal:
    """Who is making this request.

    `kind` is the discriminator (see PRINCIPAL_* constants). Other
    fields are kind-specific and may be None.

    Forward-compat fields (`user_id`, `scopes`) are reserved but not
    populated today — adding a v2 user system means filling them in
    for the new principal kind, not changing the dataclass shape.
    """

    kind: str
    fingerprint: str | None = None  # populated for LAN_DEVICE
    user_id: int | None = None  # reserved for future multi-user
    scopes: tuple[str, ...] = field(default_factory=tuple)  # reserved for future


def _token_equals(candidate: str, expected: str | None) -> bool:
    """constant-time token comparison.

    Returns True iff `candidate` matches `expected` AND `expected` is
    truthy. The truthy guard matters — a request without a configured
    app/share token must NOT auth as that principal just because the
    candidate is also empty. `hmac.compare_digest` requires equal-
    length inputs and resists timing leaks across the bytes that ARE
    compared.

    Encode to bytes first: hmac.compare_digest raises TypeError on
    non-ASCII strings (Python 3.11+), and an attacker sending a non-
    ASCII candidate must not crash auth — it must just return False.
    """
    if not expected:
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


@dataclass(frozen=True)
class AuthResult:
    """The output of `authorize_request`.

    `decision` is what the middleware acts on (ALLOW / DENY /
    PAIR_REQUIRED). `principal` is populated only when `decision ==
    ALLOW`; it identifies *who* the caller is, for downstream logging,
    auditing, or scoped authorization. Callers that don't care about
    the principal can just check `result.decision`.
    """

    decision: AuthDecision
    principal: Principal | None = None


def is_loopback_address(remote: str | None) -> bool:
    """Whether *remote* is an address of this computer (127.0.0.0/8 or
    ::1). Parsed, not matched as text: behind a reverse proxy the value
    comes from a header, and "127.0.0.1.example" is not this computer."""
    import ipaddress

    try:
        addr = ipaddress.ip_address((remote or "").split("%", 1)[0])
    except ValueError:
        return False
    mapped = getattr(addr, "ipv4_mapped", None)
    return (mapped or addr).is_loopback


def _check_sharing_state_again(ctx: object, conn: object, library: object) -> None:
    """The open-time check could not be made (the key file was not
    readable). Once it is, make it, so sharing works again without a
    restart. *library* is ``ctx.library_id`` as it was before *conn* was
    taken: the result counts only if that is still the open library, so
    a switch meanwhile leaves the new one to its own start-up check."""
    import contextlib

    from bpp.web import share_seal, share_seal_store

    if share_seal_store.unreadable():
        return
    checked = share_seal.check_on_open(conn) != share_seal.UNCHECKED  # type: ignore[arg-type]
    with getattr(ctx, "lock", None) or contextlib.nullcontext():
        still_waiting = getattr(ctx, "share_check_waits_for_key", False)
        if still_waiting and getattr(ctx, "library_id", None) == library:
            ctx.share_state_checked = checked  # type: ignore[attr-defined]
            ctx.share_check_waits_for_key = not checked  # type: ignore[attr-defined]


def check_sharing_state_if_waiting(ctx: object) -> None:
    """For the owner's own Share requests: same recheck as a device's."""
    if getattr(ctx, "share_check_waits_for_key", False):
        library = getattr(ctx, "library_id", None)
        _check_sharing_state_again(ctx, ctx.get_conn(), library)  # type: ignore[attr-defined]


def authorize_request(request: object, ctx: object) -> AuthResult:
    """Single-source-of-truth auth check.

    Returns an `AuthResult` carrying the decision (ALLOW / DENY /
    PAIR_REQUIRED) and, for ALLOW, the `Principal` that authenticated.

    Decision tree:
      1. LAN sharing toggle off → DENY for non-loopback (closes the
         entire surface).
      2. Static + index → ALLOW as anonymous (the index page itself
         ships the token; static assets are public).
      3. /api/share/pair/{status,request} from LAN → ALLOW as anonymous
         (untrusted phones need these to complete pairing).
      4. /api/* → token must match app session OR share token; share
         token from LAN additionally requires a trusted fingerprint.
      5. Other paths → ALLOW as anonymous (blueprint extensions etc.).

    Note on `request`: typed as `object` so callers can pass a Flask
    Request OR a test fake; we only access `path`, `remote_addr`,
    `headers`, `args`, `cookies`.
    """
    path: str = getattr(request, "path", "") or ""
    remote: str = getattr(request, "remote_addr", "") or ""
    headers = getattr(request, "headers", {}) or {}
    args = getattr(request, "args", {}) or {}
    cookies = getattr(request, "cookies", {}) or {}

    is_loopback = is_loopback_address(remote) or _is_trusted_peer(remote)
    library = getattr(ctx, "library_id", None)  # before the connection: see the recheck
    conn = ctx.get_conn()  # type: ignore[attr-defined]

    anonymous = Principal(kind=PRINCIPAL_ANONYMOUS)

    # LAN gate first. During a library switch the new library's sharing
    # state is not ours until share_seal has checked it.
    if not is_loopback and getattr(ctx, "share_check_waits_for_key", False):
        _check_sharing_state_again(ctx, conn, library)
    if not is_loopback and not (
        getattr(ctx, "share_state_checked", True) and is_lan_sharing_enabled(conn)
    ):
        return AuthResult(AuthDecision.DENY)

    # Static + index — anonymous
    if path == "/" or path.startswith("/static/"):
        return AuthResult(AuthDecision.ALLOW, anonymous)

    # Pair status / request: only from LAN, no token / no trust required
    if path in (PAIR_STATUS_PATH, PAIR_REQUEST_PATH) and not is_loopback:
        return AuthResult(AuthDecision.ALLOW, anonymous)

    # Token check applies to /api/ AND raw-media paths (/thumb, /photo,
    # /video) regardless of loopback. Even the local Tauri webview sends
    # the app session token; tightening this prevents a loopback CSRF
    # (a malicious local app on the host talking to localhost:5001) from
    # grabbing data. Without the media-route gate, any LAN client that
    # learns or guesses a content-addressed `path_hash` can pull the
    # original photo bytes — same protection as `/api/`.
    if path.startswith(("/api/", "/thumb/", "/photo/", "/video/")):
        token = headers.get("X-Auth-Token") or args.get("_token") or ""
        app_token = getattr(ctx, "auth_token", None)
        share_token = get_share_token(conn)
        # constant-time compare. With 256-bit tokens the
        # practical risk of a timing attack is small, but cheap to
        # close — and removes the primitive-level review flag. Both
        # candidate tokens are checked through hmac.compare_digest
        # so the auth decision doesn't short-circuit on the first
        # mismatched byte.
        is_app = _token_equals(token, app_token)
        is_share = _token_equals(token, share_token)
        if not is_app and not is_share:
            return AuthResult(AuthDecision.DENY)
        if is_app:
            return AuthResult(AuthDecision.ALLOW, Principal(kind=PRINCIPAL_LOCAL_APP))
        # The share token is a device's credential wherever the request
        # comes from: every invited device has it, and other accounts on
        # this computer can reach loopback. Only the app token (behind
        # the boot key) makes the owner. So: a paired, trusted device.
        fp = cookies.get(FP_COOKIE_NAME)
        if not fp:
            return AuthResult(AuthDecision.PAIR_REQUIRED)
        device = get_device_by_fingerprint(conn, fp)
        if device is None:
            return AuthResult(AuthDecision.PAIR_REQUIRED)
        if device["revoked_at"] is not None:
            return AuthResult(AuthDecision.DENY)
        if device["trusted_at"] is None:
            return AuthResult(AuthDecision.PAIR_REQUIRED)
        return AuthResult(
            AuthDecision.ALLOW,
            Principal(kind=PRINCIPAL_LAN_DEVICE, fingerprint=fp),
        )

    # Non-/api/ non-static non-index — anonymous.
    return AuthResult(AuthDecision.ALLOW, anonymous)


# Rate limiting, access log, and LAN URL helpers moved to
# bpp.web.share_runtime. Re-exported here so existing callers keep
# working — bp_share, bp_core, bp_logs, the auth middleware in
# bpp/web/app.py, and tests all still import from bpp.web.share.
# Trusted-proxy CIDR helpers moved to bpp.web.share_proxy. Re-exported
# here because authorize_request (still in this module) calls
# _is_trusted_peer, create_app calls warn_if_legacy_trust_proxy,
# tests reach _reset_warned_cidrs_for_tests + _trusted_peer_networks,
# and the env-var constants are documented as the public knobs.
# Paired-device CRUD moved to bpp.web.share_devices. Re-exported
# here so existing imports keep working — bp_share, bp_core,
# bp_analysis, and test_share_devices all pull these from
# bpp.web.share.
from bpp.web.share_devices import (  # noqa: E402, F401
    _row_to_device,
    approve_device,
    find_or_create_pending_device,
    get_device_by_fingerprint,
    is_device_trusted,
    list_devices,
    prune_expired_pending,
    request_access,
    revoke_device,
)
from bpp.web.share_proxy import (  # noqa: E402, F401
    _CGNAT_BLOCK,
    _LEGACY_TRUST_PROXY_ENV,
    _TRUSTED_PROXIES_ENV,
    _is_safe_proxy_network,
    _is_trusted_peer,
    _reset_warned_cidrs_for_tests,
    _trusted_peer_networks,
    _warn_bad_cidr_once,
    warn_if_legacy_trust_proxy,
)

# QR rendering + share banner moved to bpp.web.share_qr. Re-exported
# here for backwards compatibility — bp_share, do_serve, and tests
# still import these names from bpp.web.share.
from bpp.web.share_qr import (  # noqa: E402, F401
    _render_bpp_glyph,
    format_share_banner,
    render_qr_png,
    render_qr_svg,
)
from bpp.web.share_runtime import (  # noqa: E402, F401
    _reset_pair_request_buckets_for_tests,
    consume_destructive_token,
    consume_pair_request_token,
    detect_lan_ip,
    get_lan_share_url,
    is_lan_sharing_enabled,
    recent_share_access,
    record_share_access,
    set_lan_sharing_enabled,
)

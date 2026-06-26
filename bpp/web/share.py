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
import os
import secrets
import sqlite3

from bpp.utils.logging import get_logger

log = get_logger(__name__)

# DB settings keys for LAN sharing state. Stored as strings (the
# settings table is generic key/value).
_KEY_SHARE_TOKEN = "lan_share_token"
_KEY_LAN_ENABLED = "lan_sharing_enabled"


def get_share_token(conn: sqlite3.Connection) -> str:
    """Return the persisted LAN share token, creating one on first use.

    Distinct from `WebAppState.auth_token` (which rotates on every
    server boot) — the share token must survive restarts so that share
    URLs handed to family members keep working.
    """
    from bpp.db.settings import get_setting, set_setting

    token = get_setting(conn, _KEY_SHARE_TOKEN)
    if token:
        return token
    token = secrets.token_hex(32)  # 256-bit
    set_setting(conn, _KEY_SHARE_TOKEN, token)
    log.info(
        "LAN share token created on first use — old share URLs "
        "(none yet) will not work; new ones use this token."
    )
    return token


def _propagate_token_rotation_to_backups(db_path: str, new_token: str) -> None:
    """Rewrite the rotated share token into ``.backup`` and
    ``.backup.prev`` so a "user revoked because they suspect compromise"
    flow doesn't leave the old token sitting in a sibling file.

    The startup ``backup_db()`` snapshot is meant to be a recovery
    artifact for schema migration failures — but until this fix, it
    captured every value in ``settings`` at boot time, including the
    LAN share token. After a user clicks "Revoke link" expecting the
    old token to be gone, ``.backup`` (mode 0644 like the live DB
    until ``_restrict_db_perms`` chmods it) still held the
    compromised credential, readable by Time Machine, iCloud sync,
    Dropbox auto-backup, support bundles, etc.

    Best-effort: a failure here doesn't undo the rotation in the live
    DB. Worst case the user has to manually delete the backup files —
    which is the previous behaviour for the same scenario, so the
    failure mode is no worse than what we ship today. Logged at
    WARNING so an operator can surface the gap.
    """
    if not db_path:
        return
    from bpp.db.settings import set_setting

    for suffix in (".backup", ".backup.prev"):
        backup_path = db_path + suffix
        if not os.path.isfile(backup_path):
            continue
        try:
            from bpp.db.connection import get_db

            backup_conn = get_db(backup_path)
            set_setting(backup_conn, _KEY_SHARE_TOKEN, new_token)
            backup_conn.commit()
        except Exception:
            log.warning(
                "Failed to propagate rotated share token to %s — old token "
                "may still be readable in this backup file. Delete the "
                "backup manually if compromise is suspected.",
                backup_path,
                exc_info=True,
            )


def regenerate_share_token(conn: sqlite3.Connection) -> str:
    """Rotate the share token. Existing share URLs become invalid.

    User-driven: invoked from the "Revoke link" button in Settings →
    Share when a leaked URL needs to be killed.

    Also propagates the new token into ``.backup`` and ``.backup.prev``
    so the old (presumed-compromised) token doesn't outlive the
    rotation in the on-disk recovery snapshots — see
    ``_propagate_token_rotation_to_backups``.
    """
    from bpp.db.dialect import dialect
    from bpp.db.settings import set_setting

    token = secrets.token_hex(32)
    set_setting(conn, _KEY_SHARE_TOKEN, token)
    # Audit trail: a support case "my share link stopped working"
    # leaves a clear log breadcrumb showing user-driven rotation.
    log.info("LAN share token rotated — all existing share URLs invalidated.")

    db_path = dialect.database_path(conn)
    if db_path:
        _propagate_token_rotation_to_backups(db_path, token)
    return token


# ──────────────────────────────────────────────────────────────────
# Auth policy: a single function the middleware delegates to. Splitting
# the policy from the request-handling plumbing means it's unit-testable
# and OSS contributors can swap in OAuth/JWT/LDAP without re-doing the
# Flask integration.
# ──────────────────────────────────────────────────────────────────


import enum  # noqa: E402
import re  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402

LOOPBACK_ADDRS = frozenset({"127.0.0.1", "::1", "localhost"})
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
        return jsonify({"error": "Owner-only endpoint"}), 403
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

    is_loopback = remote in LOOPBACK_ADDRS or remote.startswith("127.") or _is_trusted_peer(remote)
    conn = ctx.get_conn()  # type: ignore[attr-defined]

    anonymous = Principal(kind=PRINCIPAL_ANONYMOUS)

    # LAN gate first
    if not is_loopback and not is_lan_sharing_enabled(conn):
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
        # Share token from loopback: power-user escape hatch.
        # Treated as LOCAL_APP because it's the dev machine.
        if is_loopback:
            return AuthResult(AuthDecision.ALLOW, Principal(kind=PRINCIPAL_LOCAL_APP))
        # Share token from LAN: must be a paired, trusted device.
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

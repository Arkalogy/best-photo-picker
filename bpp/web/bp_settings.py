"""Settings + presets + CLIP cap override endpoints.

Extracted from bp_core.py during the v0.1 cleanup. Three loosely
related concerns sat in bp_core because none of them had a logical
home; tonight's split gives them one:

* **Settings GET/PUT** (`/api/v1/settings`) — read all library
  settings, owner-only mutation. The GET path filters sensitive
  keys (share token, anything matching the token-shaped regex) for
  non-owner principals.
* **Presets CRUD** (`/api/v1/presets`) — saved scoring presets.
* **CLIP cap override** (`/api/v1/settings/clip_max_override` +
  the `_clip_cap_status` helper) — per-library bypass of the
  CLIP_EMBEDDING_MAX_ROWS soft cap. The helper is also called from
  `bp_core.api_status` so it's re-exported for that call site.

Backwards compat: re-exported from bp_core in the original module
isn't needed because the only external callers of `_clip_cap_status`
are within the bpp.web layer and they can import from this module
directly.
"""

from __future__ import annotations

import re
from typing import Any

from flask import Blueprint, Response, jsonify, request

from bpp.db.clip import (
    CLIP_EMBEDDING_BYTES,
    CLIP_EMBEDDING_MAX_ROWS,
    CLIP_MAX_OVERRIDE_BYPASS,
    CLIP_MAX_OVERRIDE_KEY,
    get_clip_embedding_count,
)
from bpp.db.presets import (
    delete_preset as db_delete_preset,
)
from bpp.db.presets import (
    list_presets as db_list_presets,
)
from bpp.db.presets import (
    save_preset as db_save_preset,
)
from bpp.db.settings import (
    delete_setting,
    get_all_settings,
    get_setting,
    set_setting,
    set_settings,
)
from bpp.errors import NotFoundError, ValidationError
from bpp.utils.logging import get_logger
from bpp.web.share import requires_local_app
from bpp.web.state import get_ctx

log = get_logger(__name__)

bp = Blueprint("settings_presets", __name__)


# ──────────────────────────────────────────────────────────────────
# CLIP cap override + status helper
# ──────────────────────────────────────────────────────────────────


def _clip_cap_status(conn, clip_count: int) -> dict[str, Any]:
    """Compute the CLIP memory-cap surface for /api/v1/status.

    Always returns the same keys so the frontend doesn't have to
    branch on presence. Peak MB is the realistic ~3x footprint at
    the current row count, not the cap — that's what the user
    actually has to budget against when deciding whether to enable.
    """
    cap = CLIP_EMBEDDING_MAX_ROWS
    overridden = False
    if conn is not None:
        try:
            overridden = get_setting(conn, CLIP_MAX_OVERRIDE_KEY) == CLIP_MAX_OVERRIDE_BYPASS
        except Exception:
            log.debug("clip_max_override lookup failed", exc_info=True)
    over_cap = clip_count > cap
    if not over_cap:
        state = "enabled"
    elif overridden:
        state = "enabled_override"
    else:
        state = "disabled_too_large"
    # 3x dict for dict + search matrix + dedup scratch (see ClipEmbeddingsTooLarge).
    peak_mb = (clip_count * CLIP_EMBEDDING_BYTES / (1024 * 1024)) * 3
    return {
        "clip_cap": cap,
        "clip_cap_status": state,
        "clip_cap_peak_mb": int(peak_mb),
    }


@bp.post("/api/v1/settings/clip_max_override")
@requires_local_app
def api_set_clip_max_override() -> tuple[Response, int]:
    """Set or clear the per-library CLIP cap bypass.

    Body: {"enable": true} writes the bypass sentinel; {"enable": false}
    deletes the setting so the cap applies again. Returns the new
    clip_cap_status so the frontend can re-render without a round-trip
    to /api/v1/status.
    """
    ctx = get_ctx()
    conn = ctx.get_conn()
    data = request.get_json(silent=True) or {}
    enable = bool(data.get("enable"))
    if enable:
        set_setting(conn, CLIP_MAX_OVERRIDE_KEY, CLIP_MAX_OVERRIDE_BYPASS)
        log.info("CLIP cap override enabled by user — semantic dedup will load all embeddings")
    else:
        delete_setting(conn, CLIP_MAX_OVERRIDE_KEY)
        log.info("CLIP cap override cleared by user")
    # Invalidate the cached CLIP matrix so the next load reflects the
    # new cap decision instead of serving the old cached state.
    with ctx.lock:
        ctx.caches.clip_cache["ready"] = False
        ctx.caches.clip_cache["embeddings"] = None
        ctx.caches.clip_cache["matrix"] = None
        # Match the key actually written at state.py:650; "ids" was a
        # typo that created an orphan cache slot instead of clearing the
        # stacked-matrix photo_id list.
        ctx.caches.clip_cache["matrix_ids"] = None
    # Match api_status() behavior in bp_core — degrade gracefully on
    # DB read failure rather than 500ing in the middle of a user-driven
    # settings toggle. The setting itself was written above; the count
    # is just used to render the banner state, so 0 is acceptable.
    try:
        clip_count = get_clip_embedding_count(conn)
    except Exception:
        log.warning("get_clip_embedding_count failed in override toggle", exc_info=True)
        clip_count = 0
    return jsonify(_clip_cap_status(conn, clip_count)), 200


# ──────────────────────────────────────────────────────────────────
# Presets CRUD
# ──────────────────────────────────────────────────────────────────


@bp.get("/api/v1/presets")
def api_presets_list() -> tuple[Response, int]:
    """Return all saved scoring presets as a ``{name: settings}`` map."""
    ctx = get_ctx()
    conn = ctx.get_conn()
    presets = db_list_presets(conn)
    result = {p["name"]: p["settings"] for p in presets}
    return jsonify({"presets": result}), 200


@bp.post("/api/v1/presets")
@requires_local_app
def api_presets_save() -> tuple[Response, int]:
    """Create or overwrite a named scoring preset from the JSON body's
    ``name`` and ``settings`` fields."""
    ctx = get_ctx()
    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()
    if not name:
        raise ValidationError("Name is required", field="name")
    settings = data.get("settings", {})
    conn = ctx.get_conn()
    db_save_preset(conn, name, settings)
    return jsonify({"status": "saved", "name": name}), 200


@bp.delete("/api/v1/presets/<name>")
@requires_local_app
def api_presets_delete(name: str) -> tuple[Response, int]:
    """Delete a saved scoring preset by name. Returns 404 when the
    preset does not exist."""
    ctx = get_ctx()
    conn = ctx.get_conn()
    if not db_delete_preset(conn, name):
        raise NotFoundError("Preset not found", name=name)
    return jsonify({"status": "deleted"}), 200


# ──────────────────────────────────────────────────────────────────
# Settings GET / PUT
# ──────────────────────────────────────────────────────────────────


# Setting keys that hold secrets and must never leak to LAN devices.
# `lan_share_token` is the long-lived auth token for paired phones;
# a LAN_DEVICE that already authenticated could use the GET endpoint
# to learn the token and grant other devices access bypassing the
# pairing flow, OR exfiltrate it for offline replay attacks. Future
# token-shaped keys (OAuth client secrets, etc.) get added here.
_SENSITIVE_SETTING_KEYS: frozenset[str] = frozenset(
    {
        "lan_share_token",
    }
)

# D-08: defense in depth on top of the explicit denylist. set_settings()
# accepts arbitrary owner-supplied keys, so a future contributor adding
# a new token-shaped setting (e.g., `oauth_client_secret`,
# `db_encryption_key`) would silently leak it to LAN unless they
# remember to update _SENSITIVE_SETTING_KEYS. This pattern catches keys
# that LOOK like secrets even when the explicit list misses them. The
# false-positive surface is tolerable: a user setting named
# "favorite_password_strength_label" is exotic enough that a 1-line
# allowlist tweak in this regex is cheaper than a token leak.
_SENSITIVE_KEY_PATTERN = re.compile(
    r"(token|secret|password|passwd|credential|api[_-]?key|private[_-]?key|"
    r"encrypt[_-]?key|encryption[_-]?key|signing[_-]?key|session[_-]?key)",
    re.IGNORECASE,
)


def _is_sensitive_setting_key(key: str) -> bool:
    """True if `key` should not be returned to LAN principals.

    Explicit denylist (_SENSITIVE_SETTING_KEYS) plus a substring-match
    on token/secret/password/credential/api_key/private_key. Defense
    in depth so a future setting added without updating the list still
    gets filtered.
    """
    if key in _SENSITIVE_SETTING_KEYS:
        return True
    return bool(_SENSITIVE_KEY_PATTERN.search(key))


@bp.get("/api/v1/settings")
def api_settings_get() -> tuple[Response, int]:
    """Return the library settings dict.

    LOCAL_APP gets every key including sensitive ones (the owner SPA
    needs the share token to render the Share tab + QR code). LAN
    clients get the dict with sensitive keys stripped (explicit
    denylist + a regex on token-shaped names — see
    `_is_sensitive_setting_key`).
    """
    from flask import g

    from bpp.web.share import PRINCIPAL_LOCAL_APP

    ctx = get_ctx()
    conn = ctx.get_conn()
    settings = get_all_settings(conn)

    principal = getattr(g, "bpp_principal", None)
    is_owner = principal is not None and principal.kind == PRINCIPAL_LOCAL_APP
    if not is_owner:
        settings = {k: v for k, v in settings.items() if not _is_sensitive_setting_key(k)}
    return jsonify(settings), 200


@bp.put("/api/v1/settings")
@requires_local_app
def api_settings_put() -> tuple[Response, int]:
    """Owner-only: rewrite library settings (scoring weights, model
    toggles, theme, share token, etc.).

    LOCAL_APP-only — a paired LAN device must NOT be able to mutate
    library-wide settings. The settings table holds the scoring
    weights, model toggles, theme, and the share token itself, so a
    LAN_DEVICE writing here could disable detection, rotate the share
    token to lock the owner out, or flip arbitrary keys.
    """
    data = request.get_json(silent=True) or {}
    if not data:
        raise ValidationError("No settings provided")
    ctx = get_ctx()
    conn = ctx.get_conn()
    set_settings(conn, data)
    return jsonify({"status": "saved"}), 200

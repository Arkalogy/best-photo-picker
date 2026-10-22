"""Core blueprint: index, status, stats, pick, presets, settings, version.

trimmed down. The model lifecycle (`/api/v1/models/*`) and
pip install (`/api/v1/install/*`) endpoints moved to `bp_models`;
the health probes (`/api/v1/health[/storage]`) moved to `bp_health`.
What's left here is the SPA-shell index, status snapshot, library
stats, the pick endpoint, presets CRUD, settings GET/PUT, version
metadata, the update check, and `recheck-missing`.
"""

from __future__ import annotations

import hmac
import subprocess
from pathlib import Path
from typing import Any

from flask import Blueprint, Response, jsonify, make_response, render_template, request

from bpp.config import DEFAULTS, cfg_get
from bpp.db.clip import (
    get_clip_embedding_count,
)
from bpp.db.pets import has_pet_data
from bpp.db.photos import get_photo_count
from bpp.db.stats import get_library_stats
from bpp.scoring.clip_embed import (
    can_install as clip_can_install,
)
from bpp.scoring.clip_embed import (
    is_available as clip_is_available,
)
from bpp.scoring.face_embed import is_available as face_recognition_available
from bpp.scoring.nudity import is_active as nudenet_active
from bpp.scoring.nudity import is_available as nudenet_available
from bpp.scoring.pets import is_available as pets_available
from bpp.utils.logging import get_logger
from bpp.web.face_worker import has_face_data, needs_face_clustering
from bpp.web.install_status import install_status_fields
from bpp.web.share import (
    FP_COOKIE_MAX_AGE,
    FP_COOKIE_NAME,
    _device_name_from_ua,
    _sanitize_device_name,  # re-exported for tests/test_share_cookie.py
    _token_equals,
    principal_is_local_app,
    requires_local_app,
)
from bpp.web.state import get_ctx, heic_available

# Re-export so tests/test_share_cookie.py can keep importing these
# from `bpp.web.bp_core` after the share-handler split.
__all__ = [
    "_device_name_from_ua",
    "_sanitize_device_name",
    "bp",
]

log = get_logger(__name__)


def _bpp_posture_statement() -> str:
    """Surface the canonical posture banner into the SPA.

    Importing :mod:`bpp.registry` at module-import time would create a
    cycle (registry imports bpp.utils, which can pull in web bits in
    test fixtures), so resolve at call time. Cheap: the constant is a
    module-level string."""
    from bpp.registry import BPP_POSTURE_STATEMENT

    return BPP_POSTURE_STATEMENT


bp = Blueprint("core", __name__)


# CSS/JS cache-buster: mtime of app.css. Mobile Safari is notorious
# for ignoring `Cache-Control: no-cache` and serving stale CSS from
# its back-forward cache, so we append `?v=<mtime>` to the stylesheet
# href every render — when the file changes, browsers fetch fresh.
_APP_CSS_PATH = Path(__file__).parent / "static" / "css" / "app.css"
_JS_MODULES_PATH = Path(__file__).parent / "static" / "js"


def _asset_version() -> str:
    """Return a cache-buster string that changes whenever any JS or CSS
    static asset changes. WKWebView (Tauri) ignores Cache-Control headers
    and serves stale JS from disk cache unless the URL changes. Using the
    max mtime across CSS + all JS files ensures a fresh URL whenever we
    ship updated code."""
    try:
        mtimes = [_APP_CSS_PATH.stat().st_mtime]
        for p in _JS_MODULES_PATH.rglob("*.js"):
            mtimes.append(p.stat().st_mtime)
        for p in _JS_MODULES_PATH.rglob("*.mjs"):
            mtimes.append(p.stat().st_mtime)
        return str(int(max(mtimes)))
    except OSError:
        return "0"


def _presents_boot_secret(ctx: Any) -> bool:
    """Whether this loopback request carries the boot secret as the
    ``_boot`` query parameter. Never a cookie: browsers send cookies to
    every port of 127.0.0.1, which would hand the key to any other
    local listener. The page keeps the key in ``sessionStorage`` (scoped
    to the origin, port included) and re-presents it on reload."""
    secret = ctx.boot_secret
    if not secret:
        return True
    return _token_equals(request.args.get("_boot") or "", secret)


@bp.get("/boot-check")
def api_boot_check() -> Response:
    """The desktop shell asks the server to prove it was started with
    the shell's boot key before navigating to it (another process could
    hold the port): the shell sends a fresh ``nonce`` and checks
    ``X-Boot-Proof`` = HMAC-SHA256(key, nonce). The key itself never
    crosses the wire. Anonymous on purpose (outside ``/api``); without
    a key configured (open loopback) the reply is a bare 204."""
    ctx = get_ctx()
    resp = Response(status=204)
    nonce = request.args.get("nonce") or ""
    if ctx.boot_secret and nonce:
        resp.headers["X-Boot-Proof"] = hmac.new(
            ctx.boot_secret.encode(), nonce.encode(), "sha256"
        ).hexdigest()
    return resp


@bp.get("/")
def index() -> Response:
    """Render the main SPA shell, the LAN pairing wait page, or the
    untrusted phone view depending on the request principal.

    Loopback / app-token clients get the per-boot session token.
    LAN clients with a trusted fingerprint cookie get the persistent
    share token (so bookmarked share URLs survive restarts); untrusted
    LAN devices receive a pairing-pending page instead. Issues a
    fingerprint cookie when one isn't present and sets restrictive
    Referrer / Cache-Control headers."""
    ctx = get_ctx()
    # LAN clients (phone scanning the QR) get the *persistent* share token
    # in their meta tag, so a bookmarked share URL still authenticates after
    # a server restart. Loopback (Tauri / local browser) gets the per-boot
    # app session token, which is independent of the share token and can't
    # be revoked from outside the local app.
    from bpp.web.share import (
        _is_trusted_peer,
        find_or_create_pending_device,
        get_share_token,
        is_lan_sharing_enabled,
    )

    remote = request.remote_addr or ""
    # `is_loopback` here means "the immediate peer is on the host
    # (or a Docker bridge that the operator explicitly trusts)" —
    # i.e. the same machine running the app. It's the precondition
    # for rendering the owner SPA with the per-boot app session
    # token. Trusted-proxy CIDRs let `127.0.0.1:5001:5001` Docker
    # deployments work; they do NOT promote arbitrary LAN clients
    # to owner. See bpp/web/share.py:_is_trusted_peer for the
    # blast-radius limit.
    is_loopback = (
        remote in ("127.0.0.1", "::1", "localhost")
        or remote.startswith("127.")
        or _is_trusted_peer(remote)
    )

    if not is_loopback and is_lan_sharing_enabled(ctx.get_conn()):
        token = get_share_token(ctx.get_conn())
        # Fingerprint cookie: stable identifier so the owner can approve
        # this specific browser/device, not just "anyone with the URL".
        # HttpOnly defends against XSS exfiltration; SameSite=Lax blocks
        # CSRF from cross-origin forms.
        import secrets

        from bpp.web.share import _token_equals, consume_pair_request_token
        from bpp.web.share_devices import get_device_by_fingerprint

        conn = ctx.get_conn()
        fp = request.cookies.get(FP_COOKIE_NAME)
        known = get_device_by_fingerprint(conn, fp) if fp else None
        if known is None:
            # A device the owner has never seen joins the pending list only
            # through the share link: the token in the QR / URL is the proof
            # it was invited. Without that, any LAN host could queue itself
            # for approval under a name of its choosing (the User-Agent) and
            # collect the share token from a single approve click.
            presented = request.args.get("_token") or request.headers.get("X-Auth-Token") or ""
            if not _token_equals(presented, token):
                return make_response(render_template("pair_needs_link.html"), 403)
            if not consume_pair_request_token(remote):
                return make_response(render_template("pair_needs_link.html", too_many=True), 429)
            fp = secrets.token_urlsafe(24)  # ~190 bits
        # Touch the device row (creates pending if new, bumps last_seen if
        # existing). Owner-side UI uses this to surface pairing requests
        # in real time.
        row = find_or_create_pending_device(
            conn,
            fingerprint=fp,
            name=_device_name_from_ua(request.headers.get("User-Agent", "")),
            ip=remote,
        )
        trusted = row.get("trusted_at") is not None and row.get("revoked_at") is None
        # Phone is trusted → full SPA. Untrusted → pairing wait page.
        # Defense in depth: untrusted devices don't even get the SPA
        # bundle, so XSS in unrelated code can't leak the share token
        # to a phone the owner hasn't approved.
        if trusted:
            resp = make_response(
                render_template(
                    "index.html",
                    serve_mode=ctx.serve_mode,
                    auth_token=token,
                    library_id=ctx.library_id,
                    asset_version=_asset_version(),
                    bpp_posture_statement=_bpp_posture_statement(),
                )
            )
        else:
            resp = make_response(render_template("pair.html"))
        resp.set_cookie(
            FP_COOKIE_NAME,
            fp,
            max_age=FP_COOKIE_MAX_AGE,
            httponly=True,
            samesite="Lax",
            secure=request.scheme == "https",
        )
    else:
        # Loopback: any local process can reach this port, and the page
        # carries the owner token. With a boot secret configured, only a
        # caller that presents it (the launcher's first navigation, or
        # the page re-presenting it from sessionStorage) gets the token;
        # others get a locked page.
        booted = _presents_boot_secret(ctx)
        if ctx.boot_secret and not booted:
            resp = make_response(render_template("loopback_locked.html"), 403)
            resp.headers["Referrer-Policy"] = "no-referrer"
            return resp
        token = ctx.auth_token
        resp = make_response(
            render_template(
                "index.html",
                serve_mode=ctx.serve_mode,
                auth_token=token,
                library_id=ctx.library_id,
                asset_version=_asset_version(),
                bpp_posture_statement=_bpp_posture_statement(),
            )
        )

    # Defense in depth: prevent the share token from leaking via Referer
    # when the user clicks any outbound link (map tile, video CDN, etc.)
    resp.headers["Referrer-Policy"] = "no-referrer"
    # Mobile Safari aggressively caches HTML in its back-forward cache
    # and will serve stale documents (with stale CSS/JS asset URLs)
    # even after Cmd+R. no-store forces a fresh fetch each navigation
    # so cache-busted asset URLs in the new HTML actually take effect.
    resp.headers["Cache-Control"] = "no-store, must-revalidate"
    return resp


# _clip_cap_status lives in bp_settings since the v0.1 split. Imported
# at module load so api_status's snapshot pulls the same status dict
# the override toggle endpoint returns.
from bpp.web.clip_cap import clip_cap_status  # noqa: E402


@bp.get("/api/v1/status")
def api_status() -> Response:
    """Return the user-facing app status snapshot.

    Reports first-run state, photo counts, worker activity (analyze,
    import, face, CLIP), feature availability flags (face/pets/CLIP/
    HEIC/NudeNet), CLIP embedding count, default config, and face
    cluster threshold. Cheap by design: a single COUNT() rather than
    a full analysis load."""
    ctx = get_ctx()
    wd = ctx.workdir

    # Use a cheap DB count instead of loading all photos into memory.
    # The full analysis load happens lazily when photos are actually needed.
    conn = ctx.get_conn() if wd else None
    photo_count = get_photo_count(conn) if conn else 0

    clip_avail = clip_is_available()
    clip_installable = clip_can_install()
    clip_count = 0
    with ctx.lock:
        clip_ready = ctx.caches.clip_cache["ready"]
    if clip_avail or clip_ready:
        try:
            clip_count = get_clip_embedding_count(conn) if conn else 0
        except Exception:
            log.warning("Failed to get CLIP embedding count", exc_info=True)

    # first_run is set to 'true' in settings on fresh DB creation.
    # A library with photos can never be first-run regardless of the flag
    # (handles the case where photos were imported via CLI without clearing
    # the flag through the normal _finalize_import path).
    first_run = False
    if conn and photo_count == 0:
        try:
            from bpp.db.settings import get_setting

            first_run = get_setting(conn, "first_run") == "true"
        except Exception:
            log.debug("first_run setting lookup failed", exc_info=True)

    # filter filesystem paths for non-LOCAL_APP principals.
    # workdir / input_dir / library_path leak the owner's username
    # and drive layout to LAN clients. Health flags / counts /
    # availability booleans stay accessible.
    response: dict[str, Any] = {
        "has_analysis": photo_count > 0,
        "first_run": first_run,
        "image_count": photo_count,
        "analyzing": ctx.worker.is_alive,
        "importing": ctx.import_worker.is_alive,
        "serve_mode": ctx.serve_mode,
        "defaults": DEFAULTS,
        "face_recognition_available": face_recognition_available(),
        **install_status_fields(owner=principal_is_local_app()),
        # ``nudenet_available``: detection CAN be enabled (runtime
        # present). ``nudenet_active``: it actually runs — accepted with
        # the current license text AND weights on disk.
        "nudenet_available": nudenet_available(),
        "nudenet_active": nudenet_active(),
        "pets_available": pets_available(),
        "face_extraction_done": has_face_data(conn) if conn else False,
        "face_needs_clustering": needs_face_clustering(conn) if conn else False,
        "face_extracting": ctx.face_worker.is_alive,
        "face_cluster_threshold": cfg_get(ctx.config, "face_cluster_threshold"),
        "clip_available": clip_avail,
        "clip_installable": clip_installable,
        "clip_ready": clip_ready,
        "clip_extracting": ctx.clip_worker.is_alive,
        "clip_embedding_count": clip_count,
        # CLIP semantic-dedup memory cap surface for the Settings
        # banner. Three states:
        #  - "enabled": under cap, no decision needed.
        #  - "disabled_too_large": over cap, no override → banner
        #    asks the user to opt in.
        #  - "enabled_override": over cap, override active → status
        #    line + "Disable" link.
        # peak_mb is the realistic ~3x footprint at current row count,
        # surfaced verbatim in the banner so users can size the
        # decision against their actual RAM.
        **clip_cap_status(conn, clip_count),
        "heic_available": heic_available(),
        "pet_detection_done": has_pet_data(conn) if conn else False,
        # Background perceptual-hash backfill (powers near-duplicate
        # detection). Surfaced so the UI can show "Computing similarity
        # N/M" — this pass used to run silently + peg the machine.
        "phash_progress": {
            "running": ctx.analysis_store.phash_running,
            "done": ctx.analysis_store.phash_done,
            "total": ctx.analysis_store.phash_total,
        },
    }
    if principal_is_local_app():
        response["workdir"] = wd
        response["input_dir"] = ctx.input_dir
        response["library_path"] = ctx.state.get("library_path", "")
    return jsonify(response)


@bp.get("/api/v1/stats")
def api_stats() -> tuple[Response, int]:
    """Return library statistics: counts, sizes, format breakdown."""
    ctx = get_ctx()
    conn = ctx.get_conn()
    stats = get_library_stats(conn)
    return jsonify(stats), 200


@bp.post("/api/v1/pick")
@requires_local_app
def api_pick() -> tuple[Response, int]:
    """Open a native macOS file/folder picker via osascript and return
    the selected POSIX path. ``mode='file'`` filters to archive types;
    anything else picks a folder. Returns ``{cancelled: true}`` when
    the user dismisses the dialog.

    LOCAL_APP-only — spawns a native UI dialog on the owner's
    desktop. A LAN device triggering this would pop UI on the host
    out of the user's control."""
    data = request.get_json(silent=True) or {}
    mode = data.get("mode", "folder")

    if mode == "file":
        script = (
            "POSIX path of (choose file of type"
            ' {"zip","gz","tar","tgz","bz2"}'
            ' with prompt "Select archive")'
        )
    else:
        script = 'POSIX path of (choose folder with prompt "Select photo folder")'

    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=120,
    )

    if result.returncode != 0:
        return jsonify({"cancelled": True}), 200

    path = result.stdout.strip().rstrip("/")
    return jsonify({"path": path}), 200


# --- Presets ---


# --- Version & Updates ---


@bp.get("/api/v1/version")
def api_version() -> tuple[Response, int]:
    """Return the current app version."""
    from bpp import __version__

    return jsonify({"version": __version__}), 200


@bp.get("/api/v1/update/check")
@requires_local_app
def api_update_check() -> tuple[Response, int]:
    """Check GitHub for a newer release.

    owner-only. README claims the update check is
    "opt-in"; the user-facing toggle (`bpp_check_updates` in
    localStorage) only gates whether the SPA fires the request, so a
    paired LAN device could otherwise call this directly with
    ``?force=1`` and force the host to reach api.github.com on demand.
    Closing the surface to LOCAL_APP only matches the documented
    privacy posture."""
    from bpp.web.update_checker import check_for_update

    force = request.args.get("force", "").lower() in ("1", "true")
    result = check_for_update(force=force)
    return jsonify(result), 200


@bp.get("/api/v1/models/pending")
def api_models_pending() -> tuple[Response, int]:
    """List models that aren't on disk yet — i.e. the downloads the
    next analyze run would actually trigger.

    Powers the per-model consent prompt: instead of a vague "~50 MB
    will download", the user sees concrete name/size/host for each
    pending model, then approves the whole set once.

    Restricted models whose license hasn't been accepted are filtered
    out of the main list and returned separately as ``blocked``.
    Offering them in the "Download and analyze" prompt was misleading:
    clicking proceed fires the network call, the server-side
    ``enforce_load_policy_for`` gate refuses, and the user gets a
    confusing "blocked_needs_ack" toast instead of a clean route to
    the acceptance dialog. With the filter, the prompt shows only
    downloadable models, and the frontend can surface a hint pointing
    the user to Settings → Models for the blocked ones. Attribution-
    licensed models (Apache / BSD-style) not yet accepted come back as
    ``attribution``: the dialog accepts them in one combined step.

    The eligibility rules live in :func:`bpp.web.model_filter.
    compute_pending_and_blocked` (pure, unit-tested); this endpoint is a
    thin HTTP wrapper.
    """
    from bpp.web.model_filter import compute_pending_and_blocked

    items, blocked = compute_pending_and_blocked()
    free = [i for i in items if not i.get("needs_acceptance")]
    attribution = [i for i in items if i.get("needs_acceptance")]
    return (
        jsonify(
            {
                "models": free,
                "total_mb": sum(i["size_mb"] for i in free),
                # Downloads that happen only if the user accepts the
                # attribution licenses in the pre-analysis dialog.
                "attribution": attribution,
                "attribution_mb": sum(i["size_mb"] for i in attribution),
                "blocked": blocked,
            }
        ),
        200,
    )

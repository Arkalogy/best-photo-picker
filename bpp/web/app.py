"""Flask app factory."""

from __future__ import annotations

import atexit
import sys

from flask import Flask, jsonify, request

from bpp.utils.logging import get_logger
from bpp.web.state import WebAppState

log = get_logger(__name__)


def create_app(
    input_dir: str | None = None,
    workdir: str | None = None,
    config_path: str | None = None,
    library_path: str | None = None,
) -> Flask:
    """Create and configure the Flask application."""
    # One-shot HEIF availability log. When pillow_heif is missing, HEIC
    # source files can't be decoded — face crops on HEIF photos fall
    # back to unrotated bbox coords (visible bug). Without this, the
    # missing dep is silent until the user inspects a wrong-rotation
    # crop and has no breadcrumb.
    from bpp.web.state import heic_available

    if not heic_available():
        from bpp.utils.logging import get_logger

        get_logger(__name__).warning(
            "pillow_heif not installed — HEIC photos will be skipped on import "
            "and HEIF face crops will use unrotated bbox coords. "
            "Install with: pip install bppicker[heic]"
        )
    else:
        # Without this PIL.Image.open() raises "cannot identify image file"
        # for HEIC sources — broken /photo serving in the lightbox even
        # though the lib is installed.
        from pillow_heif import register_heif_opener

        register_heif_opener()

    # Warn on the deprecated boolean BPP_TRUST_PROXY=1 flag — see
    # bpp/web/share.py for the safer CIDR-list replacement.
    from bpp.web.share import warn_if_legacy_trust_proxy

    warn_if_legacy_trust_proxy()

    app = Flask(__name__)
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
    app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB

    # P7: single error handler that translates any uncaught BppError
    # into a structured response envelope. Replaces the historical
    # 47 ad-hoc ``return jsonify({"error": ...}), 500`` sites; endpoints
    # can now just ``raise ValidationError(...)`` and the handler does
    # the response + logging consistently.
    #
    # Privacy: the response body comes from ``user_message`` (safe);
    # ``diagnostic_message`` is logged separately at WARNING with the
    # full exception traceback. Filesystem paths or internal IDs that
    # belong only in the log stay out of the wire payload.
    from bpp.errors import BppError

    @app.errorhandler(BppError)
    def _handle_bpp_error(exc: BppError):
        # exc_info=exc: pass the instance directly so the formatter
        # renders the traceback even if logging's implicit "current
        # exception" frame is different. Equivalent to exc_info=True
        # when called inside an except block, but more explicit.
        #
        # T4: include exc.context in the log when non-empty so on-call
        # has the actionable details (photo_id, missing field, count
        # over cap, etc.) in the same line as the diagnostic. Without
        # this they'd have to cross-reference the HTTP request log
        # with the response envelope to find the same kwargs. Empty
        # context is omitted to keep the log signal-to-noise high
        # — same rule the response envelope uses.
        #
        # Log level by status class: 4xx is a CLIENT condition (the
        # caller asked for something that isn't there, or sent a bad
        # body) — those are expected and belong at INFO. 5xx is a
        # SERVER bug — those keep WARNING + exc_info so the on-call
        # signal isn't drowned out by routine 404s ("library is
        # empty" / "no analysis data yet" on a fresh first-run).
        level = log.info if 400 <= exc.http_status < 500 else log.warning
        # Only attach exc_info for 5xx — for 4xx the traceback is
        # noise; the diagnostic message is enough.
        kwargs = {} if 400 <= exc.http_status < 500 else {"exc_info": exc}
        if exc.context:
            level(
                "BppError on %s %s: %s context=%s",
                request.method,
                request.path,
                exc.diagnostic_message,
                exc.context,
                **kwargs,
            )
        else:
            level(
                "BppError on %s %s: %s",
                request.method,
                request.path,
                exc.diagnostic_message,
                **kwargs,
            )
        return jsonify(exc.to_dict()), exc.http_status

    # load third-party plugin packages BEFORE building
    # WebAppState so plugins can register face detectors, embedders,
    # config-schema fields, smart album types, and worker factories
    # before the registries are first consumed. Failures are logged
    # and skipped — a single broken plugin must not abort startup.
    from bpp.plugins import load_plugin_entry_points

    load_plugin_entry_points()

    # P5b: now that the loader has called every plugin's setup()
    # callable (which has had a chance to call register_plugin), fire
    # the on_register lifecycle hook so plugins can do app-aware
    # registration (blueprints, CLI commands, etc.).
    try:
        from bpp.plugin_protocol import (
            fire_on_db_restore_if_pending,
            fire_on_register,
        )

        fire_on_register(app)
        # P-08: drain the deferred Protection C restore signal.
        # serve.py noted the corrupt-sidecar path before plugins were
        # loaded; now that fire_on_register has run, plugins exist
        # and can flush stale caches before on_library_open primes
        # against the freshly-restored DB.
        fire_on_db_restore_if_pending()
    except Exception:
        pass  # best-effort; loader already logs per-plugin failures

    # Create shared state and register on app.extensions for blueprint access
    ctx = WebAppState(
        input_dir=input_dir,
        workdir=workdir,
        config_path=config_path,
        library_path=library_path,
    )
    app.extensions["bpp"] = ctx

    # Register blueprints
    from bpp.web.bp_album_overrides import bp as album_overrides_bp
    from bpp.web.bp_albums import bp as albums_bp
    from bpp.web.bp_analysis import bp as analysis_bp
    from bpp.web.bp_calendar import bp as calendar_bp
    from bpp.web.bp_catalog import bp as catalog_bp
    from bpp.web.bp_clip import bp as clip_bp
    from bpp.web.bp_core import bp as core_bp
    from bpp.web.bp_export import bp as export_bp
    from bpp.web.bp_faces import bp as faces_bp
    from bpp.web.bp_faces_bbox import bp as faces_bbox_bp
    from bpp.web.bp_faces_cluster_ops import bp as faces_cluster_ops_bp
    from bpp.web.bp_faces_extract import bp as faces_extract_bp
    from bpp.web.bp_faces_manage import bp as faces_manage_bp
    from bpp.web.bp_faces_photo import bp as faces_photo_bp
    from bpp.web.bp_faces_review import bp as faces_review_bp
    from bpp.web.bp_groups import bp as groups_bp
    from bpp.web.bp_health import bp as health_bp
    from bpp.web.bp_inpaint import bp as inpaint_bp
    from bpp.web.bp_install import bp as install_bp
    from bpp.web.bp_library import bp as library_bp
    from bpp.web.bp_logs import bp as logs_bp
    from bpp.web.bp_media import bp as media_bp
    from bpp.web.bp_memories import bp as memories_bp
    from bpp.web.bp_model_admin import bp as model_admin_bp
    from bpp.web.bp_model_registry import bp as model_registry_bp
    from bpp.web.bp_models import bp as models_bp
    from bpp.web.bp_os_integration import bp as os_integration_bp
    from bpp.web.bp_pets import bp as pets_bp
    from bpp.web.bp_photos import bp as photos_bp
    from bpp.web.bp_photos_lifecycle import bp as photos_lifecycle_bp
    from bpp.web.bp_photos_manage import bp as photos_manage_bp
    from bpp.web.bp_recompute import bp as recompute_bp
    from bpp.web.bp_search import bp as search_bp
    from bpp.web.bp_settings import bp as settings_bp
    from bpp.web.bp_share import bp as share_bp
    from bpp.web.bp_tags import bp as tags_bp

    app.register_blueprint(core_bp)
    app.register_blueprint(health_bp)
    app.register_blueprint(models_bp)
    app.register_blueprint(install_bp)
    app.register_blueprint(model_registry_bp)
    app.register_blueprint(model_admin_bp)
    app.register_blueprint(catalog_bp)
    app.register_blueprint(photos_bp)
    app.register_blueprint(export_bp)
    app.register_blueprint(photos_manage_bp)
    app.register_blueprint(photos_lifecycle_bp)
    app.register_blueprint(inpaint_bp)
    app.register_blueprint(recompute_bp)
    app.register_blueprint(os_integration_bp)
    app.register_blueprint(albums_bp)
    app.register_blueprint(album_overrides_bp)
    app.register_blueprint(media_bp)
    app.register_blueprint(analysis_bp)
    app.register_blueprint(faces_bp)
    app.register_blueprint(faces_extract_bp)
    app.register_blueprint(faces_photo_bp)
    app.register_blueprint(faces_manage_bp)
    app.register_blueprint(faces_cluster_ops_bp)
    app.register_blueprint(faces_bbox_bp)
    app.register_blueprint(faces_review_bp)
    app.register_blueprint(groups_bp)
    app.register_blueprint(clip_bp)
    app.register_blueprint(pets_bp)
    app.register_blueprint(search_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(library_bp)
    app.register_blueprint(tags_bp)
    app.register_blueprint(memories_bp)
    app.register_blueprint(calendar_bp)
    app.register_blueprint(logs_bp)
    app.register_blueprint(share_bp)

    # Auth boundary: delegates to bpp.web.share.authorize_request().
    # Splitting the policy out makes it unit-testable, swappable for
    # OAuth/JWT in the future, and easier to reason about. See
    # docs/security.md for the trust model and threat assumptions.
    @app.before_request
    def _check_auth_token():
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

        result = authorize_request(request, ctx)
        # Stash on flask.g so blueprint handlers (and any future
        # logging / per-resource scopes) can reach the principal that
        # authenticated this request without re-running the auth check.
        from flask import g

        g.bpp_principal = result.principal

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
                )
            # Per-principal rate limit on destructive /api/ endpoints.
            # Defends against runaway-loop clients + a hostile paired
            # device slamming permanent-delete / analyze. 60 req/minute
            # burst, 60 steady. Read-only GETs aren't gated — those
            # are mostly served from cache.
            if request.method in ("POST", "PUT", "DELETE", "PATCH") and request.path.startswith(
                "/api/"
            ):
                from bpp.web.bp_health import is_e2e_fixture_library
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
        is_localhost = request.remote_addr in ("127.0.0.1", "::1")
        (log.debug if is_localhost else log.warning)(
            "Auth rejected: %s %s", request.remote_addr, request.path
        )
        return jsonify(error="Forbidden"), 403

    # Content-Security-Policy: defense-in-depth against XSS.
    #
    # script-src uses a per-request nonce. All inline event handlers
    # (onclick, oninput, etc.) have been migrated to data-action /
    # data-oninput attributes dispatched by globals.js — no
    # 'unsafe-inline' needed for script-src.
    #
    # style-src still requires 'unsafe-inline' for inline <style>
    # blocks and dynamic style= attributes set by JS.
    #
    # Leaflet + markercluster are vendored under /static/vendor/ so
    # no external script host is needed. Map tile PNGs are fetched
    # from OpenStreetMap when the user opens the Map view; img-src
    # whitelists those domains.
    import secrets

    @app.before_request
    def _gen_csp_nonce() -> None:
        from flask import g

        # Token URL-safe is base64url; 16 bytes = 22 chars, plenty
        # of entropy and CSP-spec-compatible.
        g.csp_nonce = secrets.token_urlsafe(16)

    @app.context_processor
    def _inject_csp_nonce() -> dict[str, str]:
        from flask import g

        return {"csp_nonce": getattr(g, "csp_nonce", "")}

    @app.after_request
    def _set_csp(response):
        from flask import g

        if response.content_type and "text/html" in response.content_type:
            nonce = getattr(g, "csp_nonce", "")
            nonce_src = f"'nonce-{nonce}'" if nonce else ""
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                f"script-src 'self' {nonce_src}; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: blob: https://*.tile.openstreetmap.org; "
                "connect-src 'self'; "
                "font-src 'self'"
            )
        # Prevent WKWebView from serving stale JS from its disk cache
        # after an app update. CSS has a ?v= query param; JS modules
        # use import statements that browsers resolve without the param,
        # so no-store is the reliable cross-browser / WKWebView strategy.
        if request.path.startswith("/static/js/"):
            response.headers["Cache-Control"] = "no-store"
        # Security headers — defense-in-depth for LAN-exposed instances
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    # Ensure workers stop and SQLite connections are closed on app teardown.
    # Skip under pytest: tests create many app instances; registering N
    # atexit handlers that all fire at interpreter exit (after pytest has
    # closed stdout/stderr) produces "I/O operation on closed file"
    # tracebacks if a worker's shutdown path needs to log. Workers are
    # daemon threads (base_worker.py) so they're terminated with the
    # interpreter regardless. Long-lived servers (`bpp serve`) install
    # their own signal + atexit handlers in bpp.commands.do_serve.
    if "pytest" not in sys.modules:
        atexit.register(ctx.shutdown)

    return app

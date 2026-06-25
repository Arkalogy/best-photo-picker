"""`bpp web` and `bpp serve` — Flask launchers.

Extracted from bpp.commands during the v0.1 cleanup. `do_web` is a
lightweight workdir-based launcher; `do_serve` is the long-lived
library-based server with signal handlers, atexit, and LAN sharing.
Re-exported from `bpp.commands` for backwards compatibility with
the CLI and tests.
"""

from __future__ import annotations

import argparse
import os

from bpp.utils.logging import get_logger, setup_logging


def do_web(args: argparse.Namespace) -> int:
    """Launch interactive web UI."""
    setup_logging(debug=getattr(args, "debug", False))
    log = get_logger(__name__)

    try:
        from bpp.web.app import create_app
    except ImportError:
        log.error("Flask is not installed. Install with: pip install bppicker[web]")
        return 1

    input_dir = os.path.abspath(args.input) if args.input else None
    workdir = os.path.abspath(args.workdir) if args.workdir else None
    config_path = getattr(args, "config", None)

    # If input given without workdir, create a temp workdir
    if input_dir and not workdir:
        import tempfile

        workdir = tempfile.mkdtemp(prefix="bpp_web_")
        log.info("Created temp workdir: %s", workdir)

    port = args.port
    app = create_app(input_dir=input_dir, workdir=workdir, config_path=config_path)

    if not args.no_browser:
        import threading
        import webbrowser

        threading.Timer(1.0, webbrowser.open, args=[f"http://127.0.0.1:{port}"]).start()

    host = getattr(args, "host", None) or "127.0.0.1"
    log.info("Starting web UI at http://%s:%d", host, port)
    app.run(host=host, port=port, debug=getattr(args, "debug", False), threaded=True)
    return 0


def do_serve(args: argparse.Namespace) -> int:
    """Start photo management server with library-based workflow."""
    setup_logging(debug=getattr(args, "debug", False))
    log = get_logger(__name__)

    try:
        from bpp.web.app import create_app
    except ImportError:
        log.error("Flask is not installed. Install with: pip install bppicker[web]")
        return 1

    from bpp.db.library import ensure_library_dirs, get_library_path
    from bpp.db.registry import add_library, get_active_library, set_active_library

    config_path = getattr(args, "config", None)
    library_path = getattr(args, "library", None)
    if not library_path:
        # Try the registry's active library, fall back to default
        library_path = get_active_library() or get_library_path()
    library_path = os.path.abspath(library_path)

    dirs = ensure_library_dirs(library_path)
    workdir = dirs["data"]  # DB and caches live in data/ subdir

    try:
        if os.stat(library_path).st_mode & 0o002:
            log.warning(
                "Library path %s is world-writable — any local user can read or "
                "modify your photo collection. Consider tightening permissions.",
                library_path,
            )
    except OSError:
        pass  # non-fatal; proceed even if stat fails

    # Auto-register library in the registry
    add_library(library_path)
    set_active_library(library_path)

    port = args.port

    # Attach the file-log handler BEFORE create_app. create_app
    # constructs WebAppState → init_app_db → spawns the Phase 5
    # background daemon, which emits its first log.info() ('Phase 5
    # backfill starting') immediately. If the file handler isn't
    # attached yet, that line vanishes into the stream handler and
    # never reaches <library>/logs/server.log — while the matching
    # 'done' line ~1 s later DOES land there (the handler is up by
    # then), which masked the bug. Regression-gated by
    # tests/test_serve_log_handler_ordering.py.
    from bpp.utils.logging import add_file_handler

    add_file_handler(os.path.join(dirs["logs"], "server.log"))

    # Protection C: full integrity check + auto-restore + auto-prune
    # BEFORE create_app touches the DB. If the previous shutdown was
    # ungraceful (SIGKILL mid-write — Jun-2 incident), the WAL may be
    # in a state where quick_check passes but the full integrity check
    # finds "never used pages" debris. Auto-restore from .backup when
    # we have a clean one; raise a clear actionable error if not.
    # See bpp/db/connection.py:restore_from_backup_if_corrupt.
    from bpp.db.connection import (
        prune_corrupt_face_embeddings,
        restore_from_backup_if_corrupt,
    )

    db_path = os.path.join(workdir, "photopicker.db")
    try:
        restored_from = restore_from_backup_if_corrupt(db_path)
        if restored_from is not None:
            log.warning(
                "Startup: auto-restored DB from .backup; corrupt file at %s",
                restored_from,
            )
            # Deferred signal: plugins haven't loaded yet (they
            # register inside create_app). Stash the path; app.py
            # drains it via fire_on_db_restore_if_pending() right
            # after fire_on_register so plugin-owned caches can
            # invalidate before on_library_open primes against the
            # restored DB.
            from bpp.plugin_protocol import note_db_restore

            note_db_restore(restored_from)
    except RuntimeError as exc:
        # No good backup → refuse to start with a clear message
        # instead of crashing on the first query.
        log.error("Startup integrity gate: %s", exc)
        raise
    pruned = prune_corrupt_face_embeddings(db_path)
    if pruned:
        log.warning(
            "Startup: pruned %d corrupt face_embeddings row(s)",
            pruned,
        )

    app = create_app(
        workdir=workdir,
        config_path=config_path,
        library_path=library_path,
    )

    if not args.no_browser:
        import threading
        import webbrowser

        threading.Timer(1.0, webbrowser.open, args=[f"http://127.0.0.1:{port}"]).start()

    # Ensure SQLite connections are closed on shutdown (prevents UNE zombies)
    import atexit
    import signal

    from bpp.db.connection import close_all_connections

    def _shutdown(*_args: object) -> None:
        close_all_connections()

    def _graceful_shutdown(*_args: object) -> None:
        # Cancel and join workers before closing DB connections so that
        # in-flight analysis/face/import threads aren't left with a closed
        # connection mid-operation. Matters especially under `docker stop`
        # which sends SIGTERM.
        try:
            from bpp.web.state import get_ctx_or_none

            ctx = get_ctx_or_none()
            if ctx is not None:
                ctx.shutdown()
        except Exception:
            log.debug("Context cleanup during shutdown", exc_info=True)
        close_all_connections()
        raise SystemExit(0)

    atexit.register(_shutdown)
    signal.signal(signal.SIGTERM, _graceful_shutdown)
    signal.signal(signal.SIGINT, _graceful_shutdown)

    ctx = app.extensions["bpp"]
    ctx.port = port

    # If LAN sharing is enabled (DB-persisted toggle), surface the share URL
    # in the log on startup so users can grab it without opening Settings.
    from bpp.web.share import (
        detect_lan_ip,
        format_share_banner,
        is_lan_sharing_enabled,
    )

    sharing_enabled = is_lan_sharing_enabled(ctx.get_conn())

    # default the bind address based on the share toggle so
    # `bpp serve` on a coffee-shop Wi-Fi doesn't make the service
    # visible to a port scan when the user hasn't opted into LAN
    # sharing. Defense-in-depth — the auth layer's `authorize_request`
    # already blocks non-loopback when sharing is off, but binding
    # loopback-only keeps the service off the LAN entirely. An
    # explicit `--host` override (loopback or LAN) wins regardless,
    # for users who run the server behind a reverse proxy or want
    # 0.0.0.0 unconditionally.
    user_host = getattr(args, "host", None)
    if user_host:
        host = user_host
    elif sharing_enabled:
        host = "0.0.0.0"
    else:
        host = "127.0.0.1"

    # surface the resolved bind host on the ctx so the
    # /api/v1/share/toggle handler can refuse to enable LAN sharing
    # while the server is bound loopback-only — without this, a user
    # toggling sharing ON via Settings would persist the flag, the UI
    # would show a LAN URL, but no phone could actually connect
    # because the process never opened the LAN interface.
    ctx.bound_host = host

    log.info("Library: %s", library_path)
    log.info("Starting server at http://%s:%d", host, port)

    if sharing_enabled:
        lan_ip = detect_lan_ip()
        if lan_ip:
            # Banner intentionally does NOT include the tokenized share
            # URL — see format_share_banner for the rationale (long-lived
            # secret leakage into rotating server.log files).
            for line in format_share_banner(f"{lan_ip}:{port}"):
                log.warning(line)
    if host == "0.0.0.0" and not sharing_enabled:
        # User passed `--host 0.0.0.0` explicitly even though sharing
        # is off — same advisory as before so a curious port scan
        # doesn't look like a security gap.
        log.warning(
            "Bound to 0.0.0.0 with LAN sharing OFF — non-loopback clients "
            "are blocked with HTTP 403 (LAN gate runs before any path "
            "match). Toggle Settings -> Share to enable LAN access."
        )

    # Reverse-proxy support (off by default). enabling
    # `behind_proxy` without also configuring BPP_TRUSTED_PROXIES is a
    # privilege-escalation primitive — vanilla ProxyFix(x_for=1)
    # honours X-Forwarded-For from ANY peer, so a public-internet
    # client could spoof loopback and unlock the owner SPA. The fix
    # is two-part: (1) require BPP_TRUSTED_PROXIES non-empty before
    # any rewriting, and (2) gate the ProxyFix rewrite at the WSGI
    # layer so it only fires when the RAW upstream peer is in the
    # trusted set. Without (2), ProxyFix rewrites `REMOTE_ADDR` to
    # whatever XFF claims before the app sees the original peer, so
    # the post-ProxyFix `_is_trusted_peer` check is checking the
    # spoofed address.
    if (ctx.config or {}).get("behind_proxy"):
        from bpp.web.share import _trusted_peer_networks

        nets = _trusted_peer_networks()
        if not nets:
            log.error(
                "behind_proxy is set but BPP_TRUSTED_PROXIES is empty/unsafe; "
                "refusing to enable ProxyFix. Set BPP_TRUSTED_PROXIES to a "
                "loopback / RFC1918 / link-local CIDR matching your reverse "
                "proxy's address (e.g. 127.0.0.1/32 for nginx on the same "
                "host). See docs/security.md."
            )
        else:
            import ipaddress as _ip

            from werkzeug.middleware.proxy_fix import ProxyFix

            _proxy_fixed = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
            _bare_app = app.wsgi_app
            _trusted_nets = nets

            def _gated_proxy_fix(environ, start_response):
                """Apply ProxyFix only when the raw upstream peer is trusted."""
                raw_remote = environ.get("REMOTE_ADDR", "")
                try:
                    raw_ip = _ip.ip_address(raw_remote)
                except ValueError:
                    return _bare_app(environ, start_response)
                if any(raw_ip in net for net in _trusted_nets):
                    return _proxy_fixed(environ, start_response)
                return _bare_app(environ, start_response)

            app.wsgi_app = _gated_proxy_fix
            log.info(
                "ProxyFix enabled — X-Forwarded-For honoured ONLY from %d trusted CIDR(s)",
                len(nets),
            )

    # HTTPS support (off by default). Provide an `ssl_context` config
    # entry as a (cert_path, key_path) tuple for TLS termination at
    # the Flask layer. Most deployments will instead run bpp behind
    # nginx / Caddy and leave this unset. See docs/security.md.
    ssl_context = (ctx.config or {}).get("ssl_context")

    # YAML loads sequences as Python lists, but Werkzeug's
    # `BaseWSGIServer` requires `isinstance(ssl_context, tuple)` to
    # call `load_ssl_context(*ctx)` — with a list it falls through
    # to `wrap_socket` and raises AttributeError on the first
    # connection (server boots, dies on first hit). Normalize lists
    # of length 2 (the documented `[cert, key]` shape) to tuples.
    # Other values (None, "adhoc", an actual SSLContext) pass through.
    if isinstance(ssl_context, list) and len(ssl_context) == 2:
        ssl_context = tuple(ssl_context)
    elif isinstance(ssl_context, list):
        log.error(
            "config.ssl_context is a list of length %d; expected [cert_path, "
            "key_path]. Disabling SSL.",
            len(ssl_context),
        )
        ssl_context = None

    app.run(
        host=host,
        port=port,
        debug=getattr(args, "debug", False),
        threaded=True,
        ssl_context=ssl_context,
    )
    return 0

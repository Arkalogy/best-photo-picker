"""Starting the server safely on a computer other people also use.

Every account and program on the machine can reach 127.0.0.1. This
module holds what keeps them away from the owner page at launch:

* the per-run boot key and the keyed link the terminal prints;
* opening the browser only at a server that proves it knows that key
  (never at whatever else happens to hold the port);
* holding the loopback address ourselves while the server listens on
  every interface, where the system would let another program take it.
"""

from __future__ import annotations

import argparse
import hmac
import http.client
import logging
import os
import secrets
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

from bpp.utils.logging import get_logger


def configure_loopback_boot(ctx: Any, port: int, args: argparse.Namespace) -> str:
    """Set the loopback boot secret and return the URL to open.

    Any local process can reach 127.0.0.1:port; without a secret the
    index page would hand it the owner token (other OS users, sandboxed
    apps). The desktop shell passes ``BPP_BOOT_SECRET``; ``bpp serve``
    makes one and prints the tokenized URL, as notebooks do.
    ``--open-loopback`` / ``BPP_OPEN_LOOPBACK=1`` keep the old open
    behaviour (the e2e runner, a dev reloading the page by hand).
    """
    log = get_logger(__name__)
    base = f"http://127.0.0.1:{port}"
    if getattr(args, "open_loopback", False) or os.environ.get("BPP_OPEN_LOOPBACK") == "1":
        ctx.boot_secret = None
        log.info("Loopback open: no boot key (--open-loopback)")
        return base
    # Taken out of the environment: every program the server starts
    # (ffmpeg, pip, the file manager) would inherit the key otherwise.
    secret = os.environ.get("BPP_BOOT_SECRET")
    os.environ.pop("BPP_BOOT_SECRET", None)
    ctx.boot_secret = secret or secrets.token_urlsafe(24)
    url = f"{base}/?_boot={ctx.boot_secret}"
    if secret:  # the desktop shell made it: it already knows
        replace_bare_url_banner(None)
        log.info("Loopback boot key supplied by the launcher")
    else:
        # To the terminal only: server.log is on disk for the run's
        # lifetime, and the key is what keeps other local users out.
        # Printed when Werkzeug reports the bind (its "Running on" line),
        # so a failed bind prints no link and the keyed link is the
        # last, most visible thing in the terminal.
        replace_bare_url_banner(url)
        log.info("Loopback boot key generated; the URL to open is printed when the port is bound")
    return url


class _KeyedUrlBanner(logging.Filter):
    """Swap Werkzeug's "Running on http://127.0.0.1:5001" lines for the
    keyed link: with a boot key in force the bare URL leads to the
    locked page, and the README tells the user to click the link the
    terminal prints. ``url`` None (the launcher knows the key) drops
    the lines without printing anything."""

    def __init__(self, url: str | None) -> None:
        super().__init__()
        self.url = url
        self.printed = False

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if "Running on" in msg:
            if self.url and not self.printed:
                self.printed = True
                print(
                    f"\nOpen Best Photo Picker at {self.url}\n"
                    "(the key is for this run only; the page without it is locked)",
                    flush=True,
                )
            return False
        return "Press CTRL+C" not in msg


def replace_bare_url_banner(url: str | None) -> None:
    werkzeug = logging.getLogger("werkzeug")
    for f in list(werkzeug.filters):
        if isinstance(f, _KeyedUrlBanner):
            werkzeug.removeFilter(f)
    werkzeug.addFilter(_KeyedUrlBanner(url))


# ── opening the browser ──────────────────────────────────────────────────


def _boot_check(port: int, nonce: str, *, tls: bool = False) -> str | None:
    """What the listener on 127.0.0.1:*port* answers to ``/boot-check``:
    its proof for *nonce* ("" when it sends none), or None when nothing
    answers. A request to this machine only, never anywhere else.
    With *tls* the certificate is not checked (it is often self-signed):
    the proof, not the certificate, is what tells our server apart."""
    conn: http.client.HTTPConnection
    if tls:
        import ssl

        conn = http.client.HTTPSConnection(
            "127.0.0.1", port, timeout=2, context=ssl._create_unverified_context()
        )
    else:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        conn.request("GET", f"/boot-check?nonce={nonce}")
        return conn.getresponse().getheader("X-Boot-Proof") or ""
    except (OSError, http.client.HTTPException):
        return None
    finally:
        conn.close()


def server_proves_key(port: int, boot_secret: str, *, tls: bool = False) -> bool:
    """Ask the listener on the port to prove it holds *boot_secret* (an
    HMAC over a fresh nonce; the key itself is not sent). False for any
    other listener, or none."""
    nonce = secrets.token_urlsafe(16)
    want = hmac.new(boot_secret.encode(), nonce.encode(), "sha256").hexdigest()
    return hmac.compare_digest(_boot_check(port, nonce, tls=tls) or "", want)


def open_when_ours(
    url: str,
    *,
    proves: Callable[[], bool],
    opener: Callable[[str], Any],
    wait: float = 0.25,
    tries: int = 80,
) -> bool:
    """Open *url* once ``proves()`` says our own server is answering.
    Gives up (and opens nothing) when it never does: the port is held by
    something else, and the link carries this run's key."""
    for _ in range(tries):
        if proves():
            opener(url)
            return True
        time.sleep(wait)
    get_logger(__name__).warning(
        "The browser was not opened: this run's server did not answer on 127.0.0.1 "
        "(another program holds the port, or the server listens on another address)"
    )
    return False


def open_browser_when_ours(
    url: str, port: int, boot_secret: str | None, *, tls: bool = False
) -> None:
    """Open the owner page in the browser, in the background, once this
    run's server is the one answering on the port."""
    import webbrowser

    def proves() -> bool:
        if boot_secret:
            return server_proves_key(port, boot_secret, tls=tls)
        # No key in force (--open-loopback): any answer will do.
        return _boot_check(port, "", tls=tls) is not None

    threading.Thread(
        target=open_when_ours,
        args=(url,),
        kwargs={"proves": proves, "opener": webbrowser.open},
        name="open-browser",
        daemon=True,
    ).start()


# ── the loopback address while listening on every interface ──────────────

_WILDCARDS = ("0.0.0.0", "", "::")


def hold_loopback(app: Any, host: str, port: int, ssl_context: Any) -> Any:
    """Serve *app* on 127.0.0.1 ourselves when the main server is about
    to listen on every interface.

    macOS and the BSDs let a second program bind 127.0.0.1:port next to
    a wildcard listener; the system then hands it every loopback
    connection, the owner's browser included, with the boot key and the
    owner token. A listener of our own on that exact address closes it.
    (Linux refuses such a bind itself, and would refuse ours too:
    nothing to do there.)

    Call before the main server binds. The address is taken at once,
    but nothing is answered on it until the main server reports that it
    is listening: if that bind fails the process exits, and a listener
    that had already proved the boot key would have sent the browser to
    whatever answers the port next. Returns the server, or None when
    not needed."""
    if host not in _WILDCARDS or not (sys.platform == "darwin" or "bsd" in sys.platform):
        return None
    from werkzeug.serving import make_server

    from bpp.web.wsgi_bind import bind_without_reverse_dns

    bind_without_reverse_dns()
    server = make_server("127.0.0.1", port, app, threaded=True, ssl_context=ssl_context)
    main_is_up = _MainServerUp()
    logging.getLogger("werkzeug").filters.insert(0, main_is_up)  # ahead of the banner filter

    def serve() -> None:
        # A failed main bind ends the process within milliseconds; if the
        # line is never seen (logging configured away), serve anyway.
        main_is_up.seen.wait(timeout=5)
        server.serve_forever()

    threading.Thread(target=serve, name="loopback-listener", daemon=True).start()
    get_logger(__name__).info("Holding 127.0.0.1:%d as well, so no other program can", port)
    return server


class _MainServerUp(logging.Filter):
    """Notes Werkzeug's "Running on" line: the main server has bound."""

    def __init__(self) -> None:
        super().__init__()
        self.seen = threading.Event()

    def filter(self, record: logging.LogRecord) -> bool:
        if "Running on" in record.getMessage():
            self.seen.set()
        return True

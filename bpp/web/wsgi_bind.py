"""How the Werkzeug server is set up: binding, TLS, idle connections.

Bind without a reverse DNS lookup:

``http.server.HTTPServer.server_bind``, which Werkzeug's servers inherit,
looks up the fully qualified name of the bound host (``socket.getfqdn``)
only to fill ``server_name``. Werkzeug never reads that attribute: the
WSGI ``SERVER_NAME`` comes from ``server_address``. On macOS the lookup
can go to multicast DNS and block. In the desktop app's server it held
the port unopened for about 35 s while the app was waiting for it. So
the bind skips the lookup and records the host as given.
"""

from __future__ import annotations

import contextlib
import http.server
import logging
import socketserver
from typing import Any

_log = logging.getLogger(__name__)

#: Seconds a connection may stay silent while the server waits for its
#: request (or, over TLS, its handshake) before it is dropped. Without
#: it, a caller that connects and sends nothing keeps a thread and a
#: socket for as long as it likes. It does not stop a caller that keeps
#: sending a byte now and then: that needs a cap on connections.
CONNECTION_TIMEOUT_S = 120


def _server_bind(self: socketserver.TCPServer) -> None:
    # http.server.HTTPServer.server_bind, minus socket.getfqdn(host).
    socketserver.TCPServer.server_bind(self)
    host, port = self.server_address[:2]
    self.server_name = host  # type: ignore[attr-defined]
    self.server_port = port  # type: ignore[attr-defined]


def bind_without_reverse_dns() -> None:
    """Make Werkzeug's servers in this process bind without the lookup.

    Only while Werkzeug inherits ``server_bind`` from ``http.server``
    unchanged: if a Werkzeug release brings its own, replacing it could
    drop what it does, so the bind is left alone (with the lookup) and
    tests/test_wsgi_bind.py fails until this is looked at again.
    """
    from werkzeug.serving import BaseWSGIServer

    if BaseWSGIServer.server_bind is _server_bind:
        return
    if BaseWSGIServer.server_bind is not http.server.HTTPServer.server_bind:
        return
    BaseWSGIServer.server_bind = _server_bind  # type: ignore[method-assign]


class _HandshakeOnFirstRead:
    """An SSL context whose server sockets shake hands when a
    connection is first read, on that connection's own thread.

    Werkzeug wraps the listening socket, so by default the handshake
    runs inside ``accept()`` on the one accepting thread: a caller that
    connects and sends nothing stops everyone else from connecting."""

    def __init__(self, context: Any) -> None:
        self._context = context

    def wrap_socket(self, sock: Any, **kwargs: Any) -> Any:
        kwargs["do_handshake_on_connect"] = False
        return self._context.wrap_socket(sock, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._context, name)


def lazy_tls(ssl_context: Any) -> Any:
    """The ``ssl_context`` to hand to Werkzeug: None stays None; "adhoc",
    a (cert, key) pair or a context become a context that does not
    shake hands on the accepting thread."""
    if ssl_context is None:
        return None
    from werkzeug.serving import generate_adhoc_ssl_context, load_ssl_context

    if ssl_context == "adhoc":
        ssl_context = generate_adhoc_ssl_context()
    elif isinstance(ssl_context, tuple):
        ssl_context = load_ssl_context(*ssl_context)
    return _HandshakeOnFirstRead(ssl_context)


def request_handler() -> type:
    """Werkzeug's request handler, with a limit on how long a
    connection may sit silent before its request has arrived."""
    from werkzeug.serving import WSGIRequestHandler

    class QuietConnectionsDropped(WSGIRequestHandler):
        timeout = CONNECTION_TIMEOUT_S

        def run_wsgi(self) -> None:
            # The limit is for a caller that never sends its request. Once
            # the request is in, the answer takes as long as the reader
            # needs: a socket timeout would count the whole write of a
            # large reply, and cut off a phone that is merely slow.
            self.connection.settimeout(None)
            try:
                super().run_wsgi()
            finally:
                with contextlib.suppress(OSError):
                    self.connection.settimeout(self.timeout)

        def log_error(self, format: str, *args: Any) -> None:
            # A handshake that fails (a browser that doesn't trust the
            # certificate yet, plain http on the TLS port) and a caller
            # that went quiet are everyday events any LAN host can
            # cause: not ERROR lines in the owner's log.
            if format.startswith(("SSL error occurred", "Request timed out")):
                _log.debug(format, *args)
                return
            super().log_error(format, *args)

    return QuietConnectionsDropped

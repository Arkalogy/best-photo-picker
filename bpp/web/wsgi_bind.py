"""Bind the Werkzeug server without a reverse DNS lookup.

``http.server.HTTPServer.server_bind``, which Werkzeug's servers inherit,
looks up the fully qualified name of the bound host (``socket.getfqdn``)
only to fill ``server_name``. Werkzeug never reads that attribute: the
WSGI ``SERVER_NAME`` comes from ``server_address``. On macOS the lookup
can go to multicast DNS and block. In the desktop app's server it held
the port unopened for about 35 s while the app was waiting for it. So
the bind skips the lookup and records the host as given.
"""

from __future__ import annotations

import http.server
import socketserver


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

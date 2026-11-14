"""The server binds without a reverse DNS lookup of its host. On macOS
that lookup (http.server's ``socket.getfqdn``) went to multicast DNS and
held the desktop app's server port unopened for about 35 s."""

from __future__ import annotations

import socket
from pathlib import Path

from werkzeug.serving import BaseWSGIServer, make_server

from bpp.web import wsgi_bind

ROOT = Path(__file__).resolve().parent.parent


def _app(environ, start_response):
    start_response("200 OK", [("Content-Type", "text/plain")])
    return [environ["SERVER_NAME"].encode()]


def test_bind_skips_the_reverse_lookup_and_keeps_the_address(monkeypatch):
    original = BaseWSGIServer.server_bind
    monkeypatch.setattr(BaseWSGIServer, "server_bind", original)  # restored afterwards

    def no_lookup(*_a, **_k):
        raise AssertionError("socket.getfqdn was called")

    monkeypatch.setattr(socket, "getfqdn", no_lookup)
    wsgi_bind.bind_without_reverse_dns()
    server = make_server("127.0.0.1", 0, _app, threaded=True)
    try:
        port = server.socket.getsockname()[1]
        assert server.server_name == "127.0.0.1"
        assert server.server_port == port
        # The WSGI SERVER_NAME never came from the lookup. (A raw socket:
        # the suite blocks urllib.)
        import threading

        t = threading.Thread(target=server.handle_request, daemon=True)
        t.start()
        with socket.create_connection(("127.0.0.1", port), timeout=5) as conn:
            conn.sendall(b"GET / HTTP/1.0\r\nHost: x\r\n\r\n")
            reply = b""
            while chunk := conn.recv(4096):
                reply += chunk
        t.join(5)
        assert reply.startswith(b"HTTP/1.1 200") and b"\r\n127.0.0.1\r\n" in reply, reply
    finally:
        server.server_close()


def test_werkzeug_still_binds_with_http_servers_own_method():
    """The patch replaces http.server's bind only. If Werkzeug ever ships
    its own server_bind, the patch steps aside; this fails so the change
    is looked at instead of the start-up lookup coming back unnoticed."""
    import http.server
    import inspect

    import werkzeug.serving

    # Read Werkzeug's source, not the class: another test in this worker
    # may already have run the patch.
    assert "def server_bind" not in inspect.getsource(werkzeug.serving)
    assert http.server.HTTPServer in BaseWSGIServer.__mro__


def test_the_patch_steps_aside_for_a_bind_werkzeug_brings(monkeypatch):
    def werkzeugs_own(self):
        raise AssertionError("not called here")

    monkeypatch.setattr(BaseWSGIServer, "server_bind", werkzeugs_own)
    wsgi_bind.bind_without_reverse_dns()
    assert BaseWSGIServer.server_bind is werkzeugs_own


def test_both_serve_paths_bind_without_the_lookup_before_running():
    src = (ROOT / "bpp" / "commands" / "serve.py").read_text()
    runs = src.split("app.run(")
    assert len(runs) == 3, "do_web and do_serve each start one server"
    for before in runs[:2]:
        tail = before.rstrip().splitlines()[-1].strip()
        assert tail == "bind_without_reverse_dns()", tail

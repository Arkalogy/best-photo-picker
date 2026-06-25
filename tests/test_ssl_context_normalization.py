"""R8-M13: ssl_context list-from-YAML normalized to tuple.

YAML loads sequences as Python lists, but Werkzeug's BaseWSGIServer
requires `isinstance(ssl_context, tuple)` to call
`load_ssl_context(*ssl_context)` — with a list it falls through to
`wrap_socket` and raises AttributeError on the first connection.
Server boots fine but dies on the first hit, with a confusing
stack trace.

The fix normalizes lists of length 2 (the documented `[cert,
key]` YAML shape) to tuples before passing to `app.run`. Anything
else (None, "adhoc", an actual SSLContext) passes through, and a
malformed list (wrong arity) gets disabled with a clear log line.

This test class covers the normalization logic by re-applying it
inline (the production code embeds it in `do_serve` which is
hard to invoke headlessly without booting Flask). Source-scan
locks the structural fix.
"""

from __future__ import annotations

import logging
from pathlib import Path


def _normalize_for_test(ssl_context, log):
    """Mirror the normalization logic in bpp/commands.py:do_serve.
    Lifted into a local helper so the test exercises it without
    booting Flask. Source-scan elsewhere keeps production in sync."""
    if isinstance(ssl_context, list) and len(ssl_context) == 2:
        ssl_context = tuple(ssl_context)
    elif isinstance(ssl_context, list):
        log.error(
            "config.ssl_context is a list of length %d; expected [cert_path, "
            "key_path]. Disabling SSL.",
            len(ssl_context),
        )
        ssl_context = None
    return ssl_context


class TestSslContextNormalization:
    def test_yaml_list_pair_becomes_tuple(self, caplog):
        log = logging.getLogger("test_r8m13")
        result = _normalize_for_test(["/path/cert.pem", "/path/key.pem"], log)
        assert isinstance(result, tuple)
        assert result == ("/path/cert.pem", "/path/key.pem")

    def test_none_passes_through(self):
        log = logging.getLogger("test_r8m13")
        assert _normalize_for_test(None, log) is None

    def test_adhoc_string_passes_through(self):
        """The literal "adhoc" tells Werkzeug to auto-generate a
        self-signed cert. Not great prod practice, but valid input;
        normalization must not eat the string."""
        log = logging.getLogger("test_r8m13")
        assert _normalize_for_test("adhoc", log) == "adhoc"

    def test_already_tuple_passes_through(self):
        log = logging.getLogger("test_r8m13")
        result = _normalize_for_test(("/path/cert.pem", "/path/key.pem"), log)
        assert result == ("/path/cert.pem", "/path/key.pem")
        assert isinstance(result, tuple)

    def test_wrong_arity_list_disabled_with_log(self, caplog):
        """A list of 1 or 3+ elements isn't the documented shape —
        disable SSL and log loudly so the operator sees the misconfig
        instead of getting a 500 on the first connection."""
        log = logging.getLogger("bpp.commands")  # caplog scopes to logger
        with caplog.at_level(logging.ERROR, logger="bpp.commands"):
            result = _normalize_for_test(["/only-cert.pem"], log)
        assert result is None
        assert any("ssl_context is a list of length 1" in r.message for r in caplog.records)

    def test_production_code_contains_normalization(self):
        """Source-scan: confirm the normalization is wired in
        do_serve. This is the actual call path; the helper above
        is only for unit testability."""
        # do_serve moved to bpp/commands/serve.py during the v0.1 split.
        src = Path("bpp/commands/serve.py").read_text()
        assert "isinstance(ssl_context, list) and len(ssl_context) == 2" in src, (
            "do_serve must normalize 2-element lists to tuples"
        )
        assert "ssl_context = tuple(ssl_context)" in src

"""R9-fr-M2: bind-address default depends on the LAN-share toggle.

`bpp serve` historically defaulted `--host` to `0.0.0.0`, which
means a casual run on a public network (coffee-shop Wi-Fi, hotel,
conference) makes the server visible to a port scan even though
the auth layer would reject any non-loopback request. The audit
asked us to default loopback-only when sharing is OFF and let
the share toggle (or an explicit `--host 0.0.0.0`) opt back into
LAN binding.

These tests pin the resolution logic by stubbing
`app.run` and observing the host arg it receives.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from bpp import commands


def _dummy_timer(*_a, **_kw):
    return type("T", (), {"start": lambda self: None})()


@pytest.fixture
def fake_library(tmp_path):
    """Build the minimum on-disk skeleton `do_serve` needs to run."""
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "data").mkdir()
    (lib / "logs").mkdir()
    (lib / "photos").mkdir()
    (lib / "cache").mkdir()
    return lib


def _run_serve(library, **arg_overrides):
    """Run `do_serve` with `app.run` stubbed; return the `host` it bound to."""
    captured: dict[str, str] = {}

    def _capture(self, host=None, port=None, debug=False, ssl_context=None, **_kw):
        captured["host"] = host

    args_ns = type("Args", (), {})()
    args_ns.library = str(library)
    args_ns.host = arg_overrides.get("host")
    args_ns.port = 5001
    args_ns.no_browser = True
    args_ns.config = None
    args_ns.debug = False

    with (
        patch("flask.Flask.run", _capture),
        patch("webbrowser.open", lambda *_a, **_kw: True),
        patch("threading.Timer", lambda *_a, **_kw: type("T", (), {"start": lambda self: None})()),
    ):
        commands.do_serve(args_ns)
    return captured.get("host")


class TestHostDefault:
    def test_loopback_when_lan_sharing_off(self, fake_library):
        """Default: sharing OFF → 127.0.0.1, not 0.0.0.0."""
        host = _run_serve(fake_library)
        assert host == "127.0.0.1", (
            f"Default with sharing OFF should be loopback; got {host!r}. "
            "Round-9 audit fix: 0.0.0.0 used to be the unconditional default, "
            "exposing the service to port scans on coffee-shop Wi-Fi."
        )

    def test_zero_bind_when_lan_sharing_on(self, fake_library):
        """Default: sharing ON → 0.0.0.0 so paired phones can connect."""
        from bpp.db.connection import close_all_connections, init_db
        from bpp.db.settings import set_setting

        # Pre-init the library DB and enable sharing.
        db_path = str(fake_library / "data" / "photopicker.db")
        conn = init_db(db_path)
        set_setting(conn, "lan_sharing_enabled", "1")
        close_all_connections()

        host = _run_serve(fake_library)
        assert host == "0.0.0.0", (
            f"Default with sharing ON should bind every interface; got {host!r}."
        )

    def test_explicit_host_overrides_default(self, fake_library):
        """User-supplied `--host` always wins, regardless of share toggle."""
        host = _run_serve(fake_library, host="0.0.0.0")
        assert host == "0.0.0.0"

        host = _run_serve(fake_library, host="127.0.0.1")
        assert host == "127.0.0.1"

    def test_argparse_default_is_none_sentinel(self):
        """Argparse default is the `None` sentinel, not a literal address —
        the resolution logic in do_serve distinguishes "user didn't pass
        --host" from "user passed --host 127.0.0.1 explicitly."""
        from bpp.cli import build_parser

        parser = build_parser()
        ns = parser.parse_args(["serve"])
        assert ns.host is None


class TestWorldWritableLibraryWarning:
    def test_warns_when_library_is_world_writable(self, fake_library, caplog):
        """do_serve must emit a WARNING when the library dir has mode 0o002 (world-writable)."""
        import logging
        import stat

        # Make library world-writable
        fake_library.chmod(fake_library.stat().st_mode | stat.S_IWOTH)

        with caplog.at_level(logging.WARNING, logger="bpp.commands"):
            _run_serve(fake_library)

        # Restore
        fake_library.chmod(fake_library.stat().st_mode & ~stat.S_IWOTH)

        world_writable_warnings = [
            r for r in caplog.records if "world-writable" in r.message.lower()
        ]
        assert world_writable_warnings, (
            "Expected a warning about world-writable library path, got none"
        )

    def test_no_warning_for_normal_permissions(self, fake_library, caplog):
        """No warning for a normally-permissioned directory."""
        import logging
        import stat

        # Ensure NOT world-writable
        fake_library.chmod(fake_library.stat().st_mode & ~stat.S_IWOTH)

        with caplog.at_level(logging.WARNING, logger="bpp.commands"):
            _run_serve(fake_library)

        world_writable_warnings = [
            r for r in caplog.records if "world-writable" in r.message.lower()
        ]
        assert not world_writable_warnings


class TestWebCommandHost:
    def test_web_defaults_to_loopback(self, fake_library, tmp_path):
        """bpp web binds to 127.0.0.1 by default."""
        from bpp import commands

        captured: dict = {}

        def _cap(self, host=None, port=None, **_kw):
            captured["host"] = host

        args = type("Args", (), {})()
        args.input = str(fake_library)
        args.workdir = str(tmp_path / "workdir")
        args.port = 5001
        args.no_browser = True
        args.config = None
        args.debug = False
        args.host = None

        with (
            patch("flask.Flask.run", _cap),
            patch("webbrowser.open", lambda *_a, **_kw: True),
            patch("threading.Timer", _dummy_timer),
        ):
            commands.do_web(args)

        assert captured.get("host") == "127.0.0.1"

    def test_web_respects_explicit_host(self, fake_library, tmp_path):
        """bpp web binds to the requested address when --host is passed."""
        from bpp import commands

        captured: dict = {}

        def _cap(self, host=None, port=None, **_kw):
            captured["host"] = host

        args = type("Args", (), {})()
        args.input = str(fake_library)
        args.workdir = str(tmp_path / "workdir")
        args.port = 5001
        args.no_browser = True
        args.config = None
        args.debug = False
        args.host = "0.0.0.0"

        with (
            patch("flask.Flask.run", _cap),
            patch("webbrowser.open", lambda *_a, **_kw: True),
            patch("threading.Timer", _dummy_timer),
        ):
            commands.do_web(args)

        assert captured.get("host") == "0.0.0.0"

    def test_web_cli_has_host_argument(self):
        """bpp web argparser must define --host."""
        from bpp.cli import build_parser

        parser = build_parser()
        ns = parser.parse_args(["web", "--host", "0.0.0.0"])
        assert ns.host == "0.0.0.0"

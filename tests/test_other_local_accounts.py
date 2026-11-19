"""Other accounts and programs on the same computer stay out.

The owner page is served on 127.0.0.1, which every local account and
program can reach. What keeps them out is the per-run boot key and file
permissions; these tests cover the ways around either.
"""

from __future__ import annotations

import logging
import os
import socket
import stat
import sys
import types

import pytest

# ── the boot key is only good for the account that runs the server ──────


def _hex(address):
    """An address as /proc/net/tcp prints it on this machine."""
    import ipaddress

    raw = ipaddress.ip_address(address).packed
    return "".join(
        f"{int.from_bytes(raw[i : i + 4], sys.byteorder):08X}" for i in range(0, len(raw), 4)
    )


def _proc_net(tmp_path, rows, table="tcp"):
    """A stand-in socket table: one line per (local address, local port,
    remote port, uid)."""
    head = (
        "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt"
        "   uid  timeout inode\n"
    )
    body = "".join(
        f"   {i}: {_hex(addr)}:{local:04X} {_hex(addr)}:{remote:04X} 01 00000000:00000000"
        f" 00:00000000 00000000  {uid}        0 12345 1 0000000000000000 20 4 30 10 -1\n"
        for i, (addr, local, remote, uid) in enumerate(rows)
    )
    net = tmp_path / "net"
    net.mkdir(exist_ok=True)
    (net / table).write_text(head + body)
    return str(tmp_path)


@pytest.fixture
def not_root(monkeypatch):
    """The uid the tests treat as 'this account' (never root: a root
    server does not check, see below)."""
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    return 1000


def test_a_connection_made_by_another_account_is_recognised(tmp_path, not_root):
    from bpp.web.loopback_peer import peer_is_another_account

    me = not_root
    proc = _proc_net(
        tmp_path,
        [
            ("127.0.0.1", 40001, 5001, me),
            ("127.0.0.1", 40002, 5001, 1001),
            ("127.0.0.1", 5001, 40002, me),
        ],
    )
    assert peer_is_another_account("127.0.0.1", 40002, 5001, proc_root=proc) is True
    assert peer_is_another_account("127.0.0.1", 40001, 5001, proc_root=proc) is False


def test_the_same_port_on_another_loopback_address_is_not_ours(tmp_path, not_root):
    """Another account can connect from 127.0.0.2 with the port number
    the owner's browser is using: only its own row counts, wherever it
    is listed."""
    from bpp.web.loopback_peer import peer_is_another_account

    me = not_root
    for rows in (
        [("127.0.0.1", 40002, 5001, me), ("127.0.0.2", 40002, 5001, 1001)],
        [("127.0.0.2", 40002, 5001, 1001), ("127.0.0.1", 40002, 5001, me)],
    ):
        proc = _proc_net(tmp_path, rows)
        assert peer_is_another_account("127.0.0.2", 40002, 5001, proc_root=proc) is True, rows
        assert peer_is_another_account("127.0.0.1", 40002, 5001, proc_root=proc) is False, rows


def test_the_server_port_is_part_of_the_match(tmp_path, not_root):
    from bpp.web.loopback_peer import peer_is_another_account

    proc = _proc_net(tmp_path, [("127.0.0.1", 40002, 6000, 1001)])
    assert peer_is_another_account("127.0.0.1", 40002, 6000, proc_root=proc) is True
    assert peer_is_another_account("127.0.0.1", 40002, 5001, proc_root=proc) is False


def test_ipv6_and_ipv4_mapped_connections_are_read(tmp_path, not_root):
    from bpp.web.loopback_peer import peer_is_another_account

    proc = _proc_net(
        tmp_path,
        [("::1", 40003, 5001, 1001), ("::ffff:127.0.0.1", 40004, 5001, 1001)],
        table="tcp6",
    )
    assert peer_is_another_account("::1", 40003, 5001, proc_root=proc) is True
    assert peer_is_another_account("127.0.0.1", 40004, 5001, proc_root=proc) is True
    assert peer_is_another_account("::ffff:127.0.0.1", 40004, 5001, proc_root=proc) is True


def test_an_unknown_connection_is_not_blamed(tmp_path, not_root):
    from bpp.web.loopback_peer import peer_is_another_account

    proc = _proc_net(tmp_path, [("127.0.0.1", 40001, 5001, 1001), ("127.0.0.1", 40005, 5001, 0)])
    assert peer_is_another_account("127.0.0.1", 49999, 5001, proc_root=proc) is False  # not listed
    assert (
        peer_is_another_account("127.0.0.1", 40001, 5001, proc_root=str(tmp_path / "nope")) is False
    )
    assert peer_is_another_account("127.0.0.1", None, 5001, proc_root=proc) is False
    assert peer_is_another_account("not-an-address", 40001, 5001, proc_root=proc) is False
    # A root-owned socket is a system relay (WSL), not another person.
    assert peer_is_another_account("127.0.0.1", 40005, 5001, proc_root=proc) is False


def test_a_server_run_as_root_does_not_guess(tmp_path, monkeypatch):
    from bpp.web.loopback_peer import peer_is_another_account

    monkeypatch.setattr(os, "geteuid", lambda: 0)
    proc = _proc_net(tmp_path, [("127.0.0.1", 40001, 5001, 1001)])
    assert peer_is_another_account("127.0.0.1", 40001, 5001, proc_root=proc) is False


@pytest.fixture
def served(tmp_path):
    from bpp.web.app import create_app

    lib = str(tmp_path / "lib")
    os.makedirs(os.path.join(lib, "photos"))
    app = create_app(library_path=lib)
    app.config["TESTING"] = True
    ctx = app.extensions["bpp"]
    ctx.boot_secret = "the-boot-key"
    return app, ctx


def test_the_boot_key_from_another_account_gets_the_locked_page(served, monkeypatch):
    app, ctx = served
    ctx.port = 5001  # not what the request came in on: the request's own port is used
    seen = []

    def other(addr, port, server_port, **_kw):
        seen.append((addr, port, server_port))
        return True

    monkeypatch.setattr("bpp.web.boot_key.peer_is_another_account", other)
    with app.test_client() as c:
        resp = c.get(
            "/?_boot=the-boot-key",
            base_url="http://127.0.0.1:6000",
            environ_base={"REMOTE_ADDR": "127.0.0.1", "REMOTE_PORT": "40002"},
        )
    assert resp.status_code == 403
    assert seen == [("127.0.0.1", 40002, 6000)]


def test_the_boot_key_from_the_owner_opens_the_page(served):
    app, ctx = served
    with app.test_client() as c:
        resp = c.get("/?_boot=the-boot-key")
    assert resp.status_code == 200
    assert ctx.auth_token in resp.get_data(as_text=True)
    # Thumbnails are cached for a year: the keyed page must not wipe them.
    assert "Clear-Site-Data" not in resp.headers


def test_behind_the_owners_reverse_proxy_the_socket_is_not_judged(served, monkeypatch):
    app, _ctx = served
    monkeypatch.setattr(
        "bpp.web.boot_key.peer_is_another_account",
        lambda *a, **k: pytest.fail("the proxy's socket says nothing about the caller"),
    )
    with app.test_client() as c:
        resp = c.get(
            "/?_boot=the-boot-key",
            environ_base={"werkzeug.proxy_fix.orig": {"REMOTE_ADDR": "127.0.0.1"}},
        )
    assert resp.status_code == 200


# ── the share link is a device's credential, never the owner's ───────────


def test_the_share_token_from_this_computer_is_not_the_owner(served):
    from bpp.web.share import get_share_token
    from bpp.web.share_runtime import set_lan_sharing_enabled

    app, ctx = served
    app.config["TESTING"] = False  # the request boundary is what is under test
    with app.app_context():
        conn = ctx.get_conn()
        set_lan_sharing_enabled(conn, True)
        token = get_share_token(conn)
    with app.test_client() as c:
        owner_only = c.get("/api/v1/share/devices", headers={"X-Auth-Token": token})
        with_app_token = c.get("/api/v1/share/devices", headers={"X-Auth-Token": ctx.auth_token})
    assert owner_only.status_code in (401, 403)
    assert with_app_token.status_code == 200


# ── launching ───────────────────────────────────────────────────────────


def test_the_boot_key_does_not_stay_in_the_environment(monkeypatch):
    from bpp.commands.serve_launch import configure_loopback_boot

    monkeypatch.setenv("BPP_BOOT_SECRET", "from-the-launcher")
    monkeypatch.delenv("BPP_OPEN_LOOPBACK", raising=False)
    ctx = types.SimpleNamespace(boot_secret=None)
    url = configure_loopback_boot(ctx, 5001, types.SimpleNamespace(open_loopback=False))
    assert ctx.boot_secret == "from-the-launcher" and "from-the-launcher" in url
    assert "BPP_BOOT_SECRET" not in os.environ  # programs the server starts don't inherit it


def test_the_browser_opens_only_once_our_own_server_answers():
    from bpp.commands.serve_launch import open_when_ours

    opened = []
    answers = iter([False, False, True])
    assert open_when_ours(
        "http://x/?_boot=k", proves=lambda: next(answers), opener=opened.append, wait=0, tries=5
    )
    assert opened == ["http://x/?_boot=k"]


def test_the_browser_stays_closed_when_someone_else_holds_the_port():
    from bpp.commands.serve_launch import open_when_ours

    opened = []
    assert not open_when_ours(
        "http://x/?_boot=k", proves=lambda: False, opener=opened.append, wait=0, tries=3
    )
    assert opened == []


def test_a_listener_that_does_not_know_the_key_proves_nothing(monkeypatch):
    from bpp.commands import serve_launch

    monkeypatch.setattr(serve_launch, "_boot_check", lambda port, nonce, **_kw: "0" * 64)
    assert serve_launch.server_proves_key(5001, "the-key") is False
    monkeypatch.setattr(
        serve_launch, "_boot_check", lambda port, nonce, **_kw: None
    )  # nothing answers
    assert serve_launch.server_proves_key(5001, "the-key") is False


def test_our_own_server_proves_the_key(served, monkeypatch):
    from bpp.commands import serve_launch

    app, _ctx = served

    def ask(port, nonce, **_kw):
        with app.test_client() as c:
            return c.get(f"/boot-check?nonce={nonce}").headers.get("X-Boot-Proof") or ""

    monkeypatch.setattr(serve_launch, "_boot_check", ask)
    assert serve_launch.server_proves_key(5001, "the-boot-key") is True
    assert serve_launch.server_proves_key(5001, "another-key") is False


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="macOS lets a second program take 127.0.0.1 next to a wildcard listener",
)
def test_nobody_else_can_take_the_loopback_address_while_sharing_is_on():
    from flask import Flask

    from bpp.commands.serve_launch import hold_loopback

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    app = Flask(__name__)
    app.add_url_rule("/ping", view_func=lambda: "pong")
    from werkzeug._internal import _log as werkzeug_says  # how the main server reports its bind

    werkzeug_log = logging.getLogger("werkzeug")
    held = hold_loopback(app, "0.0.0.0", port, None)
    assert held is not None

    def ask():
        client = socket.create_connection(("127.0.0.1", port), timeout=1.5)
        try:
            client.sendall(b"GET /ping HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
            reply = b""
            while chunk := client.recv(4096):
                reply += chunk
            return reply
        finally:
            client.close()

    try:
        wildcard = socket.socket()  # the main server's bind still works
        wildcard.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        wildcard.bind(("0.0.0.0", port))
        wildcard.listen(1)
        other = socket.socket()
        other.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        with pytest.raises(OSError):
            other.bind(("127.0.0.1", port))
        other.close()
        # Until the main server says it is listening, the address is held
        # but nothing is answered on it (no proof for a run that may exit).
        with pytest.raises(TimeoutError):
            ask()
        werkzeug_says("info", " * Running on http://127.0.0.1:%d", port)
        assert ask().endswith(b"pong")  # our listener, not the wildcard socket
        wildcard.close()
    finally:
        werkzeug_says("info", " * Running on (test cleanup)")  # the listener starts, so it can stop
        held.shutdown()
        held.server_close()
        werkzeug_log.filters[:] = [
            f for f in werkzeug_log.filters if type(f).__name__ != "_MainServerUp"
        ]


def test_no_second_listener_when_the_server_is_loopback_only():
    from bpp.commands.serve_launch import hold_loopback

    assert hold_loopback(object(), "127.0.0.1", 5001, None) is None


# ── files ───────────────────────────────────────────────────────────────


def test_the_owner_is_told_when_the_database_stays_readable_by_others(
    tmp_path, monkeypatch, caplog
):
    from bpp.db import connection

    db = tmp_path / "photopicker.db"
    db.write_bytes(b"x")
    os.chmod(db, 0o644)
    monkeypatch.setattr(connection.os, "chmod", lambda *a, **k: None)  # a drive that ignores it
    connection._perms_warned.clear()
    with caplog.at_level(logging.WARNING):
        connection._restrict_db_perms(str(db))
        connection._restrict_db_perms(str(db))
    warnings = [r.getMessage() for r in caplog.records if "other accounts" in r.getMessage()]
    assert len(warnings) == 1, warnings  # said once, not on every open


@pytest.mark.parametrize(
    ("source_mode", "kept"),
    [(0o4755, 0o755), (0o2755, 0o755), (0o666, 0o644), (0o1777, 0o755), (0o600, 0o600)],
)
def test_copies_do_not_keep_special_or_shared_write_bits(tmp_path, source_mode, kept):
    from bpp.db.library_copy import _copy_atomically
    from bpp.output.export_modes import _copy_bytes

    src = tmp_path / "clip.mov"
    src.write_bytes(b"data")
    os.chmod(src, source_mode)
    _copy_bytes(str(src), str(tmp_path / "exported.tmp"))
    _copy_atomically(str(src), str(tmp_path / "imported.tmp"), str(tmp_path / "imported.mov"))
    assert stat.S_IMODE(os.stat(tmp_path / "exported.tmp").st_mode) == kept
    assert stat.S_IMODE(os.stat(tmp_path / "imported.mov").st_mode) == kept


@pytest.mark.parametrize("name", ["held-99999999999999999999.txt", "held-²³.txt", "held-0.txt"])
def test_a_stray_file_in_the_hold_folder_does_not_stop_the_app(tmp_path, name):
    from bpp.scoring import install_hold

    (tmp_path / name).write_text("x")
    install_hold._sweep_stale_files(tmp_path)  # must not raise


def test_the_hold_list_is_not_written_through_a_planted_link(tmp_path):
    from bpp.scoring import install_hold

    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    own = tmp_path / "hold" / "held-123.txt"
    own.parent.mkdir()
    os.symlink(victim, own.with_suffix(".tmp"))
    install_hold._write_held_file(own, frozenset({"torch"}))
    assert victim.read_text() == "keep me"
    assert own.read_text() == "torch"


def _run_setup(monkeypatch, *argv):
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "scripts" / "setup_e2e_library.py"
    spec = importlib.util.spec_from_file_location("setup_e2e_library", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys, "argv", ["setup_e2e_library.py", *argv])
    return module.main()


def test_the_fixture_script_does_not_delete_through_a_link(tmp_path, monkeypatch):
    target = tmp_path / "precious"
    target.mkdir()
    (target / "keep.txt").write_text("x")
    link = tmp_path / "bpp_e2e_library"
    os.symlink(target, link)
    assert _run_setup(monkeypatch, "--library", str(link), "--reset") != 0
    assert (target / "keep.txt").read_text() == "x"
    assert sorted(os.listdir(target)) == ["keep.txt"]


def test_the_fixture_script_only_resets_a_folder_it_made(tmp_path, monkeypatch):
    folder = tmp_path / "photos-of-mine"
    folder.mkdir()
    (folder / "keep.txt").write_text("x")
    assert _run_setup(monkeypatch, "--library", str(folder), "--reset") != 0
    assert (folder / "keep.txt").read_text() == "x"

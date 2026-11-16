"""Sharing state in a library is honoured only when this installation made it.

The share link, the sharing switch and the approved devices live in the
library database. A library folder prepared by someone else could carry
all three, and opening it would give its author LAN access. Owner writes
are now sealed with a key kept outside every library; an opened library
whose state isn't sealed by that key has it removed.
"""

from __future__ import annotations

import os
import stat
import time

import pytest

from bpp.web import share_seal
from bpp.web import share_seal_store as store

PLANTED_TOKEN = "a" * 64
PLANTED_FP = "planted-device-fingerprint"


class _Req:
    def __init__(self, path, remote, token="", fp=None):
        self.path = path
        self.remote_addr = remote
        self.headers = {"X-Auth-Token": token} if token else {}
        self.args = {}
        self.cookies = {"bpp_share_fp": fp} if fp else {}


def _planted_library(path) -> str:
    """A library whose database already says: sharing on, this link, this
    approved device. What someone else's prepared folder would carry."""
    from bpp.db.connection import close_all_connections, get_db, init_db
    from bpp.db.library import ensure_library_dirs

    lib = str(path)
    os.makedirs(lib, exist_ok=True)
    dirs = ensure_library_dirs(lib)
    db_path = os.path.join(dirs["data"], "photopicker.db")
    init_db(db_path)
    conn = get_db(db_path)
    now = int(time.time())
    conn.execute(
        "INSERT INTO settings (key, value) VALUES ('lan_share_token', ?)", (PLANTED_TOKEN,)
    )
    conn.execute("INSERT INTO settings (key, value) VALUES ('lan_sharing_enabled', '1')")
    conn.execute(
        "INSERT INTO share_devices (fingerprint, name, first_seen, last_seen, trusted_at)"
        " VALUES (?, 'phone', ?, ?, ?)",
        (PLANTED_FP, now, now, now),
    )
    conn.commit()
    close_all_connections()
    return lib


def _open(lib):
    from bpp.db.library import ensure_library_dirs
    from bpp.web.app import create_app

    # As `bpp serve` opens it: the library's own database.
    app = create_app(workdir=ensure_library_dirs(lib)["data"], library_path=lib)
    app.config["TESTING"] = True
    return app, app.extensions["bpp"]


def _close():
    from bpp.db.connection import close_all_connections

    close_all_connections(new_epoch=True)  # as a restart: the next open sets up again


def _lan_decision(ctx, token, fp):
    from bpp.web.share import authorize_request

    return authorize_request(_Req("/api/v1/photos", "192.168.1.50", token, fp), ctx).decision


def test_a_library_from_elsewhere_does_not_bring_its_sharing_with_it(tmp_path):
    from bpp.commands.serve import _bind_host
    from bpp.web.share import AuthDecision, get_share_token, is_lan_sharing_enabled

    app, ctx = _open(_planted_library(tmp_path / "lib"))
    with app.app_context():
        conn = ctx.get_conn()
        assert not is_lan_sharing_enabled(conn)
        assert _bind_host(None, is_lan_sharing_enabled(conn)) == "127.0.0.1"
        assert _lan_decision(ctx, PLANTED_TOKEN, PLANTED_FP) is AuthDecision.DENY
        assert conn.execute("SELECT COUNT(*) FROM share_devices").fetchone()[0] == 0
        assert get_share_token(conn) != PLANTED_TOKEN


def test_the_owners_own_sharing_survives_a_restart(tmp_path):
    from bpp.web.share import (
        AuthDecision,
        approve_device,
        find_or_create_pending_device,
        get_share_token,
        is_lan_sharing_enabled,
        set_lan_sharing_enabled,
    )

    lib = str(tmp_path / "lib")
    app, ctx = _open(lib)
    with app.app_context():
        conn = ctx.get_conn()
        token = get_share_token(conn)
        set_lan_sharing_enabled(conn, True)
        device = find_or_create_pending_device(conn, "my-phone", name="phone", ip="192.168.1.50")
        assert approve_device(conn, device["id"])
    _close()

    app, ctx = _open(lib)
    with app.app_context():
        conn = ctx.get_conn()
        assert is_lan_sharing_enabled(conn)
        assert get_share_token(conn) == token
        assert _lan_decision(ctx, token, "my-phone") is AuthDecision.ALLOW


def test_a_revoke_survives_a_restart_and_keeps_the_other_devices(tmp_path):
    from bpp.web.share import (
        approve_device,
        find_or_create_pending_device,
        get_share_token,
        is_device_trusted,
        revoke_device,
        set_lan_sharing_enabled,
    )

    lib = str(tmp_path / "lib")
    app, ctx = _open(lib)
    with app.app_context():
        conn = ctx.get_conn()
        get_share_token(conn)
        set_lan_sharing_enabled(conn, True)
        ids = []
        for fp in ("phone-a", "phone-b"):
            device = find_or_create_pending_device(conn, fp, name=fp, ip="192.168.1.50")
            approve_device(conn, device["id"])
            ids.append(device["id"])
        assert revoke_device(conn, ids[0])
    _close()

    app, ctx = _open(lib)
    with app.app_context():
        conn = ctx.get_conn()
        assert not is_device_trusted(conn, "phone-a")
        assert is_device_trusted(conn, "phone-b")


def test_sharing_set_up_under_another_account_is_not_honoured(tmp_path, monkeypatch):
    from bpp.web.share import get_share_token, is_lan_sharing_enabled, set_lan_sharing_enabled

    lib = str(tmp_path / "lib")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "their-config"))
    app, ctx = _open(lib)
    with app.app_context():
        their_token = get_share_token(ctx.get_conn())
        set_lan_sharing_enabled(ctx.get_conn(), True)
    _close()

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "my-config"))
    app, ctx = _open(lib)
    with app.app_context():
        conn = ctx.get_conn()
        assert not is_lan_sharing_enabled(conn)
        assert get_share_token(conn) != their_token


def test_switching_to_a_library_from_elsewhere_resets_it_too(tmp_path):
    from bpp.web.share import is_lan_sharing_enabled

    planted = _planted_library(tmp_path / "planted")
    app, ctx = _open(str(tmp_path / "lib"))
    with app.app_context():
        ctx.switch_library(planted)
        conn = ctx.get_conn()
        assert not is_lan_sharing_enabled(conn)
        assert conn.execute("SELECT COUNT(*) FROM share_devices").fetchone()[0] == 0


def test_a_device_approved_behind_the_apps_back_resets_sharing(tmp_path):
    from bpp.web.share import get_share_token, is_lan_sharing_enabled, set_lan_sharing_enabled

    lib = str(tmp_path / "lib")
    app, ctx = _open(lib)
    with app.app_context():
        conn = ctx.get_conn()
        get_share_token(conn)
        set_lan_sharing_enabled(conn, True)
        now = int(time.time())
        conn.execute(
            "INSERT INTO share_devices (fingerprint, first_seen, last_seen, trusted_at)"
            " VALUES ('slipped-in', ?, ?, ?)",
            (now, now, now),
        )
        conn.commit()
    _close()

    app, ctx = _open(lib)
    with app.app_context():
        conn = ctx.get_conn()
        assert not is_lan_sharing_enabled(conn)
        assert conn.execute("SELECT COUNT(*) FROM share_devices").fetchone()[0] == 0


# ── libraries in use before the key existed ──────────────────────────────


def _adoptable() -> list:
    return store._load()["adopt"]


def test_a_library_already_in_use_keeps_its_sharing_once(tmp_path):
    from bpp.db.registry import add_library
    from bpp.web.share import get_share_token, is_device_trusted, is_lan_sharing_enabled

    lib = _planted_library(tmp_path / "lib")  # stands in for the owner's older library
    add_library(lib)
    share_seal.ensure_installation()  # first run of this version

    app, ctx = _open(lib)
    with app.app_context():
        conn = ctx.get_conn()
        assert is_lan_sharing_enabled(conn)
        assert get_share_token(conn) == PLANTED_TOKEN
        assert is_device_trusted(conn, PLANTED_FP)
    _close()
    assert _adoptable() == []

    # Its seal is then required like any other library's.
    app, ctx = _open(lib)
    with app.app_context():
        conn = ctx.get_conn()
        conn.execute("DELETE FROM settings WHERE key LIKE 'lan_share_seal%'")
        conn.commit()
    _close()
    app, ctx = _open(lib)
    with app.app_context():
        assert not is_lan_sharing_enabled(ctx.get_conn())


def test_a_library_registered_after_the_key_exists_is_not_adopted(tmp_path):
    from bpp.db.registry import add_library
    from bpp.web.share import is_lan_sharing_enabled

    share_seal.ensure_installation()
    lib = _planted_library(tmp_path / "lib")
    add_library(lib)

    app, ctx = _open(lib)
    with app.app_context():
        assert not is_lan_sharing_enabled(ctx.get_conn())


def test_another_database_put_at_an_older_librarys_path_is_not_adopted(tmp_path):
    import shutil

    from bpp.db.registry import add_library
    from bpp.web.share import is_lan_sharing_enabled

    lib = _planted_library(tmp_path / "lib")
    add_library(lib)
    share_seal.ensure_installation()
    # Before the owner opens it again, the folder is replaced by another
    # one with the same name (another drive mounted there, an unpacked copy).
    other = _planted_library(tmp_path / "other")
    shutil.rmtree(lib)
    shutil.copytree(other, lib)

    app, ctx = _open(lib)
    with app.app_context():
        assert not is_lan_sharing_enabled(ctx.get_conn())


def test_serve_makes_the_key_before_it_registers_the_library(tmp_path):
    import argparse

    from bpp.commands.serve import _resolve_library

    lib = _planted_library(tmp_path / "lib")
    _resolve_library(argparse.Namespace(library=lib))

    assert _adoptable() == []


def test_an_older_library_with_no_sharing_leaves_the_adoption_list(tmp_path):
    from bpp.db.connection import init_db
    from bpp.db.library import ensure_library_dirs
    from bpp.db.registry import add_library

    lib = str(tmp_path / "lib")
    os.makedirs(lib)
    init_db(os.path.join(ensure_library_dirs(lib)["data"], "photopicker.db"))
    _close()
    add_library(lib)
    share_seal.ensure_installation()
    assert len(_adoptable()) == 1

    _open(lib)
    _close()
    assert _adoptable() == []


# ── the key ──────────────────────────────────────────────────────────────


def test_the_key_is_private_and_outside_the_library(tmp_path):
    lib = str(tmp_path / "lib")
    _open(lib)

    for path in (store.key_file(), store.state_file()):
        assert os.path.isfile(path)
        assert not os.path.realpath(path).startswith(os.path.realpath(lib) + os.sep)
        if os.name == "posix":
            assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def _owner_turns_sharing_on(lib):
    from bpp.web.share import get_share_token, set_lan_sharing_enabled

    app, ctx = _open(lib)
    with app.app_context():
        get_share_token(ctx.get_conn())
        set_lan_sharing_enabled(ctx.get_conn(), True)
    _close()


def _sharing_is_on(lib) -> bool:
    from bpp.web.share import is_lan_sharing_enabled

    app, ctx = _open(lib)
    with app.app_context():
        on = is_lan_sharing_enabled(ctx.get_conn())
    _close()
    return on


def test_saving_the_records_never_rewrites_the_key(tmp_path):
    lib = str(tmp_path / "lib")
    _owner_turns_sharing_on(lib)
    with open(store.key_file()) as f:
        key = f.read()
    before = os.stat(store.key_file()).st_ino

    _owner_turns_sharing_on(lib)

    assert os.stat(store.key_file()).st_ino == before
    with open(store.key_file()) as f:
        assert f.read() == key


def test_losing_the_records_file_does_not_lose_the_owners_sharing(tmp_path):
    lib = str(tmp_path / "lib")
    _owner_turns_sharing_on(lib)
    with open(store.state_file(), "w") as f:
        f.write("")  # what a power cut can leave behind

    assert _sharing_is_on(lib)
    with open(store.key_file()) as f:
        assert len(f.read()) == 64, "the key was not replaced"


def test_a_key_that_cannot_be_read_is_not_replaced(tmp_path, monkeypatch):
    lib = str(tmp_path / "lib")
    _owner_turns_sharing_on(lib)
    with open(store.key_file()) as f:
        key = f.read()
    real_open = open

    def unreadable(path, *a, **k):
        if str(path) == store.key_file():
            raise PermissionError("not now")
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", unreadable)
    assert not _sharing_is_on(lib), "not honoured while the key can't be read"
    monkeypatch.setattr("builtins.open", real_open)
    store._memory_keys.clear()

    with open(store.key_file()) as f:
        assert f.read() == key


def test_a_damaged_key_is_set_aside_and_old_seals_stop_counting(tmp_path):
    lib = str(tmp_path / "lib")
    _owner_turns_sharing_on(lib)
    with open(store.key_file(), "w") as f:
        f.write("not a key")

    assert not _sharing_is_on(lib)
    folder = os.path.dirname(store.key_file())
    assert [n for n in os.listdir(folder) if n.startswith("share-seal.key.corrupt")]


@pytest.mark.parametrize("outcome", ["sealed", "empty", "adopted", "reset"])
def test_the_outcome_names_are_stable(outcome):
    assert outcome in {share_seal.SEALED, share_seal.EMPTY, share_seal.ADOPTED, share_seal.RESET}

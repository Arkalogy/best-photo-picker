"""A sharing seal is for one library, in one place, at one moment.

Continues tests/test_share_state_sealed.py: a seal copied into another
folder, an older copy of the database, values of the wrong kind, and the
ways the seal stays in step with the owner's own writes.
"""

from __future__ import annotations

import os

import pytest

from bpp.web import share_seal
from tests.test_share_state_sealed import _close, _open, _planted_library

# ── a seal is for one library, in one place, at one moment ───────────────


def _own_sharing(lib, devices=("my-phone",)) -> str:
    from bpp.web.share import (
        approve_device,
        find_or_create_pending_device,
        get_share_token,
        set_lan_sharing_enabled,
    )

    app, ctx = _open(lib)
    with app.app_context():
        conn = ctx.get_conn()
        token = get_share_token(conn)
        set_lan_sharing_enabled(conn, True)
        for fp in devices:
            device = find_or_create_pending_device(conn, fp, name=fp, ip="192.168.1.50")
            approve_device(conn, device["id"])
    _close()
    return token


def _sharing_on(lib) -> bool:
    from bpp.web.share import is_lan_sharing_enabled

    app, ctx = _open(lib)
    with app.app_context():
        on = is_lan_sharing_enabled(ctx.get_conn())
    _close()
    return on


def test_a_copy_of_the_owners_own_sealed_state_is_not_honoured_elsewhere(tmp_path):
    import shutil

    lib = str(tmp_path / "lib")
    _own_sharing(lib)
    copy = str(tmp_path / "received")
    shutil.copytree(lib, copy)

    assert not _sharing_on(copy), "the seal was made for the library that still exists"
    assert _sharing_on(lib)


def test_a_moved_library_keeps_its_sharing(tmp_path):
    lib = str(tmp_path / "lib")
    _own_sharing(lib)
    moved = str(tmp_path / "moved")
    os.rename(lib, moved)

    assert _sharing_on(moved)
    assert _sharing_on(moved), "and it is remembered at its new place"


def test_an_older_copy_of_the_database_does_not_undo_a_revoke(tmp_path):
    import shutil

    from bpp.web.share import is_device_trusted, is_lan_sharing_enabled, revoke_device

    lib = str(tmp_path / "lib")
    _own_sharing(lib)
    db = os.path.join(lib, "data", "photopicker.db")
    shutil.copy(db, db + ".older")

    app, ctx = _open(lib)
    with app.app_context():
        conn = ctx.get_conn()
        device_id = conn.execute("SELECT id FROM share_devices").fetchone()[0]
        revoke_device(conn, device_id)
    _close()
    for leftover in ("-wal", "-shm"):
        if os.path.exists(db + leftover):
            os.remove(db + leftover)
    shutil.copy(db + ".older", db)  # the older database comes back

    app, ctx = _open(lib)
    with app.app_context():
        conn = ctx.get_conn()
        assert not is_device_trusted(conn, "my-phone")
        assert not is_lan_sharing_enabled(conn)


def test_a_seal_committed_just_before_a_crash_still_counts(tmp_path, monkeypatch):
    from bpp.web.share import set_lan_sharing_enabled

    lib = str(tmp_path / "lib")
    _own_sharing(lib)
    app, ctx = _open(lib)
    with app.app_context():
        recorded = share_seal.confirm
        monkeypatch.setattr(share_seal, "confirm", lambda staged: None)  # the crash
        set_lan_sharing_enabled(ctx.get_conn(), True)
        monkeypatch.setattr(share_seal, "confirm", recorded)
    _close()

    assert _sharing_on(lib)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("lan_share_seal", b"A"),
        ("lan_share_seal", "\u00e9"),
        ("lan_share_token", b"token-bytes"),
        ("lan_share_seal_id", b"id"),
        ("lan_share_seal_n", "-1"),
        ("lan_share_token", b"\xff\xfe not utf-8"),
    ],
)
def test_stored_values_of_the_wrong_kind_reset_sharing_instead_of_stopping_the_app(
    tmp_path, key, value
):
    from bpp.db.connection import get_db

    lib = _planted_library(tmp_path / "lib")
    conn = get_db(os.path.join(lib, "data", "photopicker.db"))
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    if key != "lan_share_seal":
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('lan_share_seal', 'x')")
    conn.commit()
    _close()

    assert not _sharing_on(lib)


def test_turning_sharing_on_and_its_seal_are_one_commit(tmp_path, monkeypatch):
    from bpp.web.share import is_lan_sharing_enabled, set_lan_sharing_enabled

    app, ctx = _open(str(tmp_path / "lib"))
    with app.app_context():
        conn = ctx.get_conn()

        def fail(conn):
            raise RuntimeError("seal could not be written")

        monkeypatch.setattr(share_seal, "stage", fail)
        with pytest.raises(RuntimeError):
            set_lan_sharing_enabled(conn, True)
        assert not is_lan_sharing_enabled(conn), "the switch did not move without its seal"


def test_the_seal_is_not_shown_to_devices_or_writable_through_settings():
    from bpp.web.bp_settings import _SHARE_ONLY_KEYS, _is_sensitive_setting_key

    for key in (share_seal.KEY_SEAL, share_seal.KEY_SEAL_ID):
        assert _is_sensitive_setting_key(key)
        assert key in _SHARE_ONLY_KEYS


def test_revoking_the_link_keeps_sharing_restorable_from_the_startup_backup(tmp_path):
    import shutil

    from bpp.web.share import regenerate_share_token

    lib = str(tmp_path / "lib")
    _own_sharing(lib)
    db = os.path.join(lib, "data", "photopicker.db")
    app, ctx = _open(lib)  # this open takes the startup backup
    with app.app_context():
        assert os.path.exists(db + ".backup")
        regenerate_share_token(ctx.get_conn())
    _close()
    for leftover in ("-wal", "-shm"):
        if os.path.exists(db + leftover):
            os.remove(db + leftover)
    shutil.copy(db + ".backup", db)  # what the auto-restore does

    assert _sharing_on(lib)


def test_a_device_table_that_pre_approves_new_devices_is_replaced(tmp_path):
    from bpp.db.connection import close_all_connections, get_db, init_db
    from bpp.web.share import find_or_create_pending_device, is_device_trusted

    db = str(tmp_path / "photopicker.db")
    init_db(db)
    conn = get_db(db)
    conn.execute("DROP TABLE share_devices")
    conn.execute(
        "CREATE TABLE share_devices (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " fingerprint TEXT UNIQUE NOT NULL, name TEXT NOT NULL DEFAULT '',"
        " ip_at_pair TEXT NOT NULL DEFAULT '', user_id INTEGER, scope_json TEXT,"
        " first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL,"
        " trusted_at INTEGER DEFAULT 1, revoked_at INTEGER,"
        " prev_revoked INTEGER NOT NULL DEFAULT 0)"
    )
    conn.commit()
    close_all_connections(new_epoch=True)

    conn = init_db(db)
    find_or_create_pending_device(conn, "stranger", name="x", ip="192.168.1.9")

    assert not is_device_trusted(conn, "stranger")
    defaults = {r[1]: r[4] for r in conn.execute("PRAGMA table_info(share_devices)")}
    assert defaults["trusted_at"] is None
    close_all_connections(new_epoch=True)


def test_stored_text_that_cannot_be_decoded_resets_sharing_too(tmp_path):
    from bpp.db.connection import get_db

    lib = _planted_library(tmp_path / "lib")
    conn = get_db(os.path.join(lib, "data", "photopicker.db"))
    conn.execute(
        "UPDATE settings SET value = CAST(? AS TEXT) WHERE key = 'lan_share_token'", (b"\xff\xfe",)
    )
    conn.execute("UPDATE share_devices SET fingerprint = CAST(? AS TEXT)", (b"\xff\xfe",))
    conn.commit()
    _close()

    assert not _sharing_on(lib)


def test_a_library_switched_to_is_closed_to_devices_until_it_has_been_checked(tmp_path):
    from bpp.web.share import AuthDecision, authorize_request
    from tests.test_share_state_sealed import PLANTED_FP, PLANTED_TOKEN, _Req

    planted = _planted_library(tmp_path / "planted")
    app, ctx = _open(str(tmp_path / "lib"))
    seen: list = []
    real_init = ctx.init_app_db

    def init_while_a_device_knocks():
        # The paths already point at the new library; its check has not run.
        request = _Req("/api/v1/photos", "192.168.1.50", PLANTED_TOKEN, PLANTED_FP)
        seen.append(authorize_request(request, ctx).decision)
        real_init()

    ctx.init_app_db = init_while_a_device_knocks
    with app.app_context():
        ctx.switch_library(planted)

    assert seen == [AuthDecision.DENY]
    assert ctx.share_state_checked is True


def test_an_owner_write_before_the_check_does_not_seal_foreign_state(tmp_path):
    from bpp.db.connection import get_db
    from bpp.web.share import get_share_token, is_lan_sharing_enabled

    lib = _planted_library(tmp_path / "lib")
    db = os.path.join(lib, "data", "photopicker.db")
    conn = get_db(db)
    conn.execute("DELETE FROM settings WHERE key = 'lan_share_token'")
    conn.commit()

    # A request reaches the new database first and creates the link.
    token = get_share_token(conn)

    assert token and not is_lan_sharing_enabled(conn)
    assert conn.execute("SELECT COUNT(*) FROM share_devices").fetchone()[0] == 0
    _close()
    assert not _sharing_on(lib)


def test_renaming_the_library_folder_keeps_sharing_even_after_the_old_path_is_reused(tmp_path):
    lib = str(tmp_path / "lib")
    _own_sharing(lib)
    renamed = str(tmp_path / "renamed")
    os.rename(lib, renamed)
    _open(lib)  # the app recreates an empty library at the old, still-registered path
    _close()

    assert _sharing_on(renamed)


def test_a_first_seal_committed_just_before_a_crash_still_counts(tmp_path, monkeypatch):
    from bpp.web.share import set_lan_sharing_enabled

    lib = str(tmp_path / "lib")
    app, ctx = _open(lib)
    with app.app_context():
        recorded = share_seal.confirm
        monkeypatch.setattr(share_seal, "confirm", lambda staged: None)  # the crash
        set_lan_sharing_enabled(ctx.get_conn(), True)
        monkeypatch.setattr(share_seal, "confirm", recorded)
    _close()

    assert _sharing_on(lib)

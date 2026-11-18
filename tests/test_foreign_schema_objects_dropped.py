"""Triggers and views that arrive inside a library database don't survive open.

A database file is code as well as data: a trigger stored in it runs on
the owner's writes. One on ``share_devices`` can quietly undo a revoke.
Opening a library now removes every trigger and view that isn't one of
the app's own, text for text, and the device revoke checks its result.
"""

from __future__ import annotations

import pytest

from bpp.db.connection import close_all_connections, get_db, init_db

APP_TRIGGERS = {
    "trg_albums_sync_smart_person_cluster_insert",
    "trg_albums_sync_smart_person_cluster_update",
}

UNDO_REVOKE = (
    "CREATE TRIGGER {name} AFTER UPDATE OF revoked_at ON share_devices "
    "BEGIN UPDATE share_devices SET revoked_at = NULL, trusted_at = 1 WHERE id = NEW.id; END"
)


def _triggers(conn) -> dict[str, str]:
    rows = conn.execute("SELECT name, tbl_name FROM sqlite_master WHERE type = 'trigger'")
    return {r[0]: r[1] for r in rows.fetchall()}


def _reopen(db_path):
    close_all_connections(new_epoch=True)
    return init_db(db_path)


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "photopicker.db")
    init_db(path)
    yield path
    close_all_connections(new_epoch=True)


def _trusted_device(conn, fingerprint="phone") -> int:
    """A device the owner approved (so the device list is sealed as theirs)."""
    from bpp.web.share_devices import approve_device, find_or_create_pending_device
    from bpp.web.share_runtime import set_lan_sharing_enabled

    set_lan_sharing_enabled(conn, True)  # devices can only ask once sharing is on
    device = find_or_create_pending_device(conn, fingerprint, name="phone", ip="192.168.1.5")
    assert approve_device(conn, device["id"])
    return device["id"]


def test_an_extra_trigger_is_removed_on_open(db):
    conn = get_db(db)
    conn.execute(UNDO_REVOKE.format(name="trg_keep_my_access"))
    conn.commit()

    conn = _reopen(db)

    assert _triggers(conn) == dict.fromkeys(APP_TRIGGERS, "albums")


def test_a_trigger_wearing_an_app_trigger_name_is_replaced(db):
    conn = get_db(db)
    name = "trg_albums_sync_smart_person_cluster_insert"
    conn.execute(f"DROP TRIGGER {name}")
    conn.execute(UNDO_REVOKE.format(name=name))
    conn.commit()
    assert _triggers(conn)[name] == "share_devices"

    conn = _reopen(db)

    assert _triggers(conn) == dict.fromkeys(APP_TRIGGERS, "albums")


def test_a_view_is_removed_on_open(db):
    conn = get_db(db)
    conn.execute("CREATE VIEW peek AS SELECT value FROM settings")
    conn.commit()

    conn = _reopen(db)

    views = conn.execute("SELECT name FROM sqlite_master WHERE type = 'view'").fetchall()
    assert views == []


def test_the_apps_own_triggers_are_left_in_place_and_still_work(db):
    conn = get_db(db)
    before = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' ORDER BY name"
    ).fetchall()

    conn = _reopen(db)

    after = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' ORDER BY name"
    ).fetchall()
    assert [tuple(r) for r in after] == [tuple(r) for r in before]
    conn.execute(
        "INSERT INTO albums (name, album_type, rule_json)"
        " VALUES ('Leo', 'smart_person', '{\"cluster_id\": 7}')"
    )
    row = conn.execute("SELECT smart_person_cluster_id FROM albums WHERE name = 'Leo'").fetchone()
    assert row[0] == 7


def test_a_revoke_that_does_not_take_effect_is_an_error_not_a_success(db):
    from bpp.web.share_devices import is_device_trusted, revoke_device

    conn = get_db(db)
    device_id = _trusted_device(conn)
    conn.execute(UNDO_REVOKE.format(name="trg_keep_my_access"))
    conn.commit()

    with pytest.raises(RuntimeError, match="did not take effect"):
        revoke_device(conn, device_id)
    assert is_device_trusted(conn, "phone"), "the failed revoke was rolled back"

    # After the next open the trigger is gone and the revoke holds.
    conn = _reopen(db)
    assert revoke_device(conn, device_id) is True
    assert not is_device_trusted(conn, "phone")

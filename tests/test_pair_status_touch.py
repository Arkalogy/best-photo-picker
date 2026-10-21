"""Polling the pairing page keeps only a *pending* request alive, and a
pending request still expires by its age: a revoked device, or one
nobody approves, can't keep its row forever by polling."""

from __future__ import annotations

import os
import time

import pytest


@pytest.fixture
def app(tmp_path):
    from bpp.web.app import create_app
    from bpp.web.share import _reset_pair_request_buckets_for_tests, set_lan_sharing_enabled

    _reset_pair_request_buckets_for_tests()
    workdir = str(tmp_path / "workdir")
    os.makedirs(workdir)
    app = create_app(workdir=workdir)
    app.config["TESTING"] = False
    ctx = app.extensions["bpp"]
    with app.app_context():
        set_lan_sharing_enabled(ctx.get_conn(), True)
    return app


def _last_seen(conn, fp):
    return conn.execute(
        "SELECT last_seen FROM share_devices WHERE fingerprint=?", (fp,)
    ).fetchone()[0]


def _poll(app, fp):
    client = app.test_client()
    client.set_cookie("bpp_share_fp", fp)
    return client.get(
        "/api/v1/share/pair/status", environ_overrides={"REMOTE_ADDR": "192.168.1.5"}
    ).get_json()["state"]


def test_a_revoked_device_polling_is_not_kept_alive(app):
    from bpp.web.share import find_or_create_pending_device
    from bpp.web.share_devices import revoke_device

    ctx = app.extensions["bpp"]
    with app.app_context():
        conn = ctx.get_conn()
        dev = find_or_create_pending_device(conn, "fp-R", "iPhone", "192.168.1.5")
        revoke_device(conn, int(dev["id"]))
        old = int(time.time()) - 5000
        conn.execute("UPDATE share_devices SET last_seen=? WHERE fingerprint='fp-R'", (old,))
        conn.commit()
    assert _poll(app, "fp-R") == "revoked"
    with app.app_context():
        assert _last_seen(ctx.get_conn(), "fp-R") == old


def test_a_pending_device_polling_is_kept_alive(app):
    from bpp.web.share import find_or_create_pending_device

    ctx = app.extensions["bpp"]
    with app.app_context():
        conn = ctx.get_conn()
        find_or_create_pending_device(conn, "fp-P", "iPhone", "192.168.1.5")
        old = int(time.time()) - 5000
        conn.execute("UPDATE share_devices SET last_seen=? WHERE fingerprint='fp-P'", (old,))
        conn.commit()
    assert _poll(app, "fp-P") == "pending"
    with app.app_context():
        assert _last_seen(ctx.get_conn(), "fp-P") > old


def test_a_pending_request_expires_by_age_even_while_polling(app):
    from bpp.web.share import find_or_create_pending_device
    from bpp.web.share_devices import prune_expired_pending

    ctx = app.extensions["bpp"]
    with app.app_context():
        conn = ctx.get_conn()
        find_or_create_pending_device(conn, "fp-O", "iPhone", "192.168.1.5")
        conn.execute(
            "UPDATE share_devices SET first_seen=?, last_seen=? WHERE fingerprint='fp-O'",
            (int(time.time()) - 100, int(time.time())),
        )
        conn.commit()
        assert prune_expired_pending(conn, ttl_seconds=10) == 1


def test_listing_devices_drops_requests_older_than_the_ttl(app, monkeypatch):
    from bpp.web import share_devices
    from bpp.web.share import find_or_create_pending_device

    monkeypatch.setattr(share_devices, "PENDING_TTL_SECONDS", 10)
    ctx = app.extensions["bpp"]
    with app.app_context():
        conn = ctx.get_conn()
        find_or_create_pending_device(conn, "fp-old", "iPhone", "192.168.1.5")
        find_or_create_pending_device(conn, "fp-new", "iPad", "192.168.1.6")
        conn.execute(
            "UPDATE share_devices SET first_seen=? WHERE fingerprint='fp-old'",
            (int(time.time()) - 100,),
        )
        conn.commit()
    app.config["TESTING"] = True
    with app.test_client() as c:
        names = {d["name"] for d in c.get("/api/v1/share/devices").get_json()["pending"]}
    assert names == {"iPad"}


def test_a_re_request_after_a_revoke_is_a_fresh_request(app):
    from bpp.web.share import find_or_create_pending_device
    from bpp.web.share_devices import request_access, revoke_device

    ctx = app.extensions["bpp"]
    with app.app_context():
        conn = ctx.get_conn()
        dev = find_or_create_pending_device(conn, "fp-again", "iPhone", "192.168.1.5")
        revoke_device(conn, int(dev["id"]))
        old = int(time.time()) - 100
        conn.execute("UPDATE share_devices SET first_seen=? WHERE fingerprint='fp-again'", (old,))
        conn.commit()
        row = request_access(conn, "fp-again")
        assert row is not None and row["prev_revoked"]
        first_seen = conn.execute(
            "SELECT first_seen FROM share_devices WHERE fingerprint='fp-again'"
        ).fetchone()[0]
        assert first_seen > old


def test_a_re_scan_after_the_request_expired_is_a_fresh_request(app, monkeypatch):
    from bpp.web import share_devices
    from bpp.web.share import find_or_create_pending_device

    monkeypatch.setattr(share_devices, "PENDING_TTL_SECONDS", 10)
    ctx = app.extensions["bpp"]
    with app.app_context():
        conn = ctx.get_conn()
        find_or_create_pending_device(conn, "fp-S", "iPhone", "192.168.1.5")
        conn.execute(
            "UPDATE share_devices SET first_seen=? WHERE fingerprint='fp-S'",
            (int(time.time()) - 100,),
        )
        conn.commit()
        row = find_or_create_pending_device(conn, "fp-S", "iPhone", "192.168.1.5")
        assert row["first_seen"] >= int(time.time()) - 5
        assert share_devices.list_devices(conn)["pending"][0]["name"] == "iPhone"

"""A paired device reads the library; it does not get to do more.

It must not hold up the owner's work, write into the owner's logs under
a name of its choosing, see another library's activity, or keep access
the owner took away.
"""

from __future__ import annotations

import logging
import os
import shutil

import pytest

from tests.test_share_state_sealed import _close, _open

LAN = "192.168.1.50"


@pytest.fixture
def shared(tmp_path):
    """An open library with sharing on and one approved phone."""
    from bpp.web.share import get_share_token, set_lan_sharing_enabled
    from bpp.web.share_devices import approve_device, find_or_create_pending_device

    lib = str(tmp_path / "lib")
    app, ctx = _open(lib)
    app.config["TESTING"] = False  # the request boundary is what is under test
    with app.app_context():
        conn = ctx.get_conn()
        token = get_share_token(conn)
        set_lan_sharing_enabled(conn, True)
        device = find_or_create_pending_device(conn, "phone-fp", name="Ann's iPhone", ip=LAN)
        approve_device(conn, device["id"])
    yield app, ctx, token, lib
    _close()


def _as_phone(client, token, fp="phone-fp", agent="Safari"):
    client.set_cookie("bpp_share_fp", fp)
    return {
        "headers": {"X-Auth-Token": token, "User-Agent": agent},
        "environ_base": {"REMOTE_ADDR": LAN},
    }


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/faces/review-pairs/count",
        "/api/v1/faces/review-pairs/next",
        "/api/v1/photos/enhance-preview?filepath=/x.jpg",
    ],
)
def test_owner_work_routes_are_not_for_devices(shared, path):
    """These take the lock every face edit waits on, or decode a photo at
    full size on every call: a device has no use for them."""
    app, _ctx, token, _lib = shared
    with app.test_client() as c:
        resp = c.get(path, **_as_phone(c, token))
    assert resp.status_code == 403


def test_one_address_cannot_use_up_everyone_elses_pairing_requests(shared):
    from bpp.web.share import consume_destructive_token

    app, _ctx, _token, _lib = shared
    with app.test_client() as c:
        for _ in range(80):
            c.post("/api/v1/share/pair/request", environ_base={"REMOTE_ADDR": "192.168.1.66"})
        other = c.post("/api/v1/share/pair/request", environ_base={"REMOTE_ADDR": "192.168.1.67"})
    assert other.status_code != 429
    # The address that kept asking has used up its own allowance, only.
    assert not consume_destructive_token("anonymous:192.168.1.66")


def test_recent_access_shows_the_device_the_owner_approved(shared):
    """Not a name the device sends with each request, and one device
    can't push the others out of the list."""
    from bpp.web.share import recent_share_access
    from bpp.web.share_devices import approve_device, find_or_create_pending_device

    app, ctx, token, _lib = shared
    with app.app_context():
        conn = ctx.get_conn()
        other = find_or_create_pending_device(conn, "tablet-fp", name="Bo's iPad", ip=LAN)
        approve_device(conn, other["id"])
    with app.test_client() as c:
        c.get("/api/v1/status", **_as_phone(c, token, fp="tablet-fp"))
    with app.test_client() as c:
        for i in range(40):
            c.get("/api/v1/status", **_as_phone(c, token, agent=f"Owner's own Mac {i}"))
    with app.app_context():
        seen = [row["user_agent"] for row in recent_share_access(ctx.get_conn(), limit=50)]
    assert "Bo's iPad" in seen
    assert set(seen) == {"Ann's iPhone", "Bo's iPad"}
    assert len(seen) == 2  # forty requests under forty names: one visit


def test_one_name_keeps_only_its_latest_visits(shared, monkeypatch):
    """Visits spread over hours (and addresses): a device still can't fill
    the list, and the others stay in it."""
    from bpp.web import share_runtime
    from bpp.web.share import recent_share_access

    app, ctx, _token, _lib = shared
    with app.app_context():
        conn = ctx.get_conn()
        share_runtime.record_share_access(conn, ip=LAN, user_agent="x", fingerprint=None)
        for hour in range(12):
            share_runtime._reset_pair_request_buckets_for_tests()  # as if the window had passed
            monkeypatch.setattr(
                share_runtime.time, "time", lambda hour=hour: 1_800_000_000 + hour * 3600
            )
            share_runtime.record_share_access(
                conn, ip=f"192.168.1.{60 + hour}", user_agent="x", fingerprint="phone-fp"
            )
        seen = [row["user_agent"] for row in recent_share_access(conn, limit=50)]
    assert seen.count("Ann's iPhone") == 3
    assert "x" in seen  # the other visitor was not pushed out


def test_an_expired_request_needs_the_share_link_again(shared):
    """A pairing request that ran out is forgotten: the same browser has
    to come back through the link, like any device the owner never saw."""
    from bpp.web.share_devices import PENDING_TTL_SECONDS, find_or_create_pending_device

    app, ctx, token, _lib = shared
    with app.app_context():
        conn = ctx.get_conn()
        find_or_create_pending_device(conn, "old-fp", name="Old", ip=LAN)
        conn.execute(
            "UPDATE share_devices SET first_seen = first_seen - ?, last_seen = last_seen - ?"
            " WHERE fingerprint = 'old-fp'",
            (PENDING_TTL_SECONDS + 60, PENDING_TTL_SECONDS + 60),
        )
        conn.commit()
    with app.test_client() as c:
        c.set_cookie("bpp_share_fp", "old-fp")
        without_link = c.get("/", environ_base={"REMOTE_ADDR": LAN})
    assert without_link.status_code == 403
    with app.app_context():
        rows = (
            ctx.get_conn()
            .execute("SELECT 1 FROM share_devices WHERE fingerprint = 'old-fp'")
            .fetchall()
        )
    assert rows == []
    assert token  # (the link itself still works for a fresh request)


def test_the_owner_sees_requests_in_the_order_they_arrived(shared):
    """A requester can refresh its own 'last seen'; that must not move
    its row under the owner's finger."""
    from bpp.web.share_devices import find_or_create_pending_device, list_devices

    app, ctx, _token, _lib = shared
    with app.app_context():
        conn = ctx.get_conn()
        first = find_or_create_pending_device(conn, "fp-1", name="First", ip=LAN)
        find_or_create_pending_device(conn, "fp-2", name="Second", ip=LAN)
        conn.execute(
            "UPDATE share_devices SET last_seen = last_seen + 500 WHERE fingerprint = 'fp-2'"
        )
        conn.execute(
            "UPDATE share_devices SET last_seen = last_seen + 900 WHERE id = ?", (first["id"],)
        )
        conn.commit()
        order = [d["name"] for d in list_devices(conn)["pending"]]
        conn.execute(
            "UPDATE share_devices SET last_seen = last_seen + 5000 WHERE fingerprint = 'fp-2'"
        )
        conn.commit()
        again = [d["name"] for d in list_devices(conn)["pending"]]
    assert order == again == ["First", "Second"]


def test_a_refused_request_cannot_write_its_own_log_lines(shared, caplog):
    app, _ctx, _token, _lib = shared
    with caplog.at_level(logging.DEBUG), app.test_client() as c:
        for _ in range(50):
            c.get(
                "/api/v1/x%0A2026-01-01 [WARNING] Sharing was turned off for this library",
                environ_base={"REMOTE_ADDR": "192.168.1.77"},
            )
    lines = [r.getMessage() for r in caplog.records if "Auth rejected" in r.getMessage()]
    assert lines, "refusals are still logged"
    assert all("\n" not in line and "\r" not in line for line in lines)
    assert len(lines) <= 10, "one address can't fill the log"


@pytest.mark.parametrize(
    ("address", "loopback"),
    [
        ("127.0.0.1", True),
        ("127.5.6.7", True),
        ("::1", True),
        ("127.0.0.1.evil.example", False),
        ("127.evil", False),
        ("192.168.1.5", False),
        ("", False),
        ("::ffff:127.0.0.1", True),
        ("::1%lo0", True),
        ("localhost", False),
        (None, False),
    ],
)
def test_only_a_real_loopback_address_counts_as_this_computer(address, loopback):
    from bpp.web.share import is_loopback_address

    assert is_loopback_address(address) is loopback


def test_a_long_search_is_cut_short(shared):
    from bpp.web import bp_search

    app, _ctx, _token, _lib = shared
    with app.test_request_context("/api/v1/search?q=" + "a" * 5000):
        assert len(bp_search._query()) == bp_search.MAX_QUERY_CHARS <= 200
    with app.test_request_context("/api/v1/search?q=  dog on a beach "):
        assert bp_search._query() == "dog on a beach"


def test_a_long_run_of_letters_is_cut_before_tokenizing():
    from bpp.scoring import clip_tokenizer

    assert len(clip_tokenizer._clip_input("a" * 100_000)) <= clip_tokenizer.MAX_QUERY_CHARS
    assert clip_tokenizer._clip_input("a dog on a beach") == "a dog on a beach"


def test_worker_logs_do_not_follow_into_the_next_library(shared):
    _app, ctx, _token, _lib = shared
    worker = next(iter(ctx.workers._workers.values()))
    with worker._events_cond:
        worker._events.append({"type": "progress", "file": "IMG_0001.jpg"})
        worker._events.append({"type": "done", "batch": "Holiday", "imported": 12})
    old_run = worker.run_id
    ctx.workers.forget_runs()
    assert worker._events == []
    assert worker.run_id > old_run  # a new reader starts on an empty run
    # A reader still on the old run learns that it ended, and nothing else.
    _run, _seq, tail = worker.events_since(old_run, 0)
    assert tail == [{"type": "done"}]


def test_a_worker_still_winding_down_is_not_heard_in_the_next_library(shared):
    _app, ctx, _token, _lib = shared
    worker = next(iter(ctx.workers._workers.values()))

    class _StillRunning:
        def is_alive(self):
            return True

    worker._thread = _StillRunning()  # it outlived the cancel
    try:
        ctx.workers.forget_runs()
        worker._emit({"type": "progress", "file": "IMG_0002.jpg"})
        assert worker._events == []
    finally:
        worker._thread = None
        worker._muted = False


def test_a_reply_prepared_for_one_library_is_not_sent_after_a_switch(shared):
    """The owner switches library while a device's request is in flight:
    the device was approved for the first one only."""
    app, ctx, token, _lib = shared
    real = ctx.library_id

    @app.get("/api/v1/_test/slow")
    def slow():
        ctx.library_id = "another-library"  # the switch lands mid-request
        return {"photos": ["from the other library"]}

    try:
        with app.test_client() as c:
            resp = c.get("/api/v1/_test/slow", **_as_phone(c, token))
    finally:
        ctx.library_id = real
    assert resp.status_code == 409
    assert b"from the other library" not in resp.data
    assert resp.headers.get("Cache-Control") == "no-store"
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"  # the usual headers stay


def test_an_older_database_copy_does_not_bring_back_removed_access(tmp_path):
    """Revoke a device and turn sharing off, then the app restores the
    start-up backup (taken before both): neither comes back."""
    from bpp.db.library import ensure_library_dirs
    from bpp.web.share import get_share_token, is_lan_sharing_enabled, set_lan_sharing_enabled
    from bpp.web.share_devices import (
        approve_device,
        find_or_create_pending_device,
        is_device_trusted,
        revoke_device,
    )

    lib = str(tmp_path / "lib")
    db = os.path.join(ensure_library_dirs(lib)["data"], "photopicker.db")
    app, ctx = _open(lib)
    with app.app_context():
        conn = ctx.get_conn()
        get_share_token(conn)
        set_lan_sharing_enabled(conn, True)
        device = find_or_create_pending_device(conn, "phone-fp", name="Phone", ip=LAN)
        approve_device(conn, device["id"])
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    _close()
    shutil.copy2(db, db + ".snapshot")  # what a start-up backup holds

    app, ctx = _open(lib)
    with app.app_context():
        conn = ctx.get_conn()
        revoke_device(conn, device["id"])
        set_lan_sharing_enabled(conn, False)
    _close()

    for suffix in ("-wal", "-shm"):
        if os.path.exists(db + suffix):
            os.remove(db + suffix)
    shutil.copy2(db + ".snapshot", db)  # the restore
    app, ctx = _open(lib)
    with app.app_context():
        conn = ctx.get_conn()
        assert not is_lan_sharing_enabled(conn)
        assert not is_device_trusted(conn, "phone-fp")
    _close()

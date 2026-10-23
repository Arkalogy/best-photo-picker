"""Pairing a phone needs the share link.

docs/security.md promises that a LAN device authenticates with the
share token plus an owner-approved fingerprint. The index route used to
create a pending device for any LAN request, token or not, and the
owner's approve click then handed that device the share token. Now a
device only enters the pending list when its request carries the share
token (the QR / share URL), creation is rate-limited per IP, and a
known device keeps working from its cookie alone.
"""

from __future__ import annotations

import os

import pytest

LAN = {"REMOTE_ADDR": "192.168.1.5"}


@pytest.fixture
def app(tmp_path):
    from bpp.web import share_runtime
    from bpp.web.app import create_app
    from bpp.web.share import set_lan_sharing_enabled

    workdir = str(tmp_path / "workdir")
    os.makedirs(workdir)
    app = create_app(workdir=workdir)
    app.config["TESTING"] = False  # the real auth boundary
    ctx = app.extensions["bpp"]
    with app.app_context():
        set_lan_sharing_enabled(ctx.get_conn(), True)
    share_runtime._PAIR_REQUEST_BUCKETS.clear()
    yield app
    share_runtime._PAIR_REQUEST_BUCKETS.clear()


def _token(app) -> str:
    from bpp.web.share import get_share_token

    ctx = app.extensions["bpp"]
    with app.app_context():
        return get_share_token(ctx.get_conn())


def _devices(app) -> dict:
    from bpp.web.share_devices import list_devices

    ctx = app.extensions["bpp"]
    with app.app_context():
        return list_devices(ctx.get_conn())


def _count(app) -> int:
    return sum(len(v) for v in _devices(app).values())


class TestPairingNeedsTheShareLink:
    def test_a_lan_visit_without_the_token_creates_no_device(self, app):
        with app.test_client() as c:
            r = c.get("/", environ_overrides=LAN)
        assert r.status_code == 403
        assert b"share link" in r.data.lower()
        assert b"auth-token" not in r.data
        assert _count(app) == 0

    def test_a_wrong_token_is_refused(self, app):
        with app.test_client() as c:
            r = c.get("/?_token=nope", environ_overrides=LAN)
        assert r.status_code == 403
        assert _count(app) == 0

    def test_the_share_link_enters_the_pending_list(self, app):
        with app.test_client() as c:
            r = c.get(f"/?_token={_token(app)}", environ_overrides=LAN)
        assert r.status_code == 200
        assert b"Waiting for the owner" in r.data
        assert b"auth-token" not in r.data
        assert len(_devices(app)["pending"]) == 1

    def test_a_known_device_reloads_without_the_token(self, app):
        with app.test_client() as c:
            c.get(f"/?_token={_token(app)}", environ_overrides=LAN)
            r = c.get("/", environ_overrides=LAN)  # cookie only
        assert r.status_code == 200
        assert b"Waiting for the owner" in r.data
        assert _count(app) == 1

    def test_an_approved_device_gets_the_app_from_its_cookie(self, app):
        from bpp.web.share_devices import approve_device

        with app.test_client() as c:
            c.get(f"/?_token={_token(app)}", environ_overrides=LAN)
            pending = _devices(app)["pending"][0]
            ctx = app.extensions["bpp"]
            with app.app_context():
                assert approve_device(ctx.get_conn(), int(pending["id"]))
            r = c.get("/", environ_overrides=LAN)
        assert r.status_code == 200
        assert b"auth-token" in r.data

    def test_new_devices_are_rate_limited_per_ip(self, app):
        from bpp.web import share_runtime

        token = _token(app)
        statuses = []
        for _ in range(share_runtime._PAIR_REQUEST_BURST + 1):
            with app.test_client() as c:  # a fresh client each time: no cookie
                statuses.append(c.get(f"/?_token={token}", environ_overrides=LAN).status_code)
        assert (
            statuses[: share_runtime._PAIR_REQUEST_BURST]
            == [200] * share_runtime._PAIR_REQUEST_BURST
        )
        assert statuses[-1] == 429
        assert _count(app) == share_runtime._PAIR_REQUEST_BURST

    def test_the_rate_limit_shows_a_page_not_json(self, app):
        from bpp.web import share_runtime

        token = _token(app)
        for _ in range(share_runtime._PAIR_REQUEST_BURST + 1):
            with app.test_client() as c:
                r = c.get(f"/?_token={token}", environ_overrides=LAN)
        assert r.status_code == 429
        assert r.mimetype == "text/html"
        assert b"try the link again" in r.data

    def test_polling_for_approval_keeps_the_pending_row_fresh(self, app):
        with app.test_client() as c:
            c.get(f"/?_token={_token(app)}", environ_overrides=LAN)
            ctx = app.extensions["bpp"]
            with app.app_context():
                conn = ctx.get_conn()
                conn.execute("UPDATE share_devices SET last_seen = 1")
                conn.commit()
            r = c.get("/api/v1/share/pair/status", environ_overrides=LAN)
            assert r.get_json()["state"] == "pending"
            with app.app_context():
                (last_seen,) = (
                    ctx.get_conn().execute("SELECT last_seen FROM share_devices").fetchone()
                )
        assert last_seen > 1

    def test_a_forgotten_device_is_told_to_rescan(self, app):
        """Status 'unknown' is terminal on the pair page: the token was
        stripped from the URL, so only a fresh share link can help."""
        with app.test_client() as c:
            c.set_cookie("bpp_share_fp", "gone")
            r = c.get("/api/v1/share/pair/status", environ_overrides=LAN)
        assert r.get_json()["state"] == "unknown"
        from pathlib import Path

        page = (Path(__file__).resolve().parents[1] / "bpp/web/templates/pair.html").read_text()
        assert 'd.state === "unknown"' in page and "isn't known to the host" in page

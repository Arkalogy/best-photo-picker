"""``GET /api/v1/install/<key>/info`` names the pip packages an install
will run. That is for the owner's consent dialog; a paired device gets
no package names (``/install/state`` already hides them)."""

from __future__ import annotations

from tests.owner_only._owner import as_loopback


def test_the_owner_sees_the_packages(app):
    r = as_loopback(app, "GET", "/api/v1/install/faces/info")
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["key"] == "faces"
    assert body["host"] == "pypi.org"
    assert body["packages"] and all(isinstance(p, str) for p in body["packages"])


def test_an_unknown_key_is_a_400(app):
    r = as_loopback(app, "GET", "/api/v1/install/nope/info")
    assert r.status_code == 400


def test_a_paired_device_gets_no_package_names(app, lan_device):
    client = app.test_client()
    token, fp = lan_device
    client.set_cookie("bpp_share_fp", fp)
    r = client.get(
        "/api/v1/install/faces/info",
        headers={"X-Auth-Token": token},
        environ_overrides={"REMOTE_ADDR": "192.168.1.50"},
    )
    assert r.status_code == 403, r.data

"""The Host header must name this app.

A web page the owner has open can rebind its own domain to 127.0.0.1
and then read ``/`` (and the session token in it) as a same-origin
request. Browsers send that page's domain as the Host header, never an
IP literal, so refusing every Host that isn't loopback, an IP literal,
or a host the operator configured closes the hole before auth runs.
"""

from __future__ import annotations

import os

import pytest

from bpp.web.host_allowlist import host_is_allowed


class TestHostIsAllowed:
    @pytest.mark.parametrize(
        "host",
        [
            "localhost:5001",
            "LOCALHOST:5001",
            "localhost",
            "127.0.0.1:5001",
            "127.0.0.1",
            "[::1]:5001",
            "[::1]",
            "192.168.1.5:5001",
            "10.0.0.2",
            "[fe80::1%25en0]:5001",
        ],
    )
    def test_loopback_and_ip_literals_are_allowed(self, host):
        assert host_is_allowed(host, ()) is True, host

    @pytest.mark.parametrize(
        "host", ["evil.example:5001", "evil.example", "127.0.0.1.evil.example"]
    )
    def test_a_domain_name_is_refused_unless_configured(self, host):
        assert host_is_allowed(host, ()) is False, host

    def test_configured_hosts_are_allowed_with_dot_prefix_covering_subdomains(self):
        extra = ("photos.home.lan", ".example.net")
        assert host_is_allowed("photos.home.lan:5001", extra) is True
        assert host_is_allowed("PHOTOS.HOME.LAN", extra) is True
        assert host_is_allowed("app.example.net", extra) is True
        assert host_is_allowed("example.net", extra) is True
        assert host_is_allowed("evil.example:5001", extra) is False
        assert host_is_allowed("notexample.net", extra) is False

    @pytest.mark.parametrize("host", ["", "   ", "evil.example:5001:1", "[::1", "a b"])
    def test_garbage_is_refused(self, host):
        assert host_is_allowed(host, ()) is False, repr(host)


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.delenv("BPP_TRUSTED_HOSTS", raising=False)
    from bpp.web.app import create_app

    workdir = str(tmp_path / "workdir")
    os.makedirs(workdir)
    app = create_app(workdir=workdir)
    app.config["TESTING"] = True  # the Host gate must hold even when auth is bypassed
    return app


class TestHostGate:
    def test_a_rebound_domain_gets_no_page_and_no_token(self, app):
        with app.test_client() as c:
            r = c.get("/", headers={"Host": "evil.example:5001"})
        assert r.status_code == 421
        assert b"auth-token" not in r.data
        assert r.get_json()["code"] == "host_not_allowed"

    def test_the_gate_covers_the_api_too(self, app):
        with app.test_client() as c:
            r = c.get("/api/v1/status", headers={"Host": "evil.example:5001"})
        assert r.status_code == 421

    @pytest.mark.parametrize(
        "host", ["localhost:5001", "127.0.0.1:5001", "[::1]:5001", "192.168.1.5:5001"]
    )
    def test_the_apps_own_addresses_are_served(self, app, host):
        with app.test_client() as c:
            r = c.get("/", headers={"Host": host})
        assert r.status_code == 200, host

    def test_operator_configured_hosts_are_served(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BPP_TRUSTED_HOSTS", "photos.home.lan, .example.net")
        from bpp.web.app import create_app

        workdir = str(tmp_path / "w2")
        os.makedirs(workdir)
        app = create_app(workdir=workdir)
        app.config["TESTING"] = True
        with app.test_client() as c:
            assert c.get("/", headers={"Host": "photos.home.lan:5001"}).status_code == 200
            assert c.get("/", headers={"Host": "pics.example.net"}).status_code == 200
            assert c.get("/", headers={"Host": "evil.example:5001"}).status_code == 421

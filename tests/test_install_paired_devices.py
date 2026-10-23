"""Paired LAN devices poll the install state like the owner's page, but
can't install and don't see package names. A 403 on the poll would make
their page reload in a loop. Real auth middleware (TESTING=False)."""

from __future__ import annotations

import os

import pytest

from bpp.scoring import install_hold as load_failures


@pytest.fixture
def app(tmp_path, monkeypatch):
    from bpp.web.app import create_app

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(load_failures, "_installed", frozenset())
    monkeypatch.setattr(load_failures, "_held", frozenset())
    workdir = str(tmp_path / "workdir")
    os.makedirs(workdir)
    app = create_app(workdir=workdir)
    app.config["TESTING"] = False
    return app


@pytest.fixture
def phone(app):
    """A test client acting as an approved, paired phone."""
    from bpp.web.share import (
        approve_device,
        find_or_create_pending_device,
        get_share_token,
        set_lan_sharing_enabled,
    )

    ctx = app.extensions["bpp"]
    with app.app_context():
        set_lan_sharing_enabled(ctx.get_conn(), True)
        token = get_share_token(ctx.get_conn())
        device = find_or_create_pending_device(ctx.get_conn(), "fp-1", "Phone", "192.168.1.50")
        approve_device(ctx.get_conn(), device["id"])
    client = app.test_client()
    client.set_cookie("bpp_share_fp", "fp-1")

    def get(path):
        return client.get(
            path,
            headers={"X-Auth-Token": token},
            environ_overrides={"REMOTE_ADDR": "192.168.1.50"},
        )

    return get


@pytest.fixture
def owner(app):
    client = app.test_client()
    token = app.extensions["bpp"].auth_token

    def get(path):
        return client.get(
            path, headers={"X-Auth-Token": token}, environ_overrides={"REMOTE_ADDR": "127.0.0.1"}
        )

    return get


@pytest.mark.parametrize("path", ["/api/v1/install/state", "/api/v1/status"])
def test_a_phone_gets_no_restart_banner_or_install_offers(phone, path):
    # It can't restart the app or install: nothing to act on.
    load_failures.hold_until_restart("inpaint")
    resp = phone(path)
    assert resp.status_code == 200, "a 403 here makes a paired phone reload in a loop"
    body = resp.get_json()
    key = "restart_pending" if "install/state" in path else "install_restart_pending"
    assert body[key] == []
    assert body["face_installable"] is False  # installing is owner-only


def test_the_owner_sees_what_waits_for_the_restart(owner):
    load_failures.hold_until_restart("inpaint")
    assert owner("/api/v1/install/state").get_json()["restart_pending"] == ["inpaint"]


def test_a_phone_is_not_shown_the_repair_command(phone, monkeypatch, tmp_path):
    from bpp.web import pip_process

    monkeypatch.setattr(pip_process, "MARKER_DIR", tmp_path)
    (tmp_path / "bpp-install-in-progress-faces.json").write_text('{"command": "pip install x"}')
    assert phone("/api/v1/install/state").get_json()["interrupted"] == []


def test_a_phone_is_not_offered_the_ai_removal_repair(phone, monkeypatch, tmp_path):
    from bpp.web import pip_process

    monkeypatch.setattr(pip_process, "MARKER_DIR", tmp_path)
    (tmp_path / "bpp-install-in-progress-inpaint.json").write_text('{"command": "x"}')
    load_failures.hold_until_restart("inpaint")
    body = phone("/api/v1/inpaint/status").get_json()
    assert (body["repair_needed"], body["restart_pending"]) == (False, False)

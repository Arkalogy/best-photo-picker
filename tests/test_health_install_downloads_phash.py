"""/api/v1/health covers the install, download and hash state too.

An operator reading health could not tell that a feature install was
waiting on a restart, that pip had been cut off mid-install, that a model
download had failed, or that the perceptual-hash backfill was still
running. Those are the states a "the app is slow / a feature is missing"
report most often comes down to.
"""

from __future__ import annotations

import os
import threading

import pytest


@pytest.fixture
def app(tmp_path):
    from bpp.web.app import create_app

    workdir = str(tmp_path / "workdir")
    os.makedirs(workdir)
    app = create_app(workdir=workdir)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def _clean_download_jobs():
    from bpp.web import model_download_jobs

    model_download_jobs._reset_for_tests()
    yield
    model_download_jobs._reset_for_tests()


def test_the_three_blocks_are_present_and_green_on_a_fresh_library(client):
    checks = client.get("/api/v1/health").get_json()["checks"]
    assert set(checks["install"]) == {"restart_pending", "interrupted", "busy"}
    assert checks["install"]["interrupted"] == []
    assert checks["downloads"] == {"running": [], "failed": []}
    assert set(checks["phash"]) == {"ready", "in_flight", "generation"}
    assert isinstance(checks["phash"]["generation"], int)


def test_a_failed_download_is_listed_and_degrades_health(client):
    from bpp.web import model_download_jobs

    def broken():
        raise RuntimeError("connection reset")

    job, _ = model_download_jobs.start("test:clip", "CLIP", broken)
    for _ in range(200):
        if job.status != "running":
            break
        threading.Event().wait(0.02)
    data = client.get("/api/v1/health").get_json()
    assert data["checks"]["downloads"]["failed"] == [
        {"key": "test:clip", "label": "CLIP", "error": job.error}
    ]
    assert data["status"] == "degraded"


def test_a_running_download_is_listed_but_not_degraded(client):
    from bpp.web import model_download_jobs

    release = threading.Event()
    model_download_jobs.start("test:slow", "pets", lambda: release.wait(5) or {})
    try:
        data = client.get("/api/v1/health").get_json()
        assert data["checks"]["downloads"]["running"] == ["test:slow"]
        assert data["status"] == "ok"
    finally:
        release.set()


def test_an_interrupted_install_degrades_health(client, monkeypatch):
    monkeypatch.setattr(
        "bpp.web.install_status.quick_state",
        lambda: {"restart_pending": [], "interrupted": ["faces"]},
    )
    data = client.get("/api/v1/health").get_json()
    assert data["checks"]["install"]["interrupted"] == ["faces"]
    assert data["status"] == "degraded"


def test_a_pending_restart_is_informational(client, monkeypatch):
    monkeypatch.setattr(
        "bpp.web.install_status.quick_state",
        lambda: {"restart_pending": ["faces"], "interrupted": []},
    )
    data = client.get("/api/v1/health").get_json()
    assert data["checks"]["install"]["restart_pending"] == ["faces"]
    assert data["status"] == "ok"


def test_the_phash_block_follows_the_store(app, client):
    store = app.extensions["bpp"].analysis_store
    store.phash_ready.clear()
    assert client.get("/api/v1/health").get_json()["checks"]["phash"]["ready"] is False
    store.phash_ready.set()
    assert client.get("/api/v1/health").get_json()["checks"]["phash"]["ready"] is True

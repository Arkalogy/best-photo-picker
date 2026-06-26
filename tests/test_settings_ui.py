"""Tests for Settings UI: defaults, API, and configuration."""

from __future__ import annotations

import os

import pytest

from bpp.config import DEFAULTS
from bpp.web.app import create_app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app(tmp_path):
    workdir = str(tmp_path / "workdir")
    os.makedirs(workdir)
    app = create_app(workdir=workdir)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()


# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------


class TestConfigDefaults:
    """Verify DEFAULTS dict has correct values for all UI-exposed settings."""

    def test_face_cluster_threshold_default(self):
        assert DEFAULTS["face_cluster_threshold"] == 0.80

    def test_face_detection_confidence_default(self):
        assert DEFAULTS["face_detection_confidence"] == 0.3

    def test_face_embedding_confidence_default(self):
        assert DEFAULTS["face_embedding_confidence"] == 0.65

    def test_max_long_side_default(self):
        assert DEFAULTS["max_long_side"] == 1024

    def test_clip_similarity_threshold_default(self):
        assert DEFAULTS["clip_similarity_threshold"] == 0.92

    def test_follow_symlinks_default(self):
        assert DEFAULTS["follow_symlinks"] is False

    def test_thumbnail_size_default(self):
        assert DEFAULTS["thumbnail_size"] == 64


# ---------------------------------------------------------------------------
# Settings API — persist and retrieve new keys
# ---------------------------------------------------------------------------


class TestSettingsAPI:
    """Settings API round-trip for all UI-exposed settings."""

    def test_save_and_get_clip_threshold(self, client):
        resp = client.put(
            "/api/v1/settings",
            json={"clip_similarity_threshold": "0.88"},
            content_type="application/json",
        )
        assert resp.status_code == 200
        resp = client.get("/api/v1/settings")
        data = resp.get_json()
        assert data["clip_similarity_threshold"] == "0.88"

    def test_save_and_get_follow_symlinks(self, client):
        resp = client.put(
            "/api/v1/settings",
            json={"follow_symlinks": "true"},
            content_type="application/json",
        )
        assert resp.status_code == 200
        resp = client.get("/api/v1/settings")
        data = resp.get_json()
        assert data["follow_symlinks"] == "true"

    def test_save_and_get_thumbnail_size(self, client):
        resp = client.put(
            "/api/v1/settings",
            json={"thumbnail_size": "128"},
            content_type="application/json",
        )
        assert resp.status_code == 200
        resp = client.get("/api/v1/settings")
        data = resp.get_json()
        assert data["thumbnail_size"] == "128"

    def test_save_and_get_face_cluster_threshold(self, client):
        resp = client.put(
            "/api/v1/settings",
            json={"face_cluster_threshold": "0.55"},
            content_type="application/json",
        )
        assert resp.status_code == 200
        resp = client.get("/api/v1/settings")
        data = resp.get_json()
        assert data["face_cluster_threshold"] == "0.55"

    def test_save_and_get_face_detection_confidence(self, client):
        resp = client.put(
            "/api/v1/settings",
            json={"face_detection_confidence": "0.30"},
            content_type="application/json",
        )
        assert resp.status_code == 200
        resp = client.get("/api/v1/settings")
        data = resp.get_json()
        assert data["face_detection_confidence"] == "0.30"

    def test_save_and_get_max_long_side(self, client):
        resp = client.put(
            "/api/v1/settings",
            json={"max_long_side": "2048"},
            content_type="application/json",
        )
        assert resp.status_code == 200
        resp = client.get("/api/v1/settings")
        data = resp.get_json()
        assert data["max_long_side"] == "2048"


# ---------------------------------------------------------------------------
# Status API — face_cluster_threshold default
# ---------------------------------------------------------------------------


class TestStatusDefaults:
    """Verify /api/status returns correct defaults for settings."""

    def test_status_face_cluster_threshold_default(self, client):
        resp = client.get("/api/v1/status")
        data = resp.get_json()
        assert data["face_cluster_threshold"] == 0.80

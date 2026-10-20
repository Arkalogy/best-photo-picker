"""Shared fixtures and helpers of the test_blueprint_coverage.py suite (split for the
500-LOC cap)."""

from __future__ import annotations

import json
import os

import pytest

from bpp.web.app import create_app


def _make_analysis(n: int = 10) -> list[dict]:
    """Create synthetic analysis data for testing."""
    items = []
    for i in range(n):
        items.append(
            {
                "filepath": f"/tmp/test_photos/img_{i:03d}.jpg",
                "date": f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}T12:00:00",
                "date_day": f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}",
                "date_month": f"2024-{(i % 12) + 1:02d}",
                "file_size": 1024 * (i + 1),
                "file_mtime": 1700000000.0 + i,
                "blur_raw": 100.0 + i * 50,
                "blur_score": i / max(n - 1, 1),
                "exposure_score": 0.5 + (i % 3) * 0.15,
                "face_score": 0.3 + (i % 4) * 0.1,
                "face_count": i % 3,
                "largest_face_ratio": 0.05,
                "face_center_dist": 0.3,
                "composition_score": 0.4 + (i % 5) * 0.1,
                "aggregate_score": 0.3 + i * 0.05,
            }
        )
    return items


@pytest.fixture
def bp_app(tmp_path):
    """Create a Flask app for blueprint testing with analysis data loaded."""
    workdir = str(tmp_path / "workdir")
    os.makedirs(workdir)
    analysis = _make_analysis(10)
    with open(os.path.join(workdir, "analysis.json"), "w") as f:
        json.dump(analysis, f)
    app = create_app(workdir=workdir)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(bp_app):
    return bp_app.test_client()


def _get_ctx(bp_app):
    """Get the WebAppState from the app."""
    return bp_app.extensions["bpp"]


def _get_all_album_id(bp_app):
    """Get the 'All Photos' album ID."""
    ctx = _get_ctx(bp_app)
    with bp_app.app_context():
        conn = ctx.get_conn()
        row = conn.execute("SELECT id FROM albums WHERE album_type='all' LIMIT 1").fetchone()
        return row[0] if row else None


def _get_photo_ids(bp_app):
    """Get all photo IDs from the DB."""
    ctx = _get_ctx(bp_app)
    with bp_app.app_context():
        conn = ctx.get_conn()
        rows = conn.execute("SELECT id FROM photos ORDER BY id").fetchall()
        return [r[0] for r in rows]


def _get_filepaths(bp_app):
    """Get all filepaths from the DB."""
    ctx = _get_ctx(bp_app)
    with bp_app.app_context():
        conn = ctx.get_conn()
        rows = conn.execute("SELECT filepath FROM photos ORDER BY id").fetchall()
        return [r[0] for r in rows]


def _create_manual_album(client, name="Test Album"):
    """Helper to create a manual album and return its ID."""
    resp = client.post("/api/v1/albums", json={"name": name}, content_type="application/json")
    return resp.get_json()["id"]


def _add_photos_to_album(bp_app, album_id, photo_ids):
    """Directly add photos to an album via DB."""
    ctx = _get_ctx(bp_app)
    with bp_app.app_context():
        conn = ctx.get_conn()
        conn.executemany(
            "INSERT OR IGNORE INTO album_photos (album_id, photo_id) VALUES (?, ?)",
            [(album_id, pid) for pid in photo_ids],
        )
        conn.commit()

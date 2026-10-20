"""Shared fixtures and helpers of the test_web.py suite (split for the 500-LOC cap)."""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3

import pytest


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


def _default_config() -> dict:
    from bpp.config import DEFAULTS

    return dict(DEFAULTS)


# --- Flask route smoke tests ---


@pytest.fixture
def web_client(tmp_path):
    """Create a Flask test client with a temp workdir."""
    from bpp.web.app import create_app

    workdir = str(tmp_path / "workdir")
    os.makedirs(workdir)

    # Write analysis.json
    analysis = _make_analysis(10)
    with open(os.path.join(workdir, "analysis.json"), "w") as f:
        json.dump(analysis, f)

    app = create_app(workdir=workdir)
    app.config["TESTING"] = True
    return app.test_client()


# --- Face merge/dismiss route tests ---


def _setup_face_db(conn: sqlite3.Connection, analysis: list[dict]) -> None:
    """Seed face_embeddings in photopicker.db for testing.

    Embeddings must be 512 * 8 = 4096 bytes so that np.frombuffer(...,
    dtype=np.float32) succeeds in the merge-feedback path. A 1-byte
    X'00' blob crashes the centroid computation and caused pre-existing
    test failures.
    """
    import numpy as np

    _emb = np.zeros(512, dtype=np.float32).tobytes()
    # Cluster 0: first 3 photos, Cluster 1: next 2 photos
    for i in range(3):
        photo = conn.execute(
            "SELECT id FROM photos WHERE filepath=?", (analysis[i]["filepath"],)
        ).fetchone()
        if photo:
            conn.execute(
                "INSERT OR REPLACE INTO face_embeddings "
                "(photo_id, face_index, embedding, cluster_id) "
                "VALUES (?, 0, ?, 0)",
                (photo[0], _emb),
            )
    for i in range(3, 5):
        photo = conn.execute(
            "SELECT id FROM photos WHERE filepath=?", (analysis[i]["filepath"],)
        ).fetchone()
        if photo:
            conn.execute(
                "INSERT OR REPLACE INTO face_embeddings "
                "(photo_id, face_index, embedding, cluster_id) "
                "VALUES (?, 0, ?, 1)",
                (photo[0], _emb),
            )
    conn.commit()


@pytest.fixture
def face_web_client(tmp_path):
    """Create a Flask test client with face_embeddings seeded."""
    from bpp.db.connection import close_all_connections
    from bpp.web.app import create_app

    # Defensive baseline: clear any leftover pool connections from a
    # prior test before we spin up a fresh app. The previous test's
    # teardown should have handled this, but the Analysis worker is
    # known to occasionally exceed WORKER_JOIN_TIMEOUT_S — if it
    # holds a connection past shutdown, the pool can be in a state
    # that pollutes the next face-route test (observed once under
    # randomized seed 119532108).
    close_all_connections()

    workdir = str(tmp_path / "workdir")
    os.makedirs(workdir)

    analysis = _make_analysis(10)
    with open(os.path.join(workdir, "analysis.json"), "w") as f:
        json.dump(analysis, f)

    app = create_app(workdir=workdir)
    app.config["TESTING"] = True
    client = app.test_client()

    # Trigger a /api/photos call to load analysis and init DB
    client.get("/api/v1/photos")

    from bpp.db.connection import get_db
    from bpp.db.smart_albums import refresh_smart_albums

    db_path = os.path.join(workdir, "photopicker.db")
    conn = get_db(db_path)
    _setup_face_db(conn, analysis)
    refresh_smart_albums(conn)

    yield client

    # Teardown: shut down background workers (phash compute etc.) so they
    # don't race with the next test's DB writes in CI.
    with app.app_context():
        from bpp.web.state import get_ctx

        with contextlib.suppress(Exception):
            get_ctx().shutdown()
    from bpp.db.connection import close_all_connections

    close_all_connections()

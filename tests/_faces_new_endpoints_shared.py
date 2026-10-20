"""Shared fixtures and helpers of the test_faces_new_endpoints.py suite (split for the 500-LOC
cap)."""

from __future__ import annotations

import json
import os

import numpy as np
import pytest

from bpp.web.app import create_app
from tests._faces_create_shared import _ctx


def _make_analysis(n: int = 10) -> list[dict]:
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


def _get_photo_id_and_hash(bp_app):
    """Return (photo_id, thumb_hash) for the first photo."""
    ctx = _ctx(bp_app)
    with bp_app.app_context():
        conn = ctx.get_conn()
        row = conn.execute("SELECT id, filepath FROM photos LIMIT 1").fetchone()
        pid = row["id"]
        fp = row["filepath"]
        th = ctx.thumbs.get_hash(fp) if ctx.thumbs else None
        return pid, th


def _insert_face(bp_app, photo_id, face_index=0, cluster_id=0):
    """Insert a face embedding and return its id."""
    ctx = _ctx(bp_app)
    with bp_app.app_context():
        conn = ctx.get_conn()
        emb = np.random.randn(128).astype(np.float32).tobytes()
        conn.execute(
            "INSERT INTO face_embeddings"
            " (photo_id, face_index, embedding, cluster_id,"
            " bbox_x, bbox_y, bbox_w, bbox_h)"
            " VALUES (?, ?, ?, ?, 10, 20, 50, 60)",
            (photo_id, face_index, emb, cluster_id),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id FROM face_embeddings WHERE photo_id=? AND face_index=?",
            (photo_id, face_index),
        ).fetchone()
        return row["id"]

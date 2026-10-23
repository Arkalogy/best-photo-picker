"""Shared fixtures and helpers of the test_faces_create.py suite (split for the 500-LOC cap)."""

from __future__ import annotations

import json
import os

import numpy as np
import pytest

from bpp.web.app import create_app


def _make_analysis(n: int = 4) -> list[dict]:
    items = []
    for i in range(n):
        items.append(
            {
                "filepath": f"/tmp/test_photos/img_{i:03d}.jpg",
                "date": f"2024-01-{(i % 28) + 1:02d}T12:00:00",
                "date_day": f"2024-01-{(i % 28) + 1:02d}",
                "date_month": "2024-01",
                "file_size": 1024 * (i + 1),
                "file_mtime": 1700000000.0 + i,
                "blur_raw": 100.0,
                "blur_score": 0.5,
                "exposure_score": 0.6,
                "face_score": 0.4,
                "face_count": 0,
                "largest_face_ratio": 0.0,
                "face_center_dist": 0.5,
                "composition_score": 0.5,
                "aggregate_score": 0.5,
            }
        )
    return items


@pytest.fixture
def bp_app(tmp_path):
    workdir = str(tmp_path / "workdir")
    os.makedirs(workdir)
    with open(os.path.join(workdir, "analysis.json"), "w") as f:
        json.dump(_make_analysis(4), f)
    app = create_app(workdir=workdir)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(bp_app):
    return bp_app.test_client()


def _ctx(bp_app):
    return bp_app.extensions["bpp"]


def _path_hash_for(bp_app, photo_idx=0):
    ctx = _ctx(bp_app)
    with bp_app.app_context():
        conn = ctx.get_conn()
        row = conn.execute(
            "SELECT filepath FROM photos ORDER BY id LIMIT 1 OFFSET ?",
            (photo_idx,),
        ).fetchone()
        return ctx.thumbs.get_hash(row["filepath"]) if ctx.thumbs else None


def _seed_cluster(bp_app, cluster_id, photo_idx, *, embedding=None):
    """Insert a face row to make ``cluster_id`` exist in the library."""
    ctx = _ctx(bp_app)
    with bp_app.app_context():
        conn = ctx.get_conn()
        photo_row = conn.execute(
            "SELECT id FROM photos ORDER BY id LIMIT 1 OFFSET ?",
            (photo_idx,),
        ).fetchone()
        if embedding is None:
            embedding = np.random.randn(128).astype(np.float32)
        conn.execute(
            "INSERT INTO face_embeddings"
            " (photo_id, face_index, embedding, cluster_id,"
            "  bbox_x, bbox_y, bbox_w, bbox_h, quality)"
            " VALUES (?, 0, ?, ?, 100, 100, 200, 200, 0.7)",
            (photo_row["id"], embedding.tobytes(), cluster_id),
        )
        conn.commit()


def _stub_pipeline(monkeypatch, embedding, *, method="yunet", quality=0.8, bbox=None):
    fake_image = np.zeros((1024, 1024, 3), dtype=np.uint8)

    def fake_load(_filepath, _max_long_side):
        return fake_image

    def fake_extract(_image, _bbox, **_kw):
        return {
            "bbox": bbox or (50, 50, 200, 200),
            "embedding": np.asarray(embedding, dtype=np.float32),
            "quality": quality,
            "method": method,
        }

    monkeypatch.setattr("bpp.scoring.aggregate.load_and_downscale", fake_load)
    monkeypatch.setattr(
        "bpp.scoring.face_embed.extract_embedding_for_region",
        fake_extract,
    )

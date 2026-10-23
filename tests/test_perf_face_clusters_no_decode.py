"""The People list must not decode every face embedding per request.

load_face_clusters feeds /api/v1/faces/clusters. It only needs an
embedding to pick a representative face, and only for clusters that
have no quality scores; everything else is counts and file paths.
"""

from __future__ import annotations

import numpy as np

from bpp.db.connection import init_db
from bpp.db.photos import upsert_photo


def _emb(rng):
    v = rng.normal(size=128).astype(np.float32)
    return (v / np.linalg.norm(v)).tobytes()


def _face(conn, rng, photo_id, face_index, cluster_id, quality):
    conn.execute(
        "INSERT INTO face_embeddings (photo_id, face_index, embedding, cluster_id, "
        "bbox_x, bbox_y, bbox_w, bbox_h, quality) VALUES (?, ?, ?, ?, 1, 1, 50, 50, ?)",
        (photo_id, face_index, _emb(rng), cluster_id, quality),
    )


def test_quality_scored_clusters_need_no_embedding_decode(tmp_path, monkeypatch):
    import bpp.db.face_queries as fq

    conn = init_db(str(tmp_path / "t.db"))
    rng = np.random.default_rng(0)
    pids = []
    for i in range(4):
        (tmp_path / f"p{i}.jpg").write_bytes(b"x")
        pids.append(upsert_photo(conn, {"filepath": str(tmp_path / f"p{i}.jpg")}))
    _face(conn, rng, pids[0], 0, 0, 0.2)
    _face(conn, rng, pids[1], 0, 0, 0.9)
    _face(conn, rng, pids[1], 1, 0, 0.5)
    _face(conn, rng, pids[2], 0, 1, None)
    _face(conn, rng, pids[3], 0, 1, None)
    conn.commit()

    decoded: list[int] = []
    real = fq.decode_embedding
    monkeypatch.setattr(fq, "decode_embedding", lambda *a, **k: decoded.append(1) or real(*a, **k))

    clusters = {c["cluster_id"]: c for c in fq.load_face_clusters(conn)}
    assert clusters[0]["face_count"] == 3
    assert clusters[0]["photo_count"] == 2
    assert clusters[0]["representative"]["face_index"] == 0
    assert clusters[0]["representative"]["filepath"] == str(tmp_path / "p1.jpg")
    assert clusters[0]["representative"]["bbox"] == (1, 1, 50, 50)
    assert sorted(clusters[0]["filepaths"]) == sorted(str(tmp_path / f"p{i}.jpg") for i in (0, 1))
    assert clusters[1]["face_count"] == 2
    assert len(decoded) == 2, "only the cluster without quality scores is decoded"

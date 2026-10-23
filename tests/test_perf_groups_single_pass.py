"""Group detection counts shared photos in one pass, not one INTERSECT
query per group, and the groups endpoint doesn't fetch embeddings it
never reads."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from bpp.db.connection import init_db
from bpp.db.groups import detect_groups
from bpp.db.photos import upsert_photo

REPO_ROOT = Path(__file__).resolve().parent.parent


def _photo(conn, path):
    path.write_bytes(b"x")
    return upsert_photo(conn, {"filepath": str(path)})


def _face(conn, photo_id, face_index, cluster_id):
    conn.execute(
        "INSERT INTO face_embeddings (photo_id, face_index, embedding, cluster_id) "
        "VALUES (?, ?, ?, ?)",
        (photo_id, face_index, np.zeros(128, np.float32).tobytes(), cluster_id),
    )


def test_detect_groups_runs_no_per_group_query(tmp_path):
    conn = init_db(str(tmp_path / "t.db"))
    # people 0, 1, 2 share four photos; people 3, 4 share four others
    for i in range(4):
        pid = _photo(conn, tmp_path / f"a{i}.jpg")
        for fi, cid in enumerate((0, 1, 2)):
            _face(conn, pid, fi, cid)
    for i in range(4):
        pid = _photo(conn, tmp_path / f"b{i}.jpg")
        for fi, cid in enumerate((3, 4)):
            _face(conn, pid, fi, cid)
    # person 0 also has a photo of their own
    pid = _photo(conn, tmp_path / "solo.jpg")
    _face(conn, pid, 0, 0)
    conn.commit()

    captured: list[str] = []
    conn.set_trace_callback(captured.append)
    try:
        groups = detect_groups(conn, min_photos=2)
    finally:
        conn.set_trace_callback(None)

    by_members = {tuple(g["members"]): g["photo_count"] for g in groups}
    assert by_members[(0, 1, 2)] == 4
    assert by_members[(3, 4)] == 4
    assert not [s for s in captured if "INTERSECT" in s.upper()]
    assert len(captured) <= 8, f"query count grows with the groups: {captured}"


def test_groups_endpoint_does_not_fetch_embeddings():
    src = (REPO_ROOT / "bpp" / "web" / "bp_groups.py").read_text()
    assert "fe.embedding" not in src

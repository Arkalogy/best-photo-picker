"""Every clustering pass tells the plugins it ran.

``on_post_cluster`` fired after face extraction only. Pet clustering,
a manual face recluster and near-duplicate clustering changed cluster
ids without a word to the plugins that subscribe to them.
"""

from __future__ import annotations

import pytest
from PIL import Image

from bpp.db import event_hooks


@pytest.fixture
def seen():
    calls: list[tuple[str, int]] = []
    event_hooks.register_post_cluster_hook(lambda conn, kind, n: calls.append((kind, n)))
    yield calls
    event_hooks._reset_for_tests()


def test_near_duplicate_clustering_dispatches(tmp_path, seen):
    from bpp.db.connection import init_db
    from bpp.db.dedupe import assign_near_duplicate_clusters
    from bpp.db.photos import upsert_photo

    conn = init_db(str(tmp_path / "t.db"))
    for i in range(2):
        p = str(tmp_path / f"a{i}.jpg")
        Image.new("RGB", (4, 4), "gray").save(p, "JPEG")
        upsert_photo(
            conn, {"filepath": p, "date": f"2024-01-01T00:00:0{i}", "phash": 7, "ahash": 7}
        )
    conn.commit()
    assert assign_near_duplicate_clusters(conn) == 2
    assert seen == [("near_duplicate", 1)]


def test_pet_clustering_dispatches(tmp_path, seen):
    from bpp.db.connection import init_db
    from bpp.db.pets import assign_pet_clusters
    from bpp.db.photos import upsert_photo

    conn = init_db(str(tmp_path / "t.db"))
    p = str(tmp_path / "cat.jpg")
    Image.new("RGB", (4, 4), "gray").save(p, "JPEG")
    upsert_photo(conn, {"filepath": p, "date": "2024-01-01T00:00:00"})
    pid = conn.execute("SELECT id FROM photos").fetchone()[0]
    conn.execute(
        "INSERT INTO pet_detections (photo_id, detection_index, class, confidence, bbox_x, bbox_y,"
        " bbox_w, bbox_h, cluster_id) VALUES (?, 0, 'cat', 0.9, 0, 0, 1, 1, -1)",
        (pid,),
    )
    conn.commit()
    assign_pet_clusters(conn)
    assert seen == [("pet", 1)]
    assign_pet_clusters(conn)  # nothing new to assign: plugins are not woken
    assert seen == [("pet", 1)]


def test_the_manual_face_recluster_dispatches_in_source():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    src = (root / "bpp" / "web" / "bp_faces_recluster.py").read_text()
    assert 'dispatch_post_cluster(conn, "face", n_clusters)' in src

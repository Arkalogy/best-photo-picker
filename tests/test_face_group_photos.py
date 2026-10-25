"""Re-placing a person later counts only photos with a single face: a
group photo cannot say which face is whose. Exercises the recluster
endpoint body and its refresh."""

from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pytest
from flask import Flask

from bpp.constants import CLUSTER_UNPLACED
from bpp.db.connection import close_all_connections, get_db, init_db
from bpp.db.photos import bulk_upsert_photos


@pytest.fixture
def conn(tmp_path):
    path = str(tmp_path / "library.db")
    init_db(path)
    db = get_db(path)
    bulk_upsert_photos(
        db, [{"filepath": str(tmp_path / f"{i}.jpg"), "missing": 0} for i in range(5)]
    )
    db.execute("UPDATE photos SET missing=0")
    db.commit()
    yield db
    close_all_connections()


def _alice(db, cid: int, pids: list[int]) -> int:
    aid = db.execute(
        "INSERT INTO albums (name, album_type, rule_json) VALUES ('Alice', 'smart_person', ?)",
        (json.dumps({"cluster_id": cid}),),
    ).lastrowid
    db.executemany(
        "INSERT INTO album_photos (album_id, photo_id) VALUES (?, ?)", [(aid, p) for p in pids]
    )
    db.execute("INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (5, ?)", (cid,))
    db.commit()
    return aid


def _vec(i: int) -> bytes:
    v = np.zeros(128, dtype=np.float32)
    v[i] = 1
    return v.tobytes()


def _faces(db, rows) -> None:
    db.executemany(
        "INSERT INTO face_embeddings (photo_id, face_index, cluster_id, embedding, identity) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )


def _recluster(db, monkeypatch) -> None:
    from bpp.web import bp_faces_recluster as endpoint

    ctx = SimpleNamespace(
        get_conn=lambda: db, config={}, face_worker=SimpleNamespace(is_alive=False)
    )
    monkeypatch.setattr(endpoint, "get_ctx", lambda: ctx)
    fn = endpoint.api_faces_recluster
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    with Flask(__name__).test_request_context(json={"threshold": 0.5}):
        _resp, code = fn()
    assert code == 200


def _cluster_of(db, aid: int) -> int:
    return db.execute("SELECT smart_person_cluster_id FROM albums WHERE id=?", (aid,)).fetchone()[0]


def test_an_unreadable_face_in_a_group_photo_does_not_name_the_other_face(conn, monkeypatch):
    _faces(conn, [(1, 0, 9, b"damaged", "Alice"), (1, 1, 8, _vec(1), None)])
    aid = _alice(conn, 9, [1])
    _recluster(conn, monkeypatch)
    assert _cluster_of(conn, aid) == CLUSTER_UNPLACED, "kept, not put on the other face"
    assert conn.execute("SELECT COUNT(*) FROM photo_person_tags").fetchone()[0] == 0


def test_a_split_person_is_placed_by_their_solo_photos_not_the_group_ones(conn, monkeypatch):
    # Alice's faces split 2/2 over two clusters; photos 3 and 4 are group
    # shots with a stranger. By whole photos it is a three-way tie (no
    # placement); by solo photos only cluster of photos 1 and 2 counts.
    _faces(
        conn,
        [
            (1, 0, 9, _vec(0), "Alice"),
            (2, 0, 9, _vec(0), "Alice"),
            (3, 0, 9, _vec(1), "Alice"),
            (4, 0, 9, _vec(1), "Alice"),
            (3, 1, 8, _vec(2), None),
            (4, 1, 8, _vec(2), None),
        ],
    )
    aid = _alice(conn, 9, [1, 2, 3, 4])
    _recluster(conn, monkeypatch)
    cid = _cluster_of(conn, aid)
    solo_cluster = conn.execute(
        "SELECT cluster_id FROM face_embeddings WHERE photo_id=1"
    ).fetchone()[0]
    assert cid == solo_cluster, "her two solo photos decide it (the group photos do not count)"


def test_a_dismissed_false_face_does_not_make_a_solo_photo_a_group_one(conn):
    from bpp.constants import CLUSTER_DISMISSED
    from bpp.db.face_switch_record import single_face_photos as _single_face_photos

    _faces(conn, [(1, 0, 3, _vec(0), None), (1, 1, CLUSTER_DISMISSED, _vec(1), None)])
    _faces(conn, [(2, 0, 3, _vec(0), None), (2, 1, -1, _vec(1), None)])
    conn.commit()
    assert _single_face_photos(conn, {1, 2}) == {1}, "a dismissed face is no person"

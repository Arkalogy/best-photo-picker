"""Identity boundaries across switch, recluster and refresh: deleted
original evidence, a stashed default person on the next switch, an
orphan transfer's album contents, a tagged group photo after recluster,
and extraction staying allowed while a switch is pending. Synthetic data,
no models."""

# ruff: noqa: F811
import json

import numpy as np
import pytest

from bpp.constants import CLUSTER_UNPLACED
from bpp.db.connection import get_db, init_db
from bpp.db.face_identity_remap import _snapshot_cluster_photos
from bpp.db.face_name_placement import begin_switch, finish_switch
from bpp.db.face_switch_record import STASHED_TAGS
from bpp.db.photos import bulk_upsert_photos
from bpp.db.smart_album_people import _refresh_person_albums
from tests._web_shared import face_web_client  # noqa: F401


@pytest.fixture
def lib(tmp_path):
    path = str(tmp_path / "synthetic.db")
    init_db(path)
    conn = get_db(path)
    bulk_upsert_photos(conn, [{"filepath": f"/synthetic/{n}.jpg", "missing": 0} for n in range(6)])
    conn.execute("UPDATE photos SET missing=0")
    conn.commit()
    return conn, [r[0] for r in conn.execute("SELECT id FROM photos ORDER BY id")]


def faces(conn, mapping):
    conn.execute("DELETE FROM face_embeddings")
    rows = []
    indexes = {}
    for cid, pids in mapping.items():
        for pid in sorted(pids):
            fi = indexes.get(pid, 0)
            indexes[pid] = fi + 1
            rows.append((pid, fi, cid, np.array([1.0, 0.0], dtype=np.float32).tobytes()))
    conn.executemany(
        "INSERT INTO face_embeddings (photo_id,face_index,cluster_id,embedding) VALUES (?,?,?,?)",
        rows,
    )
    conn.commit()


def person(conn, name, cid, pids):
    aid = conn.execute(
        "INSERT INTO albums (name,album_type,rule_json) VALUES (?,'smart_person',?)",
        (name, json.dumps({"cluster_id": cid})),
    ).lastrowid
    conn.executemany(
        "INSERT INTO album_photos (album_id,photo_id) VALUES (?,?)", [(aid, p) for p in pids]
    )
    conn.commit()
    return aid


def state(conn, aid):
    row = conn.execute(
        "SELECT name,smart_person_cluster_id,config_json FROM albums WHERE id=?", (aid,)
    ).fetchone()
    return {
        "album": tuple(row) if row else None,
        "photos": sorted(
            r[0] for r in conn.execute("SELECT photo_id FROM album_photos WHERE album_id=?", (aid,))
        ),
        "tags": [
            tuple(r) for r in conn.execute("SELECT photo_id,cluster_id FROM photo_person_tags")
        ],
    }


def test_deleted_original_evidence_cannot_name_a_stranger(lib):
    conn, p = lib
    faces(conn, {7: {p[0]}})
    aid = person(conn, "Alice", 7, {p[0]})
    begin_switch(conn, "sface", _snapshot_cluster_photos(conn))
    conn.commit()
    # First chunk groups the old photo with a stranger's photo.
    faces(conn, {0: {p[0], p[1]}})
    _refresh_person_albums(conn)
    # The owner permanently deletes Alice's only evidence photo meanwhile.
    from bpp.db.photos import permanent_delete_photos, soft_delete_photos

    soft_delete_photos(conn, [p[0]])
    permanent_delete_photos(conn, [p[0]])
    finish_switch(conn, _snapshot_cluster_photos(conn))
    actual = state(conn, aid)
    assert actual["album"][1] == CLUSTER_UNPLACED, (
        "No surviving evidence of Alice; her name must not label the stranger"
    )


def test_second_switch_preserves_stashed_tags_of_default_person(lib):
    conn, p = lib
    aid = person(conn, "Person 8", 7, {p[0]})
    conn.execute("INSERT INTO photo_person_tags (photo_id,cluster_id) VALUES (?,7)", (p[0],))
    conn.commit()
    begin_switch(conn, "sface", {})
    conn.commit()
    faces(conn, {0: {p[1]}})
    finish_switch(conn, _snapshot_cluster_photos(conn))
    before = state(conn, aid)
    assert STASHED_TAGS in json.loads(before["album"][2])
    # Next Retry/model switch, after the previous run completed.
    begin_switch(conn, "dlib", _snapshot_cluster_photos(conn))
    conn.commit()
    after = state(conn, aid)
    assert after["album"] is not None, (
        "The only durable copy of the owner's manual tag was on this album"
    )


def test_orphan_transfer_rebuilds_destination_contents_immediately(lib):
    conn, p = lib
    aid = person(conn, "Alice", 7, {p[0]})
    faces(conn, {2: {p[0], p[1]}})
    _refresh_person_albums(conn)
    first = state(conn, aid)
    assert first["photos"] == [p[0], p[1]], (
        "Moving the album must also populate its target cluster's full photo list"
    )
    _refresh_person_albums(conn)
    assert state(conn, aid) == first, "a second refresh changes nothing"


def test_recluster_does_not_use_stashed_group_tag_as_name_evidence(lib):
    from bpp.db.face_identity_remap import _remap_names_and_tags

    conn, p = lib
    old = {0: {p[0]}, 1: {p[1]}}
    faces(conn, old)
    aid = person(conn, "Alice", 0, {p[0], p[1]})
    conn.execute("INSERT INTO photo_person_tags (photo_id,cluster_id) VALUES (?,0)", (p[1],))
    conn.commit()
    # Alice's embedding is unusable during recluster; the other person's
    # face in her manually tagged group photo is assigned her old id.
    faces(conn, {0: {p[1]}})
    assert _remap_names_and_tags(conn, old, _snapshot_cluster_photos(conn))
    assert state(conn, aid)["album"][1] == CLUSTER_UNPLACED
    _refresh_person_albums(conn)
    assert state(conn, aid)["album"][1] == CLUSTER_UNPLACED, (
        "A manual group tag must not undo the evidence-based decision to leave Alice unplaced"
    )


def test_recluster_over_http_keeps_an_unmatched_person_off_the_stranger(face_web_client, tmp_path):
    conn = get_db(str(tmp_path / "workdir" / "photopicker.db"))
    conn.execute("DELETE FROM face_embeddings")
    conn.execute("DELETE FROM albums WHERE album_type='smart_person'")
    conn.execute(
        "INSERT OR REPLACE INTO settings (key,value) "
        "VALUES ('face_embedding_method_extracted','sface')"
    )
    p = [r[0] for r in conn.execute("SELECT id FROM photos ORDER BY id")]
    conn.execute("UPDATE photos SET missing=0")
    # Same size, but Alice's row is invalid (zero norm). The stranger's
    # valid row really goes through decode_rows + cluster_faces.
    conn.executemany(
        "INSERT INTO face_embeddings (photo_id,face_index,cluster_id,embedding) VALUES (?,0,?,?)",
        [
            (p[0], 0, np.zeros(128, dtype=np.float32).tobytes()),
            (p[1], 1, np.ones(128, dtype=np.float32).tobytes()),
        ],
    )
    aid = person(conn, "Alice", 0, {p[0], p[1]})
    conn.execute("INSERT INTO photo_person_tags (photo_id,cluster_id) VALUES (?,0)", (p[1],))
    conn.commit()
    response = face_web_client.post("/api/v1/faces/recluster", json={"threshold": 0.6})
    assert response.status_code == 200
    assert (
        conn.execute("SELECT cluster_id FROM face_embeddings WHERE photo_id=?", (p[0],)).fetchone()[
            0
        ]
        == -1
    )
    assert state(conn, aid)["album"][1] == CLUSTER_UNPLACED, (
        "Alice must remain unplaced after her only usable face evidence was excluded"
    )


def test_extract_and_retry_really_return_202_while_pending(face_web_client, tmp_path, monkeypatch):
    from bpp.scoring import face_embed
    from bpp.web import bp_faces_extract
    from bpp.web.face_worker import FaceWorker

    conn = get_db(str(tmp_path / "workdir" / "photopicker.db"))
    begin_switch(conn, "sface", _snapshot_cluster_photos(conn))
    conn.commit()
    # Stub only model availability/selection and the worker start;
    # routing, authorization, guards and the Retry wipe remain real.
    monkeypatch.setattr(face_embed, "embedding_method", lambda conn=None: "sface")
    monkeypatch.setattr(bp_faces_extract, "face_recognition_available", lambda: True)
    monkeypatch.setattr(FaceWorker, "start", lambda *args, **kwargs: True)
    for route in ("/api/v1/faces/extract", "/api/v1/faces/retry"):
        response = face_web_client.post(route, json={})
        assert response.status_code == 202, (route, response.get_json())

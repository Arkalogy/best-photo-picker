"""Re-clustering keeps two people who share every photo apart (it matches
by face, not by photo), an unmatched person keeps their face evidence when
their old number vanishes, a failed Retry leaves nothing half-done, and an
Analyze run with nothing to extract ends a pending switch."""

import json
import threading
from types import SimpleNamespace

import numpy as np
import pytest
from flask import Flask

from bpp.db.connection import get_db, init_db
from bpp.db.photos import bulk_upsert_photos


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "probe.db")
    init_db(path)
    conn = get_db(path)
    bulk_upsert_photos(conn, [{"filepath": str(tmp_path / f"{i}.jpg")} for i in range(4)])
    conn.execute("UPDATE photos SET missing=0")
    conn.commit()
    return conn


def add_faces(conn, rows):
    conn.executemany(
        "INSERT INTO face_embeddings (photo_id, face_index, cluster_id, embedding, identity) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()


def person(conn, name, cid, pids):
    cur = conn.execute(
        "INSERT INTO albums (name, album_type, rule_json) VALUES (?, 'smart_person', ?)",
        (name, json.dumps({"cluster_id": cid})),
    )
    aid = cur.lastrowid
    conn.executemany(
        "INSERT INTO album_photos (album_id, photo_id) VALUES (?, ?)", [(aid, p) for p in pids]
    )
    conn.commit()
    return aid


def recluster(conn, monkeypatch):
    from bpp.web import bp_faces_recluster as endpoint

    ctx = SimpleNamespace(
        get_conn=lambda: conn, config={}, face_worker=SimpleNamespace(is_alive=False)
    )
    monkeypatch.setattr(endpoint, "get_ctx", lambda: ctx)
    # Run the actual endpoint body inside a request; auth/locking do not influence matching.
    fn = endpoint.api_faces_recluster
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    with Flask(__name__).test_request_context(json={"threshold": 0.5}):
        _response, status = fn()
        assert status == 200


def test_recluster_group_photos_keep_face_identity(db, monkeypatch):
    """Two people always photographed together, old IDs opposite face-index order."""
    a = np.zeros(128, dtype=np.float32)
    a[0] = 1
    b = np.zeros(128, dtype=np.float32)
    b[1] = 1
    add_faces(
        db,
        [
            (p, fi, cid, emb.tobytes(), name)
            for p in (1, 2)
            for fi, cid, emb, name in ((0, 7, a, "Alice"), (1, 3, b, "Bob"))
        ],
    )
    person(db, "Alice", 7, [1, 2])
    person(db, "Bob", 3, [1, 2])
    db.execute("INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (3, 7)")
    db.commit()
    recluster(db, monkeypatch)
    actual = [
        tuple(r)
        for r in db.execute(
            "SELECT a.name, fe.identity, fe.cluster_id FROM albums a JOIN face_embeddings fe "
            "ON a.smart_person_cluster_id=fe.cluster_id WHERE a.album_type='smart_person' "
            "ORDER BY a.name,fe.id"
        )
    ]
    assert all(name == identity for name, identity, _ in actual), actual
    assert {name for name, _i, _c in actual} == {"Alice", "Bob"}, "both placed, each on their own"


def test_unmatched_vanished_id_retains_face_evidence(db, monkeypatch):
    """Alice's invalid face is skipped; her tagged group photo has another person's face."""
    b = np.zeros(128, dtype=np.float32)
    b[1] = 1
    add_faces(db, [(1, 0, 9, b"damaged", "Alice"), (2, 0, 8, b.tobytes(), "Bob")])
    aid = person(db, "Alice", 9, [1, 2])
    db.execute("INSERT INTO photo_person_tags (photo_id,cluster_id) VALUES (2,9)")
    db.commit()
    recluster(db, monkeypatch)
    album = tuple(
        db.execute(
            "SELECT name,smart_person_cluster_id,config_json FROM albums WHERE id=?", (aid,)
        ).fetchone()
    )
    evidence = [
        r[0] for r in db.execute("SELECT photo_id FROM album_photos WHERE album_id=?", (aid,))
    ]
    assert album[1] != 0, "Alice must not become the album of Bob's valid face"
    assert evidence == [1]


def test_retry_wipe_failure_does_not_leave_partial_parking(db, monkeypatch, tmp_path):
    """A DB refusal during DELETE must roll back earlier name/tag parking."""
    from bpp.db.face_name_placement import switch_pending
    from bpp.errors import BppError
    from bpp.scoring import face_embed
    from bpp.web.bp_faces_extract import _wipe_faces_and_restart

    emb = np.zeros(128, dtype=np.float32).tobytes()
    add_faces(db, [(1, 0, 9, emb, "Alice")])
    aid = person(db, "Alice", 9, [1])
    db.execute("INSERT INTO photo_person_tags (photo_id,cluster_id) VALUES (2,9)")
    db.execute(
        "CREATE TRIGGER reject_wipe BEFORE DELETE ON face_embeddings "
        "BEGIN SELECT RAISE(ABORT, 'injected write failure'); END"
    )
    db.commit()
    monkeypatch.setattr(face_embed, "embedding_method", lambda conn: "sface")
    ctx = SimpleNamespace(get_conn=lambda: db, dirs={"face_crops": str(tmp_path / "crops")})
    with pytest.raises(BppError):
        _wipe_faces_and_restart(ctx, [])
    # A later independent request on the pooled connection commits its own setting.
    db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES ('probe_setting','yes')")
    db.commit()
    actual = db.execute("SELECT smart_person_cluster_id FROM albums WHERE id=?", (aid,)).fetchone()[
        0
    ]
    tags = [tuple(r) for r in db.execute("SELECT photo_id,cluster_id FROM photo_person_tags")]
    assert actual == 9 and tags == [(2, 9)] and not switch_pending(db)


def test_empty_analysis_face_phase_ends_pending_switch(db):
    """Normal Analyze also needs the terminal cleanup implemented by FaceWorker."""
    from bpp.db.face_name_placement import begin_switch, switch_pending
    from bpp.web.analyze_phases import run_face_phase
    from bpp.web.face_lock import refuse_while_switch_pending

    person(db, "Alice", 9, [1])
    begin_switch(db, "sface", {9: {1}})
    db.commit()
    events = []
    valid = [{"filepath": r[0], "face_count": 0} for r in db.execute("SELECT filepath FROM photos")]
    assert run_face_phase(db, valid, {}, emit=events.append, cancel_event=threading.Event()) == (
        0,
        0,
    )
    assert not switch_pending(db), "an empty completed Analyze lifts the face-switch guard"
    refuse_while_switch_pending(SimpleNamespace(get_conn=lambda: db))  # no longer raises

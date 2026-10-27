"""A person rename cannot label a stranger when a face-model switch starts
under it; a hidden photo does not hold a switch open; a name kept back by
an unreadable photo is not re-placed by the album rebuild. Inference is
replaced by explicit vectors; transport runs inline."""

from __future__ import annotations

import json
from threading import RLock
from types import SimpleNamespace

import numpy as np
import pytest
from flask import Flask

from bpp.constants import CLUSTER_UNPLACED
from bpp.db.connection import close_all_connections, init_db
from bpp.db.face_name_placement import begin_switch, finish_switch, switch_pending
from bpp.db.face_switch_record import record_coverage
from bpp.db.photos import bulk_upsert_photos


def _vec(axis: int = 0) -> bytes:
    v = np.zeros(128, dtype=np.float32)
    v[axis] = 1
    return v.tobytes()


@pytest.fixture
def alice(tmp_path):
    """Photos 1-3 with a face each; Alice (cluster 9) has solo faces on 1 and
    2 and a manual tag on 3."""
    db = init_db(str(tmp_path / "library.db"))
    bulk_upsert_photos(
        db, [{"filepath": str(tmp_path / f"{i}.jpg"), "face_count": 1} for i in range(1, 4)]
    )
    db.execute("UPDATE photos SET missing=0")
    aid = db.execute(
        "INSERT INTO albums (name, album_type, rule_json) VALUES ('Alice', 'smart_person', ?)",
        (json.dumps({"cluster_id": 9}),),
    ).lastrowid
    db.executemany(
        "INSERT INTO album_photos (album_id, photo_id) VALUES (?, ?)", [(aid, 1), (aid, 2)]
    )
    db.executemany(
        "INSERT INTO face_embeddings (photo_id, face_index, cluster_id, embedding, identity) "
        "VALUES (?, 0, 9, ?, 'Alice')",
        [(1, _vec()), (2, _vec())],
    )
    db.execute("INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (3, 9)")
    db.commit()
    yield db, aid, tmp_path
    close_all_connections()


def _cluster_of(db, aid: int) -> int:
    return db.execute("SELECT smart_person_cluster_id FROM albums WHERE id=?", (aid,)).fetchone()[0]


def test_a_name_kept_back_by_an_unreadable_photo_stays_unplaced_after_the_rebuild(alice):
    db, aid, _ = alice
    begin_switch(db, "sface", {9: {1, 2}})
    db.execute("DELETE FROM face_embeddings")
    db.execute(
        "INSERT INTO face_embeddings (photo_id, face_index, cluster_id, embedding) "
        "VALUES (1, 0, 0, ?)",
        (_vec(),),
    )
    record_coverage(db, {1, 3}, {2})  # photo 2 could not be read
    db.commit()

    finish_switch(db, {0: {1}})

    assert _cluster_of(db, aid) == CLUSTER_UNPLACED, "held until photo 2 has a face again"
    assert db.execute("SELECT COUNT(*) FROM photo_person_tags").fetchone()[0] == 0
    assert switch_pending(db) is False


def test_a_hidden_photo_does_not_hold_the_switch_open(alice, monkeypatch):
    from bpp.db.dialect import dialect
    from bpp.web import analyze_face_extract, face_phase_classes, face_worker
    from bpp.web.state_init import load_analysis_if_needed
    from tests.test_face_switch_coverage import InlineTransport

    db, _aid, tmp_path = alice
    db.execute("UPDATE photos SET hidden_at='2026-10-25' WHERE id=2")
    db.execute("UPDATE photos SET face_count=0 WHERE id=3")
    begin_switch(db, "sface", {9: {1, 2}})
    db.execute("DELETE FROM face_embeddings")
    db.executemany(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        [("face_embedding_method", "sface"), ("face_embedding_method_extracted", "sface")],
    )
    db.commit()
    ctx = SimpleNamespace(
        lock=RLock(),
        state={"analysis": None, "workdir": str(tmp_path)},
        get_conn=lambda: db,
        dirs={"thumbs": str(tmp_path / "thumbs")},
        load_clip_embeddings=lambda: None,
    )
    analysis = load_analysis_if_needed(ctx, kick_recovery=False)  # leaves hidden photos out
    face = [
        {"bbox": (0, 0, 100, 100), "embedding": np.frombuffer(_vec(), np.float32), "quality": 1}
    ]
    monkeypatch.setattr(face_worker, "_extract_one", lambda *a, **k: face)
    monkeypatch.setattr(face_phase_classes, "embedding_method", lambda conn: "sface")
    monkeypatch.setattr(analyze_face_extract, "BoundedSubprocessRunner", InlineTransport)

    candidates = [a for a in analysis if (a.get("face_count") or 0) > 0]
    analyze_face_extract.run_face_extraction_subprocess(candidates, {}, dialect.database_path(db))

    assert not switch_pending(db), "every photo a face run is given went through"


def test_a_rename_cannot_label_whoever_takes_the_cluster_when_a_switch_starts(alice, monkeypatch):
    from bpp.db.face_name_placement import _set_album_cluster
    from bpp.web import bp_albums, face_lock

    db, aid, _ = alice
    _set_album_cluster(db, [(aid, 0)])
    db.execute("UPDATE face_embeddings SET cluster_id=0")
    db.execute("UPDATE photo_person_tags SET cluster_id=0")
    db.commit()
    ctx = SimpleNamespace(get_conn=lambda: db)
    original_guard = face_lock.refuse_while_switch_pending

    def guard_then_switch(context):
        original_guard(context)  # the rename passes the guard...
        # ...and a switch starts right then; a stranger's face takes id 0.
        begin_switch(db, "sface", {0: {1, 2}})
        db.execute("DELETE FROM face_embeddings")
        db.execute(
            "INSERT INTO face_embeddings (photo_id, face_index, cluster_id, embedding) "
            "VALUES (3, 0, 0, ?)",
            (_vec(1),),
        )
        db.commit()

    monkeypatch.setattr(bp_albums, "get_ctx", lambda: ctx)
    monkeypatch.setattr(face_lock, "refuse_while_switch_pending", guard_then_switch)
    body = bp_albums.api_album_update.__wrapped__  # past owner auth
    with Flask(__name__).test_request_context(json={"name": "Alicia"}):
        body(aid)

    identity = db.execute("SELECT identity FROM face_embeddings WHERE photo_id=3").fetchone()[0]
    assert identity is None, "the stranger now on id 0 is not labelled Alicia"


def test_a_held_name_stays_held_through_a_retry_even_if_its_photo_is_hidden(alice):
    from bpp.db.face_switch_record import held_albums

    db, aid, _ = alice
    begin_switch(db, "sface", {9: {1, 2}})
    db.execute("DELETE FROM face_embeddings")
    db.execute(
        "INSERT INTO face_embeddings (photo_id, face_index, cluster_id, embedding) "
        "VALUES (1, 0, 0, ?)",
        (_vec(),),
    )
    record_coverage(db, {1, 3}, {2})
    db.commit()
    finish_switch(db, {0: {1}})
    assert _cluster_of(db, aid) == CLUSTER_UNPLACED and held_albums(db, [aid]) == {aid}

    db.execute("UPDATE photos SET hidden_at='2026-10-25' WHERE id=2")  # out of the way...
    begin_switch(db, "sface", {0: {1}})  # ...then Retry
    db.execute("DELETE FROM face_embeddings")
    db.execute(
        "INSERT INTO face_embeddings (photo_id, face_index, cluster_id, embedding) "
        "VALUES (1, 0, 0, ?)",
        (_vec(),),
    )
    record_coverage(db, {1, 3}, set())
    db.commit()
    finish_switch(db, {0: {1}})

    assert _cluster_of(db, aid) == CLUSTER_UNPLACED, "photo 2 still has no face: still held"


def test_an_album_config_write_reads_the_switch_keys_under_the_write_lock(alice, monkeypatch):
    from bpp.db import albums

    db, aid, _ = alice
    seen = []
    original = albums._config_json_keeping_stash

    def spy(conn, album_id, config):
        seen.append(conn.in_transaction)
        return original(conn, album_id, config)

    monkeypatch.setattr(albums, "_config_json_keeping_stash", spy)
    albums.update_album(db, aid, config={"k": 1})
    assert seen == [True], "the stash is read inside the transaction that writes the merge"


def test_a_hold_is_released_once_a_later_run_reads_the_photo_even_with_no_face(alice):
    from bpp.db.face_switch_record import held_albums

    db, aid, _ = alice
    begin_switch(db, "sface", {9: {1, 2}})
    db.execute("DELETE FROM face_embeddings")
    record_coverage(db, {1, 3}, {2})
    db.commit()
    finish_switch(db, {})
    assert held_albums(db, [aid]) == {aid}

    begin_switch(db, "sface", {})  # Retry: photo 2 now reads fine, no face in it
    record_coverage(db, {1, 2, 3}, set())
    db.commit()
    assert held_albums(db, [aid]) == set(), "read and processed: it holds no one any more"


def test_an_album_update_that_fails_leaves_no_write_lock(alice):
    from bpp.db import albums

    db, aid, _ = alice
    with pytest.raises(TypeError):
        albums.update_album(db, aid, config={"bad": object()})
    assert not db.in_transaction


def test_a_skipped_move_of_an_unplaced_person_is_logged_by_name(alice, caplog):
    from bpp.db.face_name_placement import _set_album_cluster, move_named_albums

    db, _aid, _ = alice
    ids = []
    for name in ("Gus", "Hana"):  # both kept aside: they share CLUSTER_UNPLACED
        ids.append(
            db.execute(
                "INSERT INTO albums (name, album_type, rule_json) VALUES (?, 'smart_person', ?)",
                (name, json.dumps({"cluster_id": 100 + len(ids)})),
            ).lastrowid
        )
    _set_album_cluster(db, [(i, CLUSTER_UNPLACED) for i in ids])

    with caplog.at_level("WARNING"):
        move_named_albums(db, [(ids[0], 9)], displace_named=False)  # Alice holds 9

    assert "Not moving 'Gus' onto cluster 9: 'Alice' is already there" in caplog.text

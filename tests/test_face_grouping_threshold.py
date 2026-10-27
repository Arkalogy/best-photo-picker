"""Face grouping: one distance per embedder, one join rule for analysis and
Regroup, and corrections that never loosen grouping or outlive the groups
they were made against."""

# Shared fixtures are imported by name and re-bound as test parameters.
# ruff: noqa: F811

from __future__ import annotations

import sqlite3

import numpy as np
import pytest

from bpp.constants import CLUSTER_UNASSIGNED
from tests._web_shared import face_web_client  # noqa: F401


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    c.execute(
        "CREATE TABLE face_cluster_feedback (id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT,"
        " cluster_id_a INTEGER, cluster_id_b INTEGER, distance REAL, created_at INTEGER)"
    )
    c.execute(
        "CREATE TABLE face_hard_negatives (cluster_id_a INTEGER, cluster_id_b INTEGER,"
        " count INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT,"
        " PRIMARY KEY (cluster_id_a, cluster_id_b))"
    )
    yield c
    c.close()


def _use(conn, method):
    """The model picked in Settings (for the next analysis)."""
    conn.execute("INSERT OR REPLACE INTO settings VALUES ('face_embedding_method', ?)", (method,))


def _made_by(conn, method):
    """The model analysis recorded for the stored faces."""
    conn.execute(
        "INSERT OR REPLACE INTO settings VALUES ('face_embedding_method_extracted', ?)",
        (method,),
    )


# ── One distance per embedder ──


def test_each_built_in_embedder_has_its_own_measured_default():
    from bpp.scoring.face_threshold import default_threshold

    values = {m: default_threshold(m) for m in ("sface", "dlib", "buffalo_s")}
    assert len(set(values.values())) == 3, values
    assert all(0.3 <= v <= 1.2 for v in values.values()), values


def test_an_unknown_embedder_gets_the_shared_fallback():
    from bpp.constants import FACE_CLUSTER_THRESHOLD_FALLBACK
    from bpp.scoring.face_threshold import default_threshold

    assert default_threshold("no_such_embedder") == FACE_CLUSTER_THRESHOLD_FALLBACK


def test_a_pick_is_kept_for_its_embedder_only(conn):
    from bpp.scoring.face_threshold import default_threshold, face_threshold, save_face_threshold

    save_face_threshold(conn, 0.55, method="sface")
    assert face_threshold(conn, "sface") == 0.55
    # Switching to another embedder never inherits the pick: its scale differs.
    assert face_threshold(conn, "buffalo_s") == default_threshold("buffalo_s")


def test_the_stored_faces_model_decides_which_value_is_read(conn):
    from bpp.scoring.face_threshold import face_threshold, save_face_threshold

    save_face_threshold(conn, 0.55, method="sface")
    save_face_threshold(conn, 0.45, method="buffalo_s")
    _made_by(conn, "sface")
    assert face_threshold(conn) == 0.55
    _made_by(conn, "buffalo_s")
    assert face_threshold(conn) == 0.45


def test_picking_a_model_changes_nothing_until_analysis_remakes_the_faces(conn):
    """The faces are still dlib's after SFace is picked in Settings: grouping
    them at SFace's distance merges different people."""
    from bpp.scoring.face_threshold import default_threshold, face_threshold, save_face_threshold

    _made_by(conn, "dlib")
    _use(conn, "sface")
    assert face_threshold(conn) == default_threshold("dlib")
    save_face_threshold(conn, 0.42)
    assert face_threshold(conn, "dlib") == 0.42
    assert face_threshold(conn, "sface") == default_threshold("sface")


@pytest.mark.parametrize("raw", ["abc", "0.1", "5", ""])
def test_a_damaged_or_out_of_range_pick_falls_back_to_the_default(conn, raw):
    from bpp.scoring.face_threshold import default_threshold, face_threshold

    conn.execute("INSERT INTO settings VALUES ('face_cluster_threshold.sface', ?)", (raw,))
    assert face_threshold(conn, "sface") == default_threshold("sface")


def _sface_faces(client):
    """The shared fixture's rows are 512-d; make them SFace-sized so Regroup
    groups them instead of setting them aside as another model's."""
    from bpp.web.state import get_ctx

    rng = np.random.default_rng(0)
    with client.application.app_context():
        conn = get_ctx().get_conn()
        _use(conn, "sface")
        _made_by(conn, "sface")
        ids = [r[0] for r in conn.execute("SELECT id FROM face_embeddings")]
        conn.executemany(
            "UPDATE face_embeddings SET embedding=? WHERE id=?",
            [(rng.normal(size=128).astype(np.float32).tobytes(), i) for i in ids],
        )
        conn.commit()


def test_regroup_saves_the_pick_for_the_stored_faces_model(face_web_client):
    from bpp.scoring.face_threshold import face_threshold
    from bpp.web.state import get_ctx

    _sface_faces(face_web_client)
    resp = face_web_client.post("/api/v1/faces/recluster", json={"threshold": 0.57})
    assert resp.status_code == 200, resp.data
    with face_web_client.application.app_context():
        assert face_threshold(get_ctx().get_conn(), "sface") == 0.57
    status = face_web_client.get("/api/v1/status").get_json()
    assert status["face_cluster_threshold"] == 0.57


def test_regroup_right_after_picking_a_model_groups_at_the_faces_model(face_web_client):
    """dlib faces, SFace picked, no analysis yet: Regroup must use dlib's
    distance and save the pick for dlib (found in review, 2026-10-26)."""
    from bpp.scoring.face_threshold import default_threshold, face_threshold
    from bpp.web.state import get_ctx

    _sface_faces(face_web_client)  # 128-d rows, valid for dlib too
    with face_web_client.application.app_context():
        conn = get_ctx().get_conn()
        _made_by(conn, "dlib")
        conn.commit()
    status = face_web_client.get("/api/v1/status").get_json()
    assert status["face_cluster_threshold"] == default_threshold("dlib")
    resp = face_web_client.post("/api/v1/faces/recluster", json={"threshold": 0.45})
    assert resp.status_code == 200, resp.data
    with face_web_client.application.app_context():
        conn = get_ctx().get_conn()
        assert face_threshold(conn, "dlib") == 0.45
        assert face_threshold(conn, "sface") == default_threshold("sface")


def _feedback(conn, rows):
    conn.executemany(
        "INSERT INTO face_cluster_feedback (action, cluster_id_a, cluster_id_b, distance)"
        " VALUES (?, 0, 1, ?)",
        rows,
    )


def test_merges_never_loosen_grouping_past_the_default(conn):
    from bpp.db.face_feedback import compute_adaptive_face_threshold

    # Twenty merges of far-apart groups: learned alone this would be 0.97.
    _feedback(conn, [("merge", 0.95)] * 20)
    threshold, _ = compute_adaptive_face_threshold(conn, default=0.7)
    assert threshold == 0.7


def test_splits_can_still_make_grouping_stricter(conn):
    from bpp.db.face_feedback import compute_adaptive_face_threshold

    _feedback(conn, [("reassign_out", 0.5)] * 20)
    threshold, _ = compute_adaptive_face_threshold(conn, default=0.7)
    assert threshold < 0.7


def test_learned_but_capped_is_not_offered_as_a_suggestion(conn):
    from bpp.db.face_feedback import compute_adaptive_face_threshold

    _feedback(conn, [("merge", 0.95)] * 5)
    _threshold, info = compute_adaptive_face_threshold(conn, default=0.7)
    assert info["source"] == "default"


def test_not_the_same_person_answers_follow_their_faces_through_regroup(conn):
    """Regroup renumbers groups; the siblings' answer must still apply."""
    from bpp.db.face_feedback import carry_hard_negatives, get_hard_negatives, store_hard_negative

    store_hard_negative(conn, 3, 7)  # Mia (faces 1, 2) is not Leo (faces 3, 4)
    store_hard_negative(conn, 3, 9)  # a group whose faces are now gone
    old = {3: {1, 2}, 7: {3, 4}, 9: {5}}
    new = {0: {3, 4}, 1: {1, 2}}  # Leo is 0 now, Mia 1
    carry_hard_negatives(conn, old, new)
    assert [(n["cluster_id_a"], n["cluster_id_b"]) for n in get_hard_negatives(conn)] == [(0, 1)]


def test_regroup_keeps_corrections_and_moves_the_pairs(face_web_client):
    from bpp.db.face_feedback import get_face_feedback, get_hard_negatives, store_hard_negative
    from bpp.web.state import get_ctx

    _sface_faces(face_web_client)
    with face_web_client.application.app_context():
        conn = get_ctx().get_conn()
        _feedback(conn, [("merge", 0.5)])
        store_hard_negative(conn, 0, 1)
        conn.commit()
    resp = face_web_client.post("/api/v1/faces/recluster", json={"threshold": 0.6})
    assert resp.status_code == 200, resp.data
    with face_web_client.application.app_context():
        conn = get_ctx().get_conn()
        assert len(get_face_feedback(conn)) == 1  # same model, distances still mean the same
        for pair in get_hard_negatives(conn):
            assert pair["cluster_id_a"] != pair["cluster_id_b"]


def test_a_rebuild_from_scratch_forgets_them_too(face_web_client):
    """Retry and a model switch both park the names with begin_switch."""
    from bpp.db.face_feedback import get_face_feedback, get_hard_negatives, store_hard_negative
    from bpp.db.face_name_placement import begin_switch
    from bpp.web.state import get_ctx

    with face_web_client.application.app_context():
        conn = get_ctx().get_conn()
        _feedback(conn, [("merge", 0.5)])
        store_hard_negative(conn, 0, 1)
        begin_switch(conn, "sface", {})
        conn.commit()
        assert get_face_feedback(conn) == []
        assert get_hard_negatives(conn) == []


# ── One join rule for analysis and Regroup ──


def _vec(x: float, y: float) -> np.ndarray:
    v = np.zeros(8, dtype=np.float32)
    v[0], v[1] = x, y
    return v


def _record(cid: int, emb: np.ndarray):
    return ("", -1, -1, emb, cid, 100, 1.0)


def test_a_face_near_a_groups_middle_but_far_from_its_faces_stays_out():
    """Two faces at x=-1 and x=+1: the middle is at 0. A new face at
    (0, 0.7) is 0.7 from the middle but 1.22 from each face; at 0.8 the
    old middle-distance rule took it in, Regroup's linkage would not."""
    from bpp.db.face_identity_remap import _assign_new_faces

    assigned = [_record(0, _vec(-1, 0)), _record(0, _vec(1, 0))]
    new = [("", 9, 0, _vec(0, 0.7), 100, None)]
    labels = _assign_new_faces(assigned, new, 0.8)
    assert labels != [0]
    assert labels[0] != CLUSTER_UNASSIGNED  # it starts a group of its own


def test_a_face_close_to_a_groups_faces_joins_it():
    from bpp.db.face_identity_remap import _assign_new_faces

    assigned = [_record(3, _vec(0, 0)), _record(3, _vec(0.1, 0))]
    new = [("", 9, 0, _vec(0.05, 0.1), 100, None)]
    assert _assign_new_faces(assigned, new, 0.8) == [3]


def test_analysis_compares_new_faces_with_each_groups_best_faces():
    """Phase 6 hands the assignment the faces themselves, not their mean."""
    from bpp.web.face_extraction_phase6 import CLUSTER_SAMPLE_FACES, _library_cluster_samples

    c = sqlite3.connect(":memory:")
    c.execute(
        "CREATE TABLE face_embeddings (id INTEGER PRIMARY KEY, cluster_id INTEGER,"
        " embedding BLOB, quality REAL)"
    )
    rows = [(cid, _vec(cid, q).tobytes(), q) for cid in (0, 1) for q in (0.1, 0.5, 0.9, 0.95)]
    c.executemany(
        "INSERT INTO face_embeddings (cluster_id, embedding, quality) VALUES (?, ?, ?)", rows
    )
    samples = _library_cluster_samples(c, 8)
    assert [s[4] for s in samples] == [0] * CLUSTER_SAMPLE_FACES + [1] * CLUSTER_SAMPLE_FACES
    # The best ones, each kept as it is.
    assert sorted(round(float(s[3][1]), 2) for s in samples if s[4] == 0) == [0.5, 0.9, 0.95]


# ── Two faces of one photo in one group ──


def _faces_db():
    c = sqlite3.connect(":memory:")
    c.execute(
        "CREATE TABLE face_embeddings (id INTEGER PRIMARY KEY, photo_id INTEGER,"
        " cluster_id INTEGER, embedding BLOB, quality REAL, user_confirmed INTEGER DEFAULT 0,"
        " bbox_x INTEGER, bbox_y INTEGER, bbox_w INTEGER, bbox_h INTEGER)"
    )
    c.execute(
        "CREATE TABLE albums (id INTEGER PRIMARY KEY, album_type TEXT,"
        " smart_person_cluster_id INTEGER)"
    )
    c.execute("CREATE TABLE photo_person_tags (photo_id INTEGER, cluster_id INTEGER)")
    return c


def _face(c, fid, photo, cid, x, emb=None, confirmed=0, box=True):
    e = (emb if emb is not None else _vec(0, 0)).tobytes()
    bx = (x, 10, 50, 50) if box else (None, None, None, None)
    c.execute(
        "INSERT INTO face_embeddings VALUES (?, ?, ?, ?, 1.0, ?, ?, ?, ?, ?)",
        (fid, photo, cid, e, confirmed, *bx),
    )


def _rows(c):
    return [tuple(r) for r in c.execute("SELECT id, cluster_id FROM face_embeddings ORDER BY id")]


def test_two_people_grouped_together_in_one_photo_are_parted_not_deleted():
    """Mother and baby side by side, grouped as one person by mistake: the
    baby's face must survive, in a group of its own."""
    from bpp.db.face_same_photo import part_same_photo_faces

    c = _faces_db()
    _face(c, 1, 1, 4, 10)
    _face(c, 2, 1, 4, 300)  # far apart in the photo
    part_same_photo_faces(c, [1])
    rows = _rows(c)
    assert [r[0] for r in rows] == [1, 2]
    assert rows[0][1] == 4
    assert rows[1][1] not in (4, CLUSTER_UNASSIGNED)


def test_the_face_that_belongs_keeps_the_group():
    """Leo's group holds Leo from other photos; his sibling, with the older
    row, was wrongly added from a shared photo. Leo keeps the group."""
    from bpp.db.face_same_photo import part_same_photo_faces

    c = _faces_db()
    _face(c, 10, 5, 4, 0, emb=_vec(1, 0))  # Leo, another photo
    _face(c, 11, 6, 4, 0, emb=_vec(1, 0.1))  # Leo, another photo
    _face(c, 1, 1, 4, 10, emb=_vec(-1, 0))  # the sibling, older row
    _face(c, 2, 1, 4, 300, emb=_vec(1, 0.05))  # Leo
    part_same_photo_faces(c, [1])
    rows = dict(_rows(c))
    assert rows[2] == 4
    assert rows[1] != 4


def test_a_face_placed_by_hand_keeps_its_group():
    from bpp.db.face_same_photo import part_same_photo_faces

    c = _faces_db()
    _face(c, 10, 5, 4, 0, emb=_vec(1, 0))
    _face(c, 1, 1, 4, 10, emb=_vec(1, 0))
    _face(c, 2, 1, 4, 300, emb=_vec(-1, 0), confirmed=1)
    part_same_photo_faces(c, [1])
    assert dict(_rows(c))[2] == 4


def test_one_face_found_twice_keeps_one_row():
    from bpp.db.face_same_photo import part_same_photo_faces

    c = _faces_db()
    _face(c, 1, 1, 4, 10)
    _face(c, 2, 1, 4, 14)  # the same face, boxes almost identical
    part_same_photo_faces(c, [1])
    assert _rows(c) == [(1, 4)]


def test_faces_without_boxes_are_never_deleted():
    from bpp.db.face_same_photo import part_same_photo_faces

    c = _faces_db()
    _face(c, 1, 1, 4, 0, box=False)
    _face(c, 2, 1, 4, 0, box=False)
    part_same_photo_faces(c, [1])
    assert [r[0] for r in _rows(c)] == [1, 2]


def test_only_the_photos_just_grouped_are_checked():
    from bpp.db.face_same_photo import part_same_photo_faces

    c = _faces_db()
    _face(c, 1, 1, 4, 10)
    _face(c, 2, 1, 4, 300)
    part_same_photo_faces(c, [2])  # another chunk
    assert _rows(c) == [(1, 4), (2, 4)]


def test_parted_faces_take_ids_no_person_holds():
    """A new id must not land on a tag-only or album-only person."""
    from bpp.db.face_same_photo import part_same_photo_faces

    c = _faces_db()
    c.execute("INSERT INTO albums VALUES (1, 'smart_person', 9)")
    _face(c, 1, 1, 4, 10)
    _face(c, 2, 1, 4, 300)
    part_same_photo_faces(c, [1])
    assert dict(_rows(c))[2] > 9


# ── A dlib library is pointed at a better model ──


def test_status_says_which_face_model_groups_the_faces(face_web_client):
    from bpp.scoring.face_embed import embedding_method
    from bpp.web.state import get_ctx

    with face_web_client.application.app_context():
        method = embedding_method(get_ctx().get_conn())
    status = face_web_client.get("/api/v1/status").get_json()
    assert status["face_embedding_method"] == method


def test_the_face_grouping_settings_carry_the_dlib_note():
    from pathlib import Path

    import bpp

    root = Path(bpp.__file__).parent / "web"
    page = (root / "templates" / "index.html").read_text()
    app = (root / "static" / "js" / "modules" / "app.mjs").read_text()
    assert 'id="face-dlib-note"' in page
    assert 'data-arg0="models"' in page.split('id="face-dlib-note"')[1].split("</div>")[0]
    assert 'st.face_embedding_method === "dlib"' in app


# ── A library last analysed by an older version (no record of the faces' model) ──


def _older_library(client, producer):
    """Faces made by dlib, no extracted-model record; ``producer`` tags the
    rows (None for rows from before the producer column was filled)."""
    from bpp.web.state import get_ctx

    _sface_faces(client)  # 128-d rows
    with client.application.app_context():
        conn = get_ctx().get_conn()
        conn.execute("DELETE FROM settings WHERE key='face_embedding_method_extracted'")
        _use(conn, "dlib")
        conn.execute("UPDATE face_embeddings SET producing_model_id=?", (producer,))
        conn.commit()


@pytest.mark.parametrize("producer", ["dlib_face_recognition_resnet_v1", None])
def test_an_older_library_keeps_its_faces_distance_after_a_model_pick(face_web_client, producer):
    from bpp.scoring.face_threshold import default_threshold

    _older_library(face_web_client, producer)
    resp = face_web_client.put("/api/v1/settings", json={"face_embedding_method": "sface"})
    assert resp.status_code == 200
    status = face_web_client.get("/api/v1/status").get_json()
    assert status["face_cluster_threshold"] == default_threshold("dlib")
    assert status["face_embedding_method"] == "dlib"  # the faces' model, for the note


def test_the_faces_producer_names_their_model_without_a_record(conn):
    from bpp.scoring.face_threshold import faces_method

    conn.execute("CREATE TABLE face_embeddings (id INTEGER PRIMARY KEY, producing_model_id TEXT)")
    conn.executemany(
        "INSERT INTO face_embeddings (producing_model_id) VALUES (?)",
        [("insightface_buffalo_s",)] * 3 + [(None,)],
    )
    _use(conn, "sface")
    assert faces_method(conn) == "buffalo_s"


def test_a_pick_in_a_library_without_faces_records_nothing(conn):
    from bpp.scoring.face_threshold import remember_faces_method

    conn.execute("CREATE TABLE face_embeddings (id INTEGER PRIMARY KEY)")
    remember_faces_method(conn)
    assert conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 0

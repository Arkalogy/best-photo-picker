"""Identity relabelling never decides a tie a re-clustering left open; a
re-clustering never deletes the manual tags of a person whose photos are
hidden; a face-model switch ends only when every candidate photo went
through, and a name whose photos could not be read stays unplaced.
Inference is replaced by explicit vectors; transport runs inline."""

import json
from queue import SimpleQueue

import numpy as np
import pytest

from bpp.constants import CLUSTER_UNPLACED
from bpp.db.connection import init_db
from bpp.db.face_identity_remap import _reconstruct_identities, _remap_names_and_tags
from bpp.db.face_name_placement import begin_switch, switch_pending
from bpp.db.photos import bulk_upsert_photos
from bpp.web.face_extraction_phases import PreExtractSnapshot, reconstruct_identities


@pytest.fixture
def db(tmp_path):
    conn = init_db(str(tmp_path / "probe.db"))
    bulk_upsert_photos(
        conn, [{"filepath": str(tmp_path / f"{i}.jpg"), "face_count": 1} for i in range(1, 5)]
    )
    conn.execute("UPDATE photos SET missing=0")
    conn.commit()
    return conn


def person(conn, name, cid, photos):
    aid = conn.execute(
        "INSERT INTO albums(name,album_type,rule_json) VALUES (?,'smart_person',?)",
        (name, json.dumps({"cluster_id": cid})),
    ).lastrowid
    conn.executemany(
        "INSERT INTO album_photos(album_id,photo_id) VALUES (?,?)", [(aid, p) for p in photos]
    )
    conn.commit()
    return aid


def face(conn, pid, cid, name, axis=0, fi=0):
    v = np.zeros(128, dtype=np.float32)
    v[axis] = 1
    conn.execute(
        "INSERT INTO face_embeddings"
        "(photo_id,face_index,cluster_id,embedding,identity,quality,bbox_w) "
        "VALUES (?,?,?,?,?,1,100)",
        (pid, fi, cid, v.tobytes(), name),
    )
    conn.commit()


def album(conn, aid):
    return tuple(
        conn.execute(
            "SELECT name,smart_person_cluster_id,config_json FROM albums WHERE id=?", (aid,)
        ).fetchone()
    )


class InlineTransport:
    """Replace only process/queue transport; execute real worker and reducer."""

    def __init__(self, phase, **kwargs):
        self.phase = phase

    def run(self, payload, cancel_event=None, progress_callback=None):
        from bpp.utils.subprocess_runner import SENTINEL

        queue = SimpleQueue()
        self.phase.target()(*self.phase.build_args(payload, queue, cancel_event))
        state = self.phase.initial_state()
        while True:
            msg = queue.get_nowait()
            if msg == SENTINEL:
                break
            assert msg.get("type") != "fatal_error", msg
            state, ui = self.phase.reduce(state, msg)
            if ui and progress_callback:
                progress_callback(ui)
        return state, 123


def test_identity_reconstruction_must_not_guess_a_recluster_tie(db, monkeypatch):
    from tests.test_face_identity_by_face import recluster

    aid = person(db, "Alice", 9, [1, 2])
    face(db, 1, 9, "Alice", axis=0)
    face(db, 2, 9, "Alice", axis=1)
    db.execute("INSERT INTO photo_person_tags(photo_id,cluster_id) VALUES (3,9)")
    db.commit()
    # Real recluster: orthogonal vectors split Alice equally. The name
    # and manual tag correctly become unplaced first.
    recluster(db, monkeypatch)
    before = album(db, aid)
    assert before[1] == CLUSTER_UNPLACED
    # Actual phase-7 caller used by the next extraction and recovery.
    reconstruct_identities(
        db,
        PreExtractSnapshot("sface", False, None, frozenset()),
        reconstruct_identities_fn=_reconstruct_identities,
        remap_names_and_tags_fn=_remap_names_and_tags,
    )
    after = album(db, aid)
    assert after[1] == CLUSTER_UNPLACED, "No new evidence: a tied name must remain unplaced"


def test_default_person_album_must_keep_tags_when_hidden(db, monkeypatch):
    from bpp.db.face_cluster_ids import next_cluster_id
    from tests.test_face_identity_by_face import recluster

    person(db, "Person 10", 9, [1])
    db.execute("INSERT INTO photo_person_tags(photo_id,cluster_id) VALUES (1,9)")
    db.execute("UPDATE photos SET hidden_at='2026-10-25' WHERE id=1")
    face(db, 2, 0, None)
    recluster(db, monkeypatch)  # first recluster's refresh removes the default album
    # Positive control: even without an album, the tag reserves the id.
    assert next_cluster_id(db) > 9
    recluster(db, monkeypatch)
    tags = [tuple(r) for r in db.execute("SELECT photo_id,cluster_id FROM photo_person_tags")]
    assert tags == [(1, 9)], (
        "Hiding a tagged photo then reclustering must not delete its manual tag"
    )


@pytest.mark.parametrize("failed", [True, False])
def test_a_switch_ends_when_every_photo_went_through_and_unreadable_ones_place_no_one(
    db, monkeypatch, failed
):
    from bpp.db.dialect import dialect
    from bpp.web import analyze_face_extract, face_phase_classes, face_worker

    aid = person(db, "Alice", 9, [1, 2, 3])
    begin_switch(db, "sface", {9: {1, 2, 3}})
    db.executemany(
        "INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)",
        [("face_embedding_method", "sface"), ("face_embedding_method_extracted", "sface")],
    )
    db.commit()
    analysis = [dict(r) for r in db.execute("SELECT id,filepath,face_count FROM photos")]
    # Photo 1 is a group shot (only the other person's face is detected);
    # photos 2 and 3 are Alice's and, in the failing case, cannot be read.
    v = np.zeros(128, dtype=np.float32)
    v[0] = 1

    def extract(fp, *args, **kwargs):
        if failed and fp in (analysis[1]["filepath"], analysis[2]["filepath"]):
            raise OSError("synthetic unavailable source volume")
        return [{"bbox": (0, 0, 100, 100), "embedding": v, "quality": 1}]

    monkeypatch.setattr(face_worker, "_extract_one", extract)
    monkeypatch.setattr(face_phase_classes, "embedding_method", lambda conn: "sface")
    monkeypatch.setattr(analyze_face_extract, "BoundedSubprocessRunner", InlineTransport)
    analyze_face_extract.run_face_extraction_subprocess(analysis, {}, dialect.database_path(db))

    assert not switch_pending(db), "every candidate photo was tried: the switch ends"
    if failed:
        assert album(db, aid)[1] == CLUSTER_UNPLACED, "her unreadable photos: no guess"
    else:
        assert album(db, aid)[1] >= 0, "complete evidence: she is placed"


def test_subset_analysis_must_not_finish_library_switch(db, monkeypatch):
    from bpp.db.dialect import dialect
    from bpp.web import analyze_face_extract, face_phase_classes, face_worker

    person(db, "Alice", 9, [1, 2, 3])
    begin_switch(db, "sface", {9: {1, 2, 3}})
    db.executemany(
        "INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)",
        [("face_embedding_method", "sface"), ("face_embedding_method_extracted", "sface")],
    )
    db.commit()
    # A small folder analysis completes, while the library's other face
    # photos have not been extracted at all.
    v = np.zeros(128, dtype=np.float32)
    v[0] = 1
    monkeypatch.setattr(
        face_worker,
        "_extract_one",
        lambda *a, **kw: [{"bbox": (0, 0, 100, 100), "embedding": v, "quality": 1}],
    )
    monkeypatch.setattr(face_phase_classes, "embedding_method", lambda conn: "sface")
    monkeypatch.setattr(analyze_face_extract, "BoundedSubprocessRunner", InlineTransport)
    analysis = [dict(r) for r in db.execute("SELECT id,filepath,face_count FROM photos WHERE id=1")]
    analyze_face_extract.run_face_extraction_subprocess(analysis, {}, dialect.database_path(db))
    assert switch_pending(db), "Completing one folder must not finalize a library-wide switch"

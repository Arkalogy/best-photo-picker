"""A face-model switch interrupted or restarted: Retry keeps every name's
original evidence, a resumed run clusters the faces
it extracted beyond the journaled chunk before the switch is finished, a
run with nothing to extract still finishes the switch, and re-clustering
never hands a tag-only person's tags to whoever takes their number.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
from flask import Flask

from bpp.constants import CLUSTER_UNPLACED
from bpp.db.connection import close_all_connections, init_db
from bpp.db.face_identity_remap import (
    _assign_new_faces,
    _reconstruct_identities,
    _remap_names_and_tags,
    _snapshot_cluster_photos,
)
from bpp.db.face_name_placement import begin_switch, finish_switch, switch_pending
from bpp.db.photos import bulk_upsert_photos
from bpp.db.smart_album_people import _refresh_person_albums


@pytest.fixture
def library(tmp_path):
    db = str(tmp_path / "lib.db")
    conn = init_db(db)
    bulk_upsert_photos(
        conn, [{"filepath": f"{tmp_path}/p{i}.jpg", "missing": 0} for i in range(12)]
    )
    pids = [r[0] for r in conn.execute("SELECT id FROM photos ORDER BY id")]
    yield conn, db, pids
    close_all_connections()


def _faces(conn, groups: dict[int, set[int]]) -> None:
    conn.execute("DELETE FROM face_embeddings")
    seen: dict[int, int] = {}
    rows = []
    for cid, pids in groups.items():
        for pid in sorted(pids):
            fi = seen.get(pid, 0)
            seen[pid] = fi + 1
            rows.append((pid, fi, cid, np.ones(128, dtype=np.float32).tobytes(), 0.9))
    conn.executemany(
        "INSERT INTO face_embeddings (photo_id, face_index, cluster_id, embedding, quality) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()


def _album(conn, name: str, cid: int, pids=()) -> int:
    aid = conn.execute(
        "INSERT INTO albums (name, album_type, rule_json) VALUES (?, 'smart_person', ?)",
        (name, json.dumps({"cluster_id": cid}, sort_keys=True)),
    ).lastrowid
    conn.executemany(
        "INSERT INTO album_photos (album_id, photo_id) VALUES (?, ?)", [(aid, p) for p in pids]
    )
    conn.commit()
    return aid


def _cluster_of(conn, name: str) -> int:
    return conn.execute(
        "SELECT smart_person_cluster_id FROM albums WHERE name=?", (name,)
    ).fetchone()[0]


def _tag_rows(conn) -> list[tuple[int, int]]:
    return sorted(
        tuple(r) for r in conn.execute("SELECT photo_id, cluster_id FROM photo_person_tags")
    )


class TestRetryKeepsOriginalEvidence:
    @pytest.mark.parametrize("retry", [False, True])
    def test_a_name_is_placed_from_the_complete_run_even_after_a_retry(self, library, retry):
        conn, _db, p = library
        alice, stranger = set(p[:4]), {p[0], p[4], p[5]}  # p[0] is a group photo
        old = {7: alice, 8: stranger}
        _faces(conn, old)
        _album(conn, "Alice", 7, alice)
        conn.execute("INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (?, 7)", (p[9],))
        conn.commit()
        begin_switch(conn, "sface", old)
        conn.commit()
        _faces(conn, {0: {p[0]}})  # the first chunk found only the stranger in p[0]
        _refresh_person_albums(conn)
        assert _cluster_of(conn, "Alice") < 0, "parked while the run is partial"
        if retry:  # the Retry endpoint's begin + wipe, before the run completed
            begin_switch(conn, "sface", _snapshot_cluster_photos(conn))
            conn.execute("DELETE FROM face_embeddings")
            conn.commit()

        complete = {0: stranger, 1: alice - {p[0]}}
        _faces(conn, complete)
        _refresh_person_albums(conn)
        finish_switch(conn, complete)
        _refresh_person_albums(conn)

        assert _cluster_of(conn, "Alice") == 1, "Alice is on her own photos' cluster"
        assert _tag_rows(conn) == [(p[9], 1)], "her tag followed her"


class TestReclusterTagOnlyIdentity:
    def test_a_named_person_taking_a_tag_only_persons_number_does_not_get_their_tags(self, library):
        from bpp.web.bp_faces_recluster import api_faces_recluster

        conn, _db, p = library
        # Alice: one face on cluster 1, no tags. "Person 1": a manual tag on
        # cluster 0 alone. Re-clustering numbers Alice's face 0.
        conn.execute(
            "INSERT INTO face_embeddings (photo_id, face_index, cluster_id, embedding, quality) "
            "VALUES (?, 0, 1, ?, 0.9)",
            (p[0], np.ones(128, dtype=np.float32).tobytes()),
        )
        _album(conn, "Alice", 1)
        _album(conn, "Person 1", 0)
        conn.execute("INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (?, 0)", (p[2],))
        conn.commit()
        ctx = SimpleNamespace(
            get_conn=lambda: conn, face_worker=SimpleNamespace(is_alive=False), config={}
        )
        body = api_faces_recluster.__wrapped__.__wrapped__  # past the auth and lock wrappers
        with (
            Flask(__name__).test_request_context(json={"threshold": 0.6}),
            patch("bpp.web.bp_faces_recluster.get_ctx", return_value=ctx),
        ):
            _resp, status = body()
        assert status == 200

        assert _cluster_of(conn, "Alice") == 0
        other = _cluster_of(conn, "Person 1")
        assert other not in (0, 1), "the tag-only person moved to a fresh number"
        assert _tag_rows(conn) == [(p[2], other)], "their tag went with them, not to Alice"


class TestNothingToExtract:
    def test_a_retry_with_no_face_candidates_finishes_the_switch(self, tmp_path):
        from bpp.db.photos import upsert_photo
        from bpp.web.bp_faces_extract import _wipe_faces_and_restart
        from bpp.web.face_worker import FaceWorker

        db = str(tmp_path / "lib.db")
        conn = init_db(db)
        fp = str(tmp_path / "tagged.jpg")
        upsert_photo(conn, {"filepath": fp, "face_count": 0, "missing": 0})
        pid = conn.execute("SELECT id FROM photos").fetchone()[0]
        _album(conn, "Alice", 7)
        conn.execute("INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (?, 7)", (pid,))
        conn.commit()
        analysis = [{"id": pid, "filepath": fp, "face_count": 0}]
        worker = FaceWorker()

        def run_now(analysis, db_path, config):
            worker._run(analysis, db_path, config)
            return True

        ctx = SimpleNamespace(
            get_conn=lambda: conn,
            db_path=lambda: db,
            dirs={"face_crops": str(tmp_path / "crops")},
            config={},
            face_worker=SimpleNamespace(start=run_now),
        )
        try:
            with patch("bpp.scoring.face_embed.embedding_method", return_value="sface"):
                _wipe_faces_and_restart(ctx, analysis)

            assert switch_pending(conn) is False, "the guard is lifted"
            assert _cluster_of(conn, "Alice") == CLUSTER_UNPLACED, "kept as a person without faces"
        finally:
            close_all_connections()


class TestResumeBeyondTheJournaledChunk:
    def test_faces_extracted_on_resume_are_clustered_before_the_switch_ends(self, tmp_path):
        from bpp.web import face_extraction_journal as journal
        from bpp.web import face_extraction_phases as phases
        from bpp.web.face_phase_pipeline import FacePhaseContext, run_face_pipeline
        from bpp.web.face_recovery import recover_pending_face_extractions

        db = str(tmp_path / "lib.db")
        conn = init_db(db)
        bulk_upsert_photos(
            conn,
            [{"filepath": f"{tmp_path}/p{i}.jpg", "face_count": 1, "missing": 0} for i in range(6)],
        )
        p = [r[0] for r in conn.execute("SELECT id FROM photos ORDER BY id")]
        analysis = [dict(r) for r in conn.execute("SELECT id, filepath, face_count FROM photos")]
        alice, other = set(p[:4]), {p[0], p[4], p[5]}
        _album(conn, "Alice", 7, alice)
        begin_switch(conn, "sface", {7: alice, 8: other})
        conn.commit()
        stranger_emb = np.zeros(128, dtype=np.float32)
        stranger_emb[0] = 1
        alice_emb = np.zeros(128, dtype=np.float32)
        alice_emb[1] = 1
        # The crash came after the first chunk (only p[0], the stranger) was
        # clustered, before its identity phase.
        conn.execute(
            "INSERT INTO face_embeddings (photo_id, face_index, cluster_id, embedding, bbox_w, "
            "quality) VALUES (?, 0, 0, ?, 100, 0.9)",
            (p[0], stranger_emb.tobytes()),
        )
        conn.commit()
        run_id = journal.start_run(conn)
        for bit in range(6):
            journal.mark_phase_complete(conn, run_id, bit)
        journal.store_snapshot(
            conn,
            run_id,
            phases.PreExtractSnapshot("dlib", True, {7: alice, 8: other}, frozenset()),
        )

        def infer(c, partition, snapshot, **_kw):
            records, rows = list(partition.cached_records), []
            for _i, fp, pid in partition.need_extract:
                emb = alice_emb if pid in alice - {p[0]} else stranger_emb
                rows.append((pid, emb.tobytes()))
                records.append((fp, pid, 0, emb))
            c.executemany(
                "INSERT OR REPLACE INTO face_embeddings (photo_id, face_index, cluster_id, "
                "embedding, bbox_w, quality) VALUES (?, 0, -1, ?, 100, 0.9)",
                rows,
            )
            c.commit()
            return phases.ExtractionOutput(records, False, len(rows))

        def extract(c, with_faces, photo_map, max_long_side, face_confidence, config, **kw):
            rid = kw.get("resume_run_id") or journal.start_run(c)
            pctx = FacePhaseContext(
                conn=c,
                with_faces=with_faces,
                photo_map=photo_map,
                max_long_side=max_long_side,
                face_confidence=face_confidence,
                config=config,
                run_id=rid,
                phases_done=journal.get_phases_complete(c, rid),
                extract_one_fn=lambda: None,
                validate_bbox_fn=lambda: None,
                validate_embedding_fn=lambda: None,
                assign_new_faces_fn=_assign_new_faces,
                reconstruct_identities_fn=_reconstruct_identities,
                remap_names_and_tags_fn=_remap_names_and_tags,
            )
            run_face_pipeline(pctx)
            return pctx.faces_found, pctx.n_clusters

        ctx = SimpleNamespace(
            load_analysis_if_needed=lambda: analysis,
            config={},
            invalidate_face_cluster_map=lambda: None,
        )
        try:
            with (
                patch("bpp.web.face_worker.extract_and_cluster_faces", side_effect=extract),
                patch(
                    "bpp.web.face_phase_classes.MethodReconcilerPhase._resolve",
                    return_value="sface",
                ),
                patch("bpp.web.face_phase_classes._refuse_switch_to_unrunnable", return_value=None),
                patch.object(phases, "extract_new_embeddings", side_effect=infer),
            ):
                assert recover_pending_face_extractions(conn, ctx=ctx) == 1

            unclustered = conn.execute(
                "SELECT COUNT(*) FROM face_embeddings WHERE cluster_id < 0"
            ).fetchone()[0]
            assert unclustered == 0, "every recovered face was clustered"
            alice_faces = conn.execute(
                "SELECT cluster_id FROM face_embeddings WHERE photo_id=?", (p[1],)
            ).fetchone()[0]
            assert _cluster_of(conn, "Alice") == alice_faces, "Alice is on her faces' cluster"
            assert switch_pending(conn) is False
        finally:
            close_all_connections()

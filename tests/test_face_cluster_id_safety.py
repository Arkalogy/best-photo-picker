"""Cluster ids never land on a person known only by manual tags: a
re-clustering moves such a person off an id that now holds other faces
(with or without an album), and a new cluster from split or create-person
is numbered above them. A completed identity phase is never replayed.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pytest

from bpp.db.connection import close_all_connections, init_db
from bpp.db.face_cluster_ids import next_cluster_id
from bpp.db.face_identity_remap import _remap_names_and_tags
from bpp.db.photos import bulk_upsert_photos


@pytest.fixture
def conn(tmp_path):
    c = init_db(str(tmp_path / "lib.db"))
    bulk_upsert_photos(c, [{"filepath": f"{tmp_path}/p{i}.jpg", "missing": 0} for i in range(6)])
    yield c
    close_all_connections()


def _pids(conn) -> list[int]:
    return [r[0] for r in conn.execute("SELECT id FROM photos ORDER BY id")]


def _face(conn, pid: int, cid: int) -> None:
    conn.execute(
        "INSERT INTO face_embeddings (photo_id, face_index, cluster_id, embedding, quality) "
        "VALUES (?, 0, ?, ?, 0.9)",
        (pid, cid, np.ones(128, dtype=np.float32).tobytes()),
    )


def _tags(conn) -> list[tuple[int, int]]:
    return sorted(
        tuple(r) for r in conn.execute("SELECT photo_id, cluster_id FROM photo_person_tags")
    )


class TestTagOnlyPersonOnARenumberedId:
    def test_a_new_cluster_on_their_id_does_not_take_their_tags(self, conn):
        p = _pids(conn)
        conn.execute(
            "INSERT INTO albums (name, album_type, rule_json) "
            "VALUES ('Person 4', 'smart_person', ?)",
            (json.dumps({"cluster_id": 3}),),
        )
        conn.execute("INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (?, 3)", (p[5],))
        _face(conn, p[0], 3)  # a brand-new cluster 3 of a stranger's faces
        conn.commit()

        _remap_names_and_tags(conn, {}, {3: {p[0]}})

        rows = _tags(conn)
        assert len(rows) == 1 and rows[0][0] == p[5] and rows[0][1] not in (3,)
        album = conn.execute("SELECT smart_person_cluster_id FROM albums").fetchone()[0]
        assert album == rows[0][1], "the album went with the tags"

    def test_one_without_an_album_gets_a_default_one_so_the_tags_survive(self, conn):
        p = _pids(conn)
        conn.execute("INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (?, 3)", (p[5],))
        _face(conn, p[0], 3)
        conn.commit()

        _remap_names_and_tags(conn, {}, {3: {p[0]}})
        # The recluster endpoint's orphan sweep keeps tags with an album.
        conn.execute(
            "DELETE FROM photo_person_tags WHERE cluster_id NOT IN "
            "(SELECT DISTINCT cluster_id FROM face_embeddings WHERE cluster_id >= 0) "
            "AND cluster_id NOT IN (SELECT smart_person_cluster_id FROM albums "
            " WHERE album_type='smart_person' AND smart_person_cluster_id >= 0)"
        )

        rows = _tags(conn)
        assert len(rows) == 1 and rows[0][1] != 3, "kept, off the stranger's id"
        name = conn.execute(
            "SELECT name FROM albums WHERE smart_person_cluster_id=?", (rows[0][1],)
        ).fetchone()
        assert name is not None and name[0].startswith("Person ")


class TestNewIdsAboveTagOnlyPeople:
    def test_next_cluster_id_counts_tags_and_albums(self, conn):
        p = _pids(conn)
        _face(conn, p[0], 2)
        conn.execute("INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (?, 6)", (p[1],))
        conn.execute(
            "INSERT INTO albums (name, album_type, rule_json) "
            "VALUES ('Grandma', 'smart_person', ?)",
            (json.dumps({"cluster_id": 9}),),
        )
        conn.commit()
        assert next_cluster_id(conn) == 10

    def test_split_and_create_person_use_the_shared_allocator(self):
        import inspect

        from bpp.web import bp_faces_cluster_ops, face_create_helpers

        for mod in (bp_faces_cluster_ops, face_create_helpers):
            src = inspect.getsource(mod)
            assert "next_cluster_id(conn)" in src, mod.__name__
            assert "MAX(cluster_id)" not in src, f"{mod.__name__} allocates past tag-only ids"


class TestIdentityReplayOnResume:
    def test_a_completed_identity_phase_is_never_replayed(self, conn):
        """Outside a switch it would remap names by the run's old snapshot a
        second time; during one, names are placed only when the run ends."""
        from bpp.db.face_name_placement import begin_switch
        from bpp.web.face_phase_classes import IdentityReconstructPhase

        ctx = SimpleNamespace(conn=conn, journal_complete=lambda _bit: True)
        assert IdentityReconstructPhase().should_skip(ctx)
        begin_switch(conn, "sface", {})
        conn.commit()
        assert IdentityReconstructPhase().should_skip(ctx)


class TestZeroCandidateRetryClosesItsJournal:
    def test_the_retry_row_is_closed(self, tmp_path):
        from bpp.db.journal import journal_start, pending_journals
        from bpp.db.photos import upsert_photo
        from bpp.web.face_worker import FaceWorker

        db = str(tmp_path / "z.db")
        c = init_db(db)
        fp = str(tmp_path / "x.jpg")
        upsert_photo(c, {"filepath": fp, "face_count": 0, "missing": 0})
        pid = c.execute("SELECT id FROM photos").fetchone()[0]
        journal_start(c, "face_extraction_retry", {})
        c.commit()
        try:
            FaceWorker()._run([{"id": pid, "filepath": fp, "face_count": 0}], db, {})
            assert pending_journals(c, kind="face_extraction_retry") == []
        finally:
            close_all_connections()

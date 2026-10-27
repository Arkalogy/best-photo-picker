"""Retry, tags and stray albums in name placement: carried names next to
current ones, identity reconstruction restoring tags, adopted stray parked
albums, restored tags joining the album, Retry parking names, tagged
default clusters kept, ids held by tags or albums reserved.

Shared fixtures are imported by name and re-bound as test parameters.
"""

# ruff: noqa: F811

from __future__ import annotations

import logging

from bpp.constants import CLUSTER_UNPLACED
from bpp.db.face_name_placement import (
    begin_switch,
    finish_switch,
    load_pending,
    switch_pending,
)
from tests._face_name_preservation_shared import _create_named_person_album
from tests.test_face_name_switch import (  # noqa: F401
    _album_ids,
    _placed,
    _set_clusters,
    _tags,
    people,
)


class TestCarriedAndStrayNames:
    def test_a_carried_and_a_current_name_can_tie(self, people):
        """Typed string keys sort next to int keys without a crash."""
        from bpp.db.face_name_matching import _match_named_clusters

        remap: dict = {}
        _match_named_clusters(
            {3: "Alice", "album:9": "Bob"},
            {3: {1, 2, 3, 4, 5}, "album:9": {6, 7, 8, 9, 10}},
            {0: {1, 2, 3, 4, 5}, 1: {6, 7, 8, 9, 10}},
            remap,
            set(),
        )
        assert remap == {3: 0, "album:9": 1}

    def test_identity_reconstruction_restores_an_unplaced_persons_tags(self, people):
        from bpp.db.face_identity_remap import _reconstruct_identities
        from bpp.db.face_switch_record import stash_unplaced_tags

        conn, _old, p = people
        pids = p["pids"]
        aid = _album_ids(conn)["Alice"]
        conn.execute(
            "UPDATE albums SET rule_json=?, smart_person_cluster_id=? WHERE id=?",
            (f'{{"album": {aid}, "cluster_id": -3}}', CLUSTER_UNPLACED, aid),
        )
        stash_unplaced_tags(conn, {aid: {pids[18]}})
        conn.commit()
        _set_clusters(conn, {5: p["alice"], 0: p["bob"]})
        conn.executemany(
            "UPDATE face_embeddings SET identity='Alice' WHERE photo_id=?",
            [(x,) for x in p["alice"]],
        )
        conn.commit()

        _reconstruct_identities(conn)

        assert _placed(conn)[5] == "Alice"
        assert _tags(conn) == {5: {pids[18]}}

    def test_a_parked_album_with_no_record_is_adopted(self, people):
        """A record lost (malformed, or finished without one) must not leave a
        parked name invisible for good: the next switch carries it, and a
        refresh places it when no switch is pending."""
        from bpp.db.face_name_placement import park_id
        from bpp.db.smart_albums import refresh_smart_albums

        conn, _old, p = people
        aid = _album_ids(conn)["Alice"]
        conn.execute(
            "UPDATE albums SET rule_json=?, smart_person_cluster_id=? WHERE id=?",
            (f'{{"cluster_id": {park_id(aid)}}}', park_id(aid), aid),
        )
        conn.execute("DELETE FROM settings WHERE key='face_switch_pending'")
        conn.commit()
        _set_clusters(conn, {4: p["alice"], 0: p["bob"]})

        refresh_smart_albums(conn)
        assert _placed(conn).get(4) == "Alice", "no switch pending: the refresh owns it"

    def test_restored_tags_join_the_placed_album_at_once(self, people):
        from bpp.db.smart_albums import refresh_smart_albums

        conn, old, p = people
        pids = p["pids"]
        conn.execute(
            "INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (?, 3)", (pids[18],)
        )
        conn.commit()
        begin_switch(conn, "sface", old)
        conn.commit()
        _set_clusters(conn, {0: p["bob"]})
        finish_switch(conn, {0: p["bob"]})  # Alice unplaced, her tag stashed
        _set_clusters(conn, {0: p["bob"], 4: p["alice"]})
        refresh_smart_albums(conn)
        aid = _album_ids(conn)["Alice"]
        in_album = {
            r[0] for r in conn.execute("SELECT photo_id FROM album_photos WHERE album_id=?", (aid,))
        }
        assert pids[18] in in_album, "the tagged photo is in her album right away"

    def test_two_imports_into_one_batch_keep_it_marked(self, tmp_path):
        from bpp.db.library_copy import active_import, remove_stale_import_tmp

        batch = tmp_path / "photos" / "trip"
        batch.mkdir(parents=True)
        (batch / "x.jpg.tmp").write_bytes(b"half")
        with active_import(str(batch)):
            with active_import(str(batch)):
                pass  # the first import finished
            assert remove_stale_import_tmp(str(tmp_path / "photos"), older_than_s=0) == 0
        assert remove_stale_import_tmp(str(tmp_path / "photos"), older_than_s=0) == 1


class TestRetryAndTags:
    def test_placement_logs_cleanly_with_a_carried_name(self, people, caplog):
        """The app's log handler formats every record; a carried name's
        string key must not break the matcher's INFO line."""
        from bpp.db.face_name_matching import _match_named_clusters

        remap: dict = {}
        with caplog.at_level(logging.INFO):
            _match_named_clusters(
                {"album:1": "Alice"}, {"album:1": {1, 2}}, {4: {1, 2}}, remap, set()
            )
            for record in caplog.records:
                record.getMessage()  # raises if the format does not fit its args
        assert remap == {"album:1": 4}

    def test_a_deleted_photos_tag_does_not_block_the_others(self, people):
        from bpp.db.face_switch_record import restore_unplaced_tags, stash_unplaced_tags

        conn, _old, p = people
        pids = p["pids"]
        aid = _album_ids(conn)["Alice"]
        stash_unplaced_tags(conn, {aid: {pids[18], pids[19]}})
        conn.execute("DELETE FROM photos WHERE id=?", (pids[19],))  # permanently deleted meanwhile
        conn.commit()
        assert restore_unplaced_tags(conn, {aid: 4}) == 1
        assert _tags(conn) == {4: {pids[18]}}

    def test_retry_parks_names_and_tags_instead_of_deleting_them(self, people, monkeypatch):
        import tempfile
        from types import SimpleNamespace

        from bpp.scoring import face_embed
        from bpp.web import bp_faces_extract

        conn, _old, p = people
        pids = p["pids"]
        conn.execute(
            "INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (?, 3)", (pids[18],)
        )
        conn.commit()

        ctx = SimpleNamespace(
            dirs={"face_crops": tempfile.mkdtemp()},
            face_worker=SimpleNamespace(start=lambda *_a, **_k: True),
            config={},
            get_conn=lambda: conn,
            db_path=lambda: ":memory:",
        )
        monkeypatch.setattr(face_embed, "embedding_method", lambda _c=None: "sface")
        bp_faces_extract._wipe_faces_and_restart(ctx, [{"filepath": "x"}])

        assert conn.execute("SELECT COUNT(*) FROM face_embeddings").fetchone()[0] == 0
        assert _tags(conn) == {}, "no tag row is left to become a ghost album"
        assert switch_pending(conn), "the names are parked, the extraction will place them"
        pending = load_pending(conn)
        assert sorted(pending.names.values()) == ["Alice", "Bob"]
        assert pending.tags == {3: {pids[18]}}

    def test_a_tagged_default_cluster_is_kept_as_a_person(self, people):
        conn, old, p = people
        pids = p["pids"]
        faces = set(pids[12:14])
        _set_clusters(conn, {**old, 7: faces})
        _create_named_person_album(conn, "Person 8", 7, sorted(faces))
        conn.execute(
            "INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (?, 7)", (pids[15],)
        )
        conn.commit()
        begin_switch(conn, "sface", {**old, 7: faces})
        conn.commit()
        new = {0: p["alice"], 1: p["bob"], 2: set(pids[16:18])}  # Person 8 matches nothing
        _set_clusters(conn, new)
        finish_switch(conn, new)
        from bpp.db.face_switch_record import pop_unplaced_tags

        aid = _album_ids(conn).get("Person 8")
        assert aid is not None, "a tagged default person is parked, not deleted"
        assert pop_unplaced_tags(conn, [aid]) == {aid: {pids[15]}}


def test_an_id_held_only_by_a_tag_only_person_is_not_reused(tmp_path):
    """Grandma exists by an album and a manual tag at cluster 0, with no
    face rows; a new face must not be numbered 0."""
    import sqlite3

    import numpy as np

    from bpp.db.face_identity_remap import _assign_new_faces
    from bpp.web.face_extraction_phase6 import cluster_faces
    from tests.test_face_chunk_clustering import _schema

    conn = sqlite3.connect(":memory:")
    _schema(conn)
    conn.execute(
        "CREATE TABLE albums (id INTEGER PRIMARY KEY, name TEXT, album_type TEXT,"
        " smart_person_cluster_id INTEGER)"
    )
    conn.execute("CREATE TABLE photo_person_tags (photo_id INTEGER, cluster_id INTEGER)")
    conn.execute(
        "INSERT INTO albums (name, album_type, smart_person_cluster_id)"
        " VALUES ('Grandma', 'smart_person', 0)"
    )
    conn.execute("INSERT INTO photo_person_tags VALUES (2, 0)")
    v = np.zeros(128, dtype=np.float32)
    v[0] = 1.0
    conn.execute(
        "INSERT INTO face_embeddings (photo_id, face_index, bbox_w, embedding, quality, cluster_id)"
        " VALUES (3, 0, 100, ?, 1.0, -1)",
        (v.tobytes(),),
    )
    conn.commit()
    cluster_faces(
        conn,
        [("/p3.jpg", 3, 0, v)],
        {"face_cluster_threshold": 0.6},
        assign_new_faces_fn=_assign_new_faces,
        post_cluster_dedup=False,
    )
    assert (
        conn.execute("SELECT cluster_id FROM face_embeddings WHERE photo_id=3").fetchone()[0] == 1
    )

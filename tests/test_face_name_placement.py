"""Person names survive cluster-id changes: swapped ids on recluster, and a
face-model switch that runs in chunks.

Before 0.2.2 the remap moved albums one pair at a time while the new ids
reused the old numbers, so names were deleted or landed on another person;
a chunked model switch kept the old cluster map only in the first chunk's
memory, so people outside that chunk lost their names.
"""

from __future__ import annotations

import logging
import sqlite3

import pytest

from bpp.db.face_identity_remap import _remap_names_and_tags
from bpp.db.face_name_placement import (
    begin_switch,
    finish_switch,
    load_pending,
    switch_pending,
)
from tests._face_name_preservation_shared import (
    _create_named_person_album,
    _get_person_album_names,
    _get_photo_ids,
    _insert_embeddings,
    _make_db,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _album_ids(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT name, id FROM albums WHERE album_type='smart_person'").fetchall()
    return {name: aid for name, aid in rows}


def _placed_names(conn: sqlite3.Connection) -> dict[int, str]:
    """Names on real clusters; a parked album (negative id) is not placed."""
    return {cid: n for cid, n in _get_person_album_names(conn).items() if cid >= 0}


def _tags(conn: sqlite3.Connection) -> dict[int, set[int]]:
    out: dict[int, set[int]] = {}
    for pid, cid in conn.execute("SELECT photo_id, cluster_id FROM photo_person_tags"):
        out.setdefault(cid, set()).add(pid)
    return out


def _add_tags(conn: sqlite3.Connection, cid: int, pids: set[int]) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO photo_person_tags (photo_id, cluster_id) VALUES (?, ?)",
        [(pid, cid) for pid in sorted(pids)],
    )
    conn.commit()


def _set_clusters(conn: sqlite3.Connection, clusters: dict[int, set[int]]) -> None:
    """Make face_embeddings reflect ``clusters`` (what a recluster writes)."""
    conn.execute("DELETE FROM face_embeddings")
    conn.commit()
    _insert_embeddings(conn, {cid: sorted(pids) for cid, pids in clusters.items()})


@pytest.fixture
def people(tmp_path):
    """A DB with Leo (cluster 3) and Mia (cluster 0) named, plus a stranger."""
    conn, _ = _make_db(tmp_path, n_photos=20)
    pids = _get_photo_ids(conn)
    leo, mia, stranger = set(pids[0:5]), set(pids[5:10]), set(pids[10:15])
    old = {3: leo, 0: mia, 5: stranger}
    _set_clusters(conn, old)
    _create_named_person_album(conn, "Leo", 3, sorted(leo))
    _create_named_person_album(conn, "Mia", 0, sorted(mia))
    return conn, old, {"leo": leo, "mia": mia, "stranger": stranger}


# ---------------------------------------------------------------------------
# Recluster: the ids change under the names
# ---------------------------------------------------------------------------


class TestRemapWithOverlappingIds:
    def test_swapped_cluster_ids_keep_both_names(self, people):
        conn, old, p = people
        ids_before = _album_ids(conn)
        new = {0: p["leo"], 3: p["mia"], 5: p["stranger"]}
        _set_clusters(conn, new)

        _remap_names_and_tags(conn, old, new)

        assert _get_person_album_names(conn) == {0: "Leo", 3: "Mia"}
        assert _album_ids(conn) == ids_before, "the albums move; they are not replaced"

    def test_name_moving_onto_another_names_old_id(self, people):
        conn, old, p = people
        new = {5: p["leo"], 3: p["mia"], 8: p["stranger"]}
        _set_clusters(conn, new)

        _remap_names_and_tags(conn, old, new)

        assert _get_person_album_names(conn) == {5: "Leo", 3: "Mia"}

    def test_tags_follow_swapped_ids(self, people):
        conn, old, p = people
        _add_tags(conn, 3, p["leo"])
        _add_tags(conn, 0, p["mia"])
        new = {0: p["leo"], 3: p["mia"], 5: p["stranger"]}
        _set_clusters(conn, new)

        _remap_names_and_tags(conn, old, new)

        assert _tags(conn) == {0: p["leo"], 3: p["mia"]}

    def test_unmatched_name_does_not_land_on_a_stranger(self, people, caplog):
        from bpp.constants import CLUSTER_UNPLACED

        conn, old, p = people
        # Leo's faces all vanished (dismissed); new cluster 3 is the stranger.
        new = {3: p["stranger"], 7: p["mia"]}
        _set_clusters(conn, new)

        with caplog.at_level(logging.WARNING):
            _remap_names_and_tags(conn, old, new)

        names = _get_person_album_names(conn)
        assert names.get(3) != "Leo", f"Leo's name landed on the stranger: {names}"
        assert names[7] == "Mia"
        assert names[CLUSTER_UNPLACED] == "Leo", "kept as a person without faces, not deleted"
        assert "Could not place the name 'Leo'" in caplog.text

    def test_default_album_in_the_way_is_replaced(self, people):
        conn, old, p = people
        _create_named_person_album(conn, "Person 5", 5, sorted(p["stranger"]))
        leo_album = _album_ids(conn)["Leo"]
        new = {5: p["leo"], 0: p["mia"], 8: p["stranger"]}
        _set_clusters(conn, new)

        _remap_names_and_tags(conn, old, new)

        assert _get_person_album_names(conn) == {5: "Leo", 0: "Mia"}
        assert _album_ids(conn)["Leo"] == leo_album
        assert "Person 5" not in _album_ids(conn)


# ---------------------------------------------------------------------------
# Model switch: the wipe parks the names, each chunk places what it can
# ---------------------------------------------------------------------------


class TestSwitchPlacement:
    def test_begin_switch_parks_names_without_committing(self, people):
        conn, old, p = people
        _add_tags(conn, 3, p["leo"])

        begin_switch(conn, "sface", old)

        assert conn.in_transaction, "the caller's wipe transaction commits both"
        conn.commit()
        pending = load_pending(conn)
        assert pending is not None
        assert pending.to_method == "sface"
        assert pending.names == {3: "Leo", 0: "Mia"}
        assert pending.tags == {3: p["leo"]}
        assert pending.old_cluster_photos == old
        assert switch_pending(conn) is True
        # Nothing points at a cluster number until a chunk places it.
        assert _placed_names(conn) == {}
        assert _tags(conn) == {}
        assert set(_album_ids(conn)) == {"Leo", "Mia"}

    def test_names_placed_across_chunks(self, people):
        conn, old, p = people
        _add_tags(conn, 3, p["leo"])
        begin_switch(conn, "sface", old)
        conn.commit()
        ids_before = _album_ids(conn)

        chunk1 = {0: p["leo"]}
        _set_clusters(conn, chunk1)
        assert _placed_names(conn) == {} and _tags(conn) == {}, "parked until the run finishes"
        assert switch_pending(conn) is True

        chunk2 = {0: p["leo"], 1: p["mia"], 2: p["stranger"]}
        _set_clusters(conn, chunk2)
        finish_switch(conn, chunk2)
        assert {n: a for n, a in _album_ids(conn).items() if n in ids_before} == ids_before
        assert _tags(conn) == {0: p["leo"]}
        names = _get_person_album_names(conn)
        assert {c: n for c, n in names.items() if not n.startswith("Person ")} == {
            0: "Leo",
            1: "Mia",
        }
        assert names.get(2, "").startswith("Person "), "the stranger got a default album"
        assert switch_pending(conn) is False

    def test_each_name_takes_its_strongest_cluster_of_the_complete_run(self, people):
        conn, old, p = people
        begin_switch(conn, "sface", old)
        conn.commit()
        # Leo's photos split; most of them sit with all of Mia's.
        leo = sorted(p["leo"])
        complete = {0: set(leo[:2]), 1: set(leo[2:]) | p["mia"]}
        _set_clusters(conn, complete)
        finish_switch(conn, complete)
        assert _placed_names(conn) == {1: "Mia", 0: "Leo"}, "Mia's five beat Leo's three"

    def test_default_album_created_mid_run_yields_to_the_name(self, people):
        conn, old, p = people
        begin_switch(conn, "sface", old)
        conn.commit()
        # refresh_smart_albums ran after chunk 1 and made "Person 1" for a
        # cluster that chunk 2 turns out to be Mia.
        chunk2 = {0: p["leo"], 1: p["mia"]}
        _set_clusters(conn, chunk2)
        _create_named_person_album(conn, "Person 1", 1, sorted(p["mia"]))
        finish_switch(conn, chunk2)
        assert _placed_names(conn) == {0: "Leo", 1: "Mia"}
        assert "Person 1" not in _album_ids(conn)

    def test_finish_keeps_names_that_never_found_a_cluster(self, people, caplog):
        from bpp.constants import CLUSTER_UNPLACED

        conn, old, p = people
        begin_switch(conn, "sface", old)
        conn.commit()
        chunk = {0: p["leo"]}
        _set_clusters(conn, chunk)
        with caplog.at_level(logging.WARNING):
            unplaced = finish_switch(conn, chunk)

        assert unplaced == 1
        assert _placed_names(conn) == {0: "Leo"}
        assert _get_person_album_names(conn)[CLUSTER_UNPLACED] == "Mia", "kept, not deleted"
        assert "Mia" in caplog.text
        assert switch_pending(conn) is False

    def test_a_second_switch_carries_the_unplaced_names(self, people):
        conn, old, _ = people
        begin_switch(conn, "sface", old)
        conn.commit()
        # The user switched again before the run placed anything.
        begin_switch(conn, "buffalo_s", {})
        conn.commit()
        pending = load_pending(conn)
        assert pending is not None and pending.to_method == "buffalo_s"
        assert sorted(pending.names.values()) == ["Leo", "Mia"], "nothing is lost"


class TestReconcileWritesThePending:
    def test_method_change_parks_names_in_the_wipe(self, people):
        from bpp.web.face_method_reconcile import reconcile_method

        conn, old, _ = people
        conn.execute("INSERT INTO settings (key, value) VALUES ('face_embedding_method', 'dlib')")
        conn.commit()

        stored, changed, _snapshot = reconcile_method(conn, "sface")

        assert (stored, changed) == ("dlib", True)
        assert conn.execute("SELECT COUNT(*) FROM face_embeddings").fetchone()[0] == 0
        pending = load_pending(conn)
        assert pending is not None
        assert pending.names == {3: "Leo", 0: "Mia"}
        assert pending.old_cluster_photos == old
        assert _placed_names(conn) == {}, "nothing points at the wiped ids"


# ---------------------------------------------------------------------------
# The refresh, dead albums, identity labels, tag-only people
# ---------------------------------------------------------------------------


class TestPlacementSurvivesTheRefresh:
    def test_refresh_between_chunks_leaves_parked_names_alone(self, people):
        """refresh_smart_albums runs after every chunk; a parked name must
        not be treated as an orphan and renamed onto whoever overlaps."""
        from bpp.db.smart_albums import refresh_smart_albums

        conn, old, p = people
        begin_switch(conn, "sface", old)
        conn.commit()
        # Chunk 1: Leo's cluster, which also holds one of Mia's group photos.
        shared = sorted(p["mia"])[0]
        chunk1 = {0: p["leo"] | {shared}}
        _set_clusters(conn, chunk1)
        refresh_smart_albums(conn)

        named = {c: n for c, n in _placed_names(conn).items() if not n.startswith("Person ")}
        assert named == {}, "no name is placed mid-run (default albums only)"
        assert {"Leo", "Mia"} <= set(_album_ids(conn)), "parked albums survive the refresh"
        chunk2 = {0: p["leo"] | {shared}, 1: p["mia"] - {shared}}
        _set_clusters(conn, chunk2)
        finish_switch(conn, chunk2)
        assert _placed_names(conn) == {0: "Leo", 1: "Mia"}

    def test_a_parked_album_the_user_deleted_displaces_nothing(self, people):
        conn, old, p = people
        begin_switch(conn, "sface", old)
        conn.commit()
        leo_album = _album_ids(conn)["Leo"]
        conn.execute("DELETE FROM album_photos WHERE album_id=?", (leo_album,))
        conn.execute("DELETE FROM albums WHERE id=?", (leo_album,))
        conn.commit()
        chunk = {4: p["leo"], 5: p["mia"]}
        _set_clusters(conn, chunk)
        _create_named_person_album(conn, "Person 5", 4, sorted(p["leo"]))
        finish_switch(conn, chunk)

        names = _get_person_album_names(conn)
        assert names[5] == "Mia" and "Leo" not in names.values()
        assert names[4].startswith("Person "), "cluster 4 keeps a default name"


class TestRelocateEdgeCases:
    def test_displaced_named_album_is_kept_unplaced_and_logged(self, people, caplog):
        from bpp.constants import CLUSTER_UNPLACED

        conn, old, p = people
        # Leo 3→0 lands on Mia's old id; Mia's faces vanished.
        new = {0: p["leo"], 5: p["stranger"]}
        _set_clusters(conn, new)
        with caplog.at_level(logging.WARNING):
            _remap_names_and_tags(conn, old, new)
        assert _get_person_album_names(conn) == {0: "Leo", CLUSTER_UNPLACED: "Mia"}
        assert "Could not place the name 'Mia'" in caplog.text

    def test_a_tag_only_person_whose_id_is_reused_is_kept_unplaced(self, people):
        from bpp.constants import CLUSTER_UNPLACED
        from bpp.db.face_switch_record import pop_unplaced_tags

        conn, old, p = people
        pids = _get_photo_ids(conn)
        grandma = set(pids[15:18])
        _create_named_person_album(conn, "Grandma", 12, sorted(grandma))
        _add_tags(conn, 12, grandma)
        _add_tags(conn, 3, p["leo"])
        # Leo's faces vanished; cluster 3 and a new cluster 12 hold strangers.
        new = {3: p["stranger"], 12: set(pids[18:20]), 7: p["mia"]}
        _set_clusters(conn, new)

        _remap_names_and_tags(conn, old, new)

        names = _get_person_album_names(conn)
        assert names.get(12) != "Grandma", "her id now holds strangers; she must not stay on it"
        assert names.get(3) is None and names[7] == "Mia"
        assert _tags(conn) == {}, "no tag points at a stranger"
        ids = _album_ids(conn)
        assert pop_unplaced_tags(conn, [ids["Grandma"], ids["Leo"]]) == {
            ids["Grandma"]: grandma,
            ids["Leo"]: p["leo"],
        }, "both people keep their tags on their (unplaced) albums"
        assert names[CLUSTER_UNPLACED] in ("Grandma", "Leo")


class TestIdentityLabelsSwap:
    def test_reconstruct_identities_handles_swapped_best_clusters(self, people):
        from bpp.db.face_identity_remap import _reconstruct_identities

        conn, _, p = people
        # Labels: Leo's faces now cluster at 0, Mia's at 3 (a swap).
        _set_clusters(conn, {0: p["leo"], 3: p["mia"]})
        conn.execute("UPDATE face_embeddings SET identity='Leo' WHERE cluster_id=0")
        conn.execute("UPDATE face_embeddings SET identity='Mia' WHERE cluster_id=3")
        conn.commit()

        _reconstruct_identities(conn)

        assert _get_person_album_names(conn) == {0: "Leo", 3: "Mia"}


class TestRunCompletion:
    def test_a_run_where_every_chunk_failed_keeps_the_names(self, monkeypatch, tmp_path):
        """Nothing was extracted, so nothing can be matched: keep the record
        for the next extraction instead of dropping every name."""
        from bpp.web import analyze_face_extract as afe

        seen: list[bool] = []
        monkeypatch.setattr(afe, "_FACE_EXTRACTION_CHUNK_SIZE", 1)
        monkeypatch.setattr(afe, "_run_face_extraction_chunk", lambda *a, **k: (0, 0, None, False))
        monkeypatch.setattr(
            afe, "_finish_name_placement", lambda db, *, completed: seen.append(completed)
        )
        afe._run_chunked(
            [{"filepath": "a"}, {"filepath": "b"}], {}, str(tmp_path / "x.db"), None, None, None
        )
        assert seen == [False]

    def test_a_cancelled_run_keeps_the_switch_pending(self, monkeypatch, tmp_path):
        from bpp.web import analyze_face_extract as afe

        class Token:
            def is_set(self):
                return True

        seen: list[bool] = []
        monkeypatch.setattr(
            afe, "_finish_name_placement", lambda db, *, completed: seen.append(completed)
        )
        afe._run_chunked([{"filepath": "a"}], {}, str(tmp_path / "x.db"), None, None, Token())
        assert seen == [False]


class TestTaggedClustersAndRuns:
    def test_a_tagged_cluster_with_faces_is_matched_by_its_faces(self, people):
        """Three manual tags on a 5-photo cluster must not shrink the overlap
        to the tags alone (Jaccard 3/5 would still pass; 3 tags on 50 faces
        would not)."""
        conn, _, _ = people
        pids = _get_photo_ids(conn)
        conn.execute(
            "DELETE FROM albums WHERE album_type='smart_person'"
        )  # only the tagged cluster
        conn.commit()
        big = set(pids[0:15])  # a default-named cluster with many faces
        _set_clusters(conn, {7: big})
        _create_named_person_album(conn, "Person 8", 7, sorted(big))
        _add_tags(conn, 7, set(pids[0:2]))
        begin_switch(conn, "sface", {7: big})
        conn.commit()

        new = {2: big}
        _set_clusters(conn, new)
        finish_switch(conn, new)
        # A default-named cluster with manual tags is a person: placed by its
        # faces with the names, its tags with it.
        assert _tags(conn) == {2: set(pids[0:2])}

    def test_two_identities_with_one_best_cluster_do_not_collide(self, people):
        from bpp.db.face_identity_remap import _reconstruct_identities

        conn, _, p = people
        # The new cluster 7 holds both; Leo has more labelled faces in it.
        _set_clusters(conn, {7: p["leo"] | p["mia"], 5: p["stranger"]})
        leo, mia = sorted(p["leo"]), sorted(p["mia"])
        conn.executemany(
            "UPDATE face_embeddings SET identity='Leo' WHERE photo_id=?", [(x,) for x in leo]
        )
        conn.executemany(
            "UPDATE face_embeddings SET identity='Mia' WHERE photo_id=?", [(x,) for x in mia[:3]]
        )
        conn.commit()

        _reconstruct_identities(conn)  # must not raise IntegrityError

        names = _get_person_album_names(conn)
        assert names[7] == "Leo", names
        assert names.get(0) == "Mia", "the loser keeps its album where it was"

    def test_refresh_transfer_does_not_overwrite_another_person(self, people):
        """A name whose old cluster vanished is moved by the refresh, but never
        onto a cluster another person already owns by name."""
        from bpp.db.smart_albums import refresh_smart_albums

        conn, _, p = people
        # Leo's old cluster 3 is gone; his photos now sit in Mia's cluster 0.
        _set_clusters(conn, {0: p["leo"] | p["mia"], 5: p["stranger"]})
        _add_tags(conn, 0, {sorted(p["mia"])[0]})
        _add_tags(conn, 3, {sorted(p["leo"])[0]})

        refresh_smart_albums(conn)

        names = _get_person_album_names(conn)
        assert names[0] == "Mia", names
        assert "Leo" in _album_ids(conn), "unplaced name is kept, not dropped"
        assert _tags(conn)[0] == {sorted(p["mia"])[0]}, "Mia's own tag survives"

    def test_refresh_transfer_moves_the_album_onto_a_default_cluster(self, people):
        from bpp.db.smart_albums import refresh_smart_albums

        conn, _, p = people
        leo_album = _album_ids(conn)["Leo"]
        _set_clusters(conn, {9: p["leo"], 0: p["mia"]})
        _create_named_person_album(conn, "Person 10", 9, sorted(p["leo"]))

        refresh_smart_albums(conn)

        assert _get_person_album_names(conn)[9] == "Leo"
        assert _album_ids(conn)["Leo"] == leo_album, "the album moves; it is not renamed over"

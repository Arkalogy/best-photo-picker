"""A face-model switch places names once, from the complete run: a group
photo seen in an early batch cannot misplace a name, a
tag on a photo deleted meanwhile does not stop the switch from finishing,
and identity reconstruction moves a person's manual tags with the album.
"""

from __future__ import annotations

import logging

import pytest

from bpp.constants import CLUSTER_UNPLACED
from bpp.db.face_identity_remap import _reconstruct_identities
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
    _make_db,
)
from tests.test_face_name_switch import _placed, _set_clusters, _tags


@pytest.fixture
def alice_and_stranger(tmp_path):
    """Alice's old cluster holds photos 1-4; an unnamed person's holds 1, 5
    and 6 (photo 1 is a group photo of both). Alice has a manual tag on
    photo 10."""
    conn, _ = _make_db(tmp_path, n_photos=12)
    pids = _get_photo_ids(conn)
    alice = {pids[0], pids[1], pids[2], pids[3]}
    other = {pids[0], pids[4], pids[5]}
    old = {7: alice, 8: other}
    _set_clusters(conn, old)
    _create_named_person_album(conn, "Alice", 7, sorted(alice))
    conn.execute("INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (?, 7)", (pids[9],))
    conn.commit()
    return conn, old, {"alice": alice, "other": other, "tagged": pids[9], "pids": pids}


class TestPlacedFromTheCompleteRun:
    def test_a_group_photo_found_first_does_not_fix_a_name_on_the_wrong_cluster(
        self, alice_and_stranger
    ):
        conn, old, p = alice_and_stranger
        begin_switch(conn, "sface", old)
        conn.commit()
        # Chunk 1 holds only the group photo, and the new detector finds
        # only the other person in it: the one cluster so far is theirs.
        chunk1 = {0: {p["pids"][0]}}
        _set_clusters(conn, chunk1)
        assert _placed(conn) == {}, "nothing is placed from a partial run"
        assert switch_pending(conn) is True, "the record stays until the run completes"

        complete = {0: p["other"], 1: p["alice"] - {p["pids"][0]}}
        _set_clusters(conn, complete)

        finish_switch(conn, complete)

        named = {c: n for c, n in _placed(conn).items() if not n.startswith("Person ")}
        assert named == {1: "Alice"}, "the complete evidence puts Alice on her cluster"
        assert _tags(conn) == {1: {p["tagged"]}}, "her manual tag followed her"
        assert switch_pending(conn) is False

    def test_a_name_the_complete_evidence_gives_no_cluster_becomes_unplaced(self, tmp_path, caplog):
        from bpp.db.face_switch_record import pop_unplaced_tags

        conn, _ = _make_db(tmp_path, n_photos=12)
        pids = _get_photo_ids(conn)
        alice, bob = set(pids[0:4]), set(pids[4:8])
        old = {7: alice, 8: bob}
        _set_clusters(conn, old)
        _create_named_person_album(conn, "Alice", 7, sorted(alice))
        _create_named_person_album(conn, "Bob", 8, sorted(bob))
        conn.execute(
            "INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (?, 7)", (pids[9],)
        )
        conn.execute(
            "INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (?, 8)", (pids[8],)
        )
        conn.commit()
        begin_switch(conn, "sface", old)
        conn.commit()
        # Chunk 1: one of Alice's photos, in which the new model found a
        # face it will later group with Bob's.
        chunk1 = {0: {pids[0]}}
        _set_clusters(conn, chunk1)
        assert _placed(conn) == {} and _tags(conn) == {}, "parked until the run finishes"

        # The complete run: cluster 0 is Bob's four photos plus that one;
        # Alice's other photos yielded no faces at all.
        complete = {0: bob | {pids[0]}}
        _set_clusters(conn, complete)
        with caplog.at_level(logging.WARNING):
            unplaced = finish_switch(conn, complete)

        assert unplaced == 1
        assert _placed(conn) == {0: "Bob"}, "the stronger evidence takes the cluster"
        assert _tags(conn) == {0: {pids[8]}}, "Alice's tag left Bob's cluster"
        names = _get_person_album_names(conn)
        assert names[CLUSTER_UNPLACED] == "Alice", "kept as a person without faces"
        alice_id = conn.execute("SELECT id FROM albums WHERE name='Alice'").fetchone()[0]
        assert pop_unplaced_tags(conn, [alice_id]) == {alice_id: {pids[9]}}, "her tag is stashed"
        assert caplog.text.count("Could not place the name 'Alice'") == 1
        assert switch_pending(conn) is False

    def test_the_final_placement_moves_the_tags_of_a_corrected_name(self, tmp_path):
        conn, _ = _make_db(tmp_path, n_photos=12)
        pids = _get_photo_ids(conn)
        alice, bob = set(pids[0:4]), set(pids[4:8])
        old = {7: alice, 8: bob}
        _set_clusters(conn, old)
        _create_named_person_album(conn, "Alice", 7, sorted(alice))
        _create_named_person_album(conn, "Bob", 8, sorted(bob))
        conn.execute(
            "INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (?, 7)", (pids[10],)
        )
        conn.execute(
            "INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (?, 8)", (pids[11],)
        )
        conn.commit()
        begin_switch(conn, "sface", old)
        conn.commit()
        # Chunk 1 saw one photo of each, and the new model numbered them
        # the other way round from where the complete run will put them.
        chunk1 = {0: {pids[0]}, 1: {pids[4]}}
        _set_clusters(conn, chunk1)
        assert _placed(conn) == {} and _tags(conn) == {}, "parked until the run finishes"

        # The complete run: cluster 1 holds Alice's four photos, cluster 0
        # Bob's four and the single Alice photo chunk 1 misread.
        complete = {0: bob | {pids[0]}, 1: alice - {pids[0]}}
        _set_clusters(conn, complete)
        finish_switch(conn, complete)

        assert _placed(conn) == {1: "Alice", 0: "Bob"}
        assert _tags(conn) == {1: {pids[10]}, 0: {pids[11]}}, "the tags swapped with the names"
        assert load_pending(conn) is None


class TestDeletedTaggedPhoto:
    def test_a_tagged_photo_deleted_during_the_switch_does_not_block_finishing(
        self, alice_and_stranger
    ):
        conn, old, p = alice_and_stranger
        begin_switch(conn, "sface", old)
        conn.execute("DELETE FROM face_embeddings")
        conn.commit()
        # The user deletes the tagged photo for good while the switch is pending.
        conn.execute("DELETE FROM photos WHERE id=?", (p["tagged"],))
        conn.commit()

        complete = {0: p["alice"]}
        _set_clusters(conn, complete)
        assert finish_switch(conn, complete) == 0

        assert _placed(conn) == {0: "Alice"}
        assert _tags(conn) == {}, "the tag went with the photo"
        assert switch_pending(conn) is False, "the guard is lifted"


class TestReconstructionMovesTags:
    def test_identity_labels_move_the_tags_with_the_album(self, tmp_path):
        conn, _ = _make_db(tmp_path, n_photos=12)
        pids = _get_photo_ids(conn)
        alice, bob = {pids[0], pids[1]}, {pids[2], pids[3]}
        # The faces were renumbered (Alice's are now cluster 1, Bob's 0)
        # but the albums and the tags still carry the old numbers.
        _set_clusters(conn, {1: alice, 0: bob})
        conn.execute("UPDATE face_embeddings SET identity='Alice' WHERE cluster_id=1")
        conn.execute("UPDATE face_embeddings SET identity='Bob' WHERE cluster_id=0")
        _create_named_person_album(conn, "Alice", 0, sorted(alice))
        _create_named_person_album(conn, "Bob", 1, sorted(bob))
        conn.execute(
            "INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (?, 0)", (pids[10],)
        )
        conn.commit()

        _reconstruct_identities(conn)

        assert _get_person_album_names(conn) == {1: "Alice", 0: "Bob"}
        assert _tags(conn) == {1: {pids[10]}}, "Alice's tag followed her album"
        assert CLUSTER_UNPLACED not in _get_person_album_names(conn)

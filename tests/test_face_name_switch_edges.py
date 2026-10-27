"""Edge cases of name placement across a face-model switch: several
unplaced people at once, best-only tie deferral, carried unplaced people,
tags that wait on the album, typed carry keys, hidden parked people, and
the startup sweep that leaves a live import alone.

Shared fixtures are imported by name and re-bound as test parameters.
"""

# ruff: noqa: F811

from __future__ import annotations

import logging
import os
import sqlite3
import time

from bpp.constants import CLUSTER_UNPLACED
from bpp.db.face_name_placement import (
    begin_switch,
    finish_switch,
    load_pending,
    switch_pending,
)
from tests._face_name_preservation_shared import _create_named_person_album, _get_person_album_names
from tests.test_face_name_switch import (  # noqa: F401
    _album_ids,
    _placed,
    _set_clusters,
    _tags,
    people,
)


class TestUnplacedPeople:
    def test_several_unplaced_names_finish_without_a_unique_clash(self, people):
        conn, old, p = people
        begin_switch(conn, "sface", old)
        conn.commit()
        strangers = {0: set(p["pids"][12:16])}
        _set_clusters(conn, strangers)
        assert finish_switch(conn, strangers) == 2
        names = {
            n
            for (n,) in conn.execute(
                "SELECT name FROM albums WHERE smart_person_cluster_id=?", (CLUSTER_UNPLACED,)
            )
        }
        assert names == {"Alice", "Bob"}
        assert switch_pending(conn) is False

    def test_an_unplaced_person_is_carried_into_the_next_switch_and_parked(self, people):
        conn, old, p = people
        begin_switch(conn, "sface", old)
        conn.commit()
        _set_clusters(conn, {0: p["alice"]})
        finish_switch(conn, {0: p["alice"]})  # Bob unplaced (-3)

        begin_switch(conn, "buffalo_s", {0: p["alice"]})
        conn.commit()
        pending = load_pending(conn)
        bob_key = next(k for k, n in pending.names.items() if n == "Bob")
        assert pending.evidence(bob_key) == p["bob"], (
            "his album keeps his solo photos as evidence (the group photo is left out)"
        )
        assert CLUSTER_UNPLACED not in _get_person_album_names(conn), (
            "parked, not left to the refresh"
        )
        new = {0: p["alice"], 1: p["bob"]}
        _set_clusters(conn, new)
        finish_switch(conn, new)
        assert _placed(conn) == {0: "Alice", 1: "Bob"}

    def test_three_unplaced_people_are_placed_by_one_refresh(self, people):
        from bpp.db.smart_albums import refresh_smart_albums

        conn, old, p = people
        pids = p["pids"]
        carol = set(pids[12:15])
        _create_named_person_album(conn, "Carol", 7, sorted(carol))
        old2 = {**old, 7: carol}
        _set_clusters(conn, old2)
        begin_switch(conn, "sface", old2)
        conn.commit()
        _set_clusters(conn, {9: set(pids[16:18])})
        finish_switch(conn, {9: set(pids[16:18])})  # all three unplaced

        _set_clusters(conn, {0: p["alice"], 1: p["bob"], 2: carol, 9: set(pids[16:18])})
        refresh_smart_albums(conn)
        placed = _placed(conn)
        assert {placed.get(0), placed.get(1), placed.get(2)} == {"Alice", "Bob", "Carol"}

    def test_a_tagged_default_cluster_keeps_its_tags_by_its_faces(self, people):
        conn, old, p = people
        pids = p["pids"]
        faces = set(pids[12:17])
        _set_clusters(conn, {**old, 7: faces})
        conn.executemany(
            "INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (?, 7)",
            [(pids[18],), (pids[19],)],  # tagged photos without a detected face
        )
        conn.commit()
        begin_switch(conn, "sface", {**old, 7: faces})
        conn.commit()
        new = {0: p["alice"], 1: p["bob"], 2: faces}
        _set_clusters(conn, new)
        finish_switch(conn, new)  # tagged clusters nobody named are placed at the final pass
        assert _tags(conn) == {2: {pids[18], pids[19]}}, "the 2 tags follow the cluster's 5 faces"

    def test_carry_keys_are_stable_across_repeated_switches(self):
        from bpp.db.face_name_placement import _carry_key_for_album, _carry_key_for_tags

        assert _carry_key_for_album(42) == _carry_key_for_album(42) == "album:42"
        k1 = _carry_key_for_tags(5)
        assert _carry_key_for_tags(k1) == k1 == "tags:5"
        assert _carry_key_for_album(1005) != _carry_key_for_tags(5)

    def test_a_dropped_name_is_still_reported_after_a_second_switch(self, people, caplog):
        conn, old, p = people
        begin_switch(conn, "sface", old)
        conn.commit()
        pending = load_pending(conn)
        pending.dropped.append("Grandma")
        from bpp.db.face_switch_record import _save_pending

        _save_pending(conn, pending)
        conn.commit()
        begin_switch(conn, "buffalo_s", {})
        conn.commit()
        new = {0: p["alice"], 1: p["bob"]}
        _set_clusters(conn, new)
        with caplog.at_level(logging.WARNING):
            finish_switch(conn, new)
        assert "Grandma" in caplog.text


class TestCarriedNamesAndTags:
    def test_an_unplaced_persons_tags_wait_on_the_album_and_come_back(self, people):
        from bpp.db.smart_albums import refresh_smart_albums

        conn, old, p = people
        pids = p["pids"]
        conn.executemany(
            "INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (?, 0)",
            [(pids[18],), (pids[19],)],  # Bob's manual tags
        )
        conn.commit()
        begin_switch(conn, "sface", old)
        conn.commit()
        _set_clusters(conn, {0: p["alice"]})
        finish_switch(conn, {0: p["alice"]})  # Bob unplaced
        assert _tags(conn) == {}, "nothing points at a cluster yet"

        _set_clusters(conn, {0: p["alice"], 4: p["bob"]})
        refresh_smart_albums(conn)
        assert _placed(conn)[4] == "Bob"
        assert _tags(conn) == {4: {pids[18], pids[19]}}, "his tags came back with him"

    def test_a_carried_tagged_cluster_keeps_its_face_evidence(self, people):
        conn, old, p = people
        pids = p["pids"]
        faces = set(pids[12:14])
        _set_clusters(conn, {**old, 7: faces})
        conn.execute(
            "INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (?, 7)", (pids[15],)
        )
        conn.commit()
        begin_switch(conn, "sface", {**old, 7: faces})
        conn.commit()
        begin_switch(conn, "buffalo_s", {})  # switched again before anything was placed
        conn.commit()
        pending = load_pending(conn)
        key = next(k for k in pending.tags if str(k).startswith("tags:"))
        assert pending.evidence(key) == faces, "identified by its faces, not only its one tag"

    def test_carry_keys_cannot_collide_at_any_id(self):
        from bpp.db.face_name_placement import _carry_key_for_album, _carry_key_for_tags
        from bpp.db.face_switch_record import PendingNames

        assert _carry_key_for_album(1_000_005) != _carry_key_for_tags(5)
        assert _carry_key_for_tags(_carry_key_for_tags(5)) == _carry_key_for_tags(5)
        rec = PendingNames(
            to_method="sface",
            old_cluster_photos={3: {1}, "album:9": {2}},
            names={3: "A", "album:9": "B"},
            tags={"tags:5": {4}},
            albums={3: 1, "album:9": 9},
        )
        back = PendingNames.from_json(rec.to_json())
        assert back is not None
        assert back.names == {3: "A", "album:9": "B"} and back.tags == {"tags:5": {4}}

    def test_parked_and_unplaced_people_are_not_listed(self, people):
        from bpp.db.albums import list_albums

        conn, old, p = people
        conn.row_factory = sqlite3.Row
        begin_switch(conn, "sface", old)
        conn.commit()  # both parked
        listed = {a["name"] for a in list_albums(conn) if a["album_type"] == "smart_person"}
        assert listed == set()
        _set_clusters(conn, {0: p["alice"]})
        finish_switch(conn, {0: p["alice"]})  # Alice placed, Bob unplaced
        listed = {a["name"] for a in list_albums(conn) if a["album_type"] == "smart_person"}
        assert listed == {"Alice"}


class TestStartupSweepAgain:
    def test_a_half_copy_of_an_old_photo_is_still_a_live_import(self, tmp_path):
        import shutil

        from bpp.db.library_copy import remove_stale_import_tmp

        src = tmp_path / "old_photo.jpg"
        src.write_bytes(b"x" * 100)
        long_ago = time.time() - 86400
        os.utime(src, (long_ago, long_ago))
        batch = tmp_path / "photos" / "trip"
        batch.mkdir(parents=True)
        shutil.copy2(src, batch / "old_photo.jpg.tmp")  # copy2 keeps the old mtime

        assert remove_stale_import_tmp(str(tmp_path / "photos")) == 0
        assert (batch / "old_photo.jpg.tmp").exists()

    def test_an_active_batch_is_never_swept(self, tmp_path):
        from bpp.db.library_copy import active_import, remove_stale_import_tmp

        batch = tmp_path / "photos" / "trip"
        batch.mkdir(parents=True)
        stale = batch / "x.jpg.tmp"
        stale.write_bytes(b"half")
        long_ago = time.time() - 86400
        os.utime(stale, (long_ago, long_ago))
        with active_import(str(batch)):
            assert remove_stale_import_tmp(str(tmp_path / "photos"), older_than_s=0) == 0
        assert remove_stale_import_tmp(str(tmp_path / "photos"), older_than_s=0) == 1


class TestKeptNames:
    def test_a_person_album_with_no_cluster_id_is_still_listed(self, people):
        from bpp.db.albums import list_albums

        conn, _old, _p = people
        conn.row_factory = sqlite3.Row
        conn.execute(
            "INSERT INTO albums (name, album_type, rule_json)"
            " VALUES ('Legacy', 'smart_person', NULL)"
        )
        conn.commit()
        assert "Legacy" in {a["name"] for a in list_albums(conn)}

    def test_a_config_write_keeps_the_stashed_tags(self, people):
        from bpp.db.albums import get_album, update_album
        from bpp.db.face_switch_record import pop_unplaced_tags, stash_unplaced_tags

        conn, _old, _p = people
        conn.row_factory = sqlite3.Row
        aid = _album_ids(conn)["Alice"]
        stash_unplaced_tags(conn, {aid: {7, 8}})
        conn.commit()
        update_album(conn, aid, config={"k": 3})  # a client writes the whole config
        assert get_album(conn, aid)["config"] == {"k": 3}, "the stash is not shown to clients"
        assert pop_unplaced_tags(conn, [aid]) == {aid: {7, 8}}, "but it survived the write"

    def test_a_recluster_keeps_an_unmatched_name_and_its_tags(self, people):
        from bpp.db.face_identity_remap import _remap_names_and_tags
        from bpp.db.smart_albums import refresh_smart_albums

        conn, old, p = people
        pids = p["pids"]
        conn.executemany(
            "INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (?, 3)",
            [(pids[18],), (pids[19],)],  # Alice's manual tags
        )
        conn.commit()
        # Re-cluster: Alice's faces vanish; her old id 3 now holds strangers.
        new = {3: set(pids[12:15]), 7: p["bob"]}
        _set_clusters(conn, new)
        _remap_names_and_tags(conn, old, new)
        assert _get_person_album_names(conn)[CLUSTER_UNPLACED] == "Alice"
        assert _tags(conn) == {}, "nothing points at the strangers"

        # Later her faces are found again: the refresh places her and her tags return.
        _set_clusters(conn, {3: set(pids[12:15]), 7: p["bob"], 9: p["alice"]})
        refresh_smart_albums(conn)
        assert _placed(conn)[9] == "Alice"
        assert _tags(conn) == {9: {pids[18], pids[19]}}

    def test_a_tag_match_never_takes_a_cluster_a_name_is_waiting_for(self, people):
        conn, _old, p = people
        pids = p["pids"]
        shared = set(pids[10:15])
        old2 = {3: p["alice"] | shared, 0: p["bob"] | shared}
        _set_clusters(conn, old2)
        conn.execute(
            "INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (?, 7)", (pids[0],)
        )
        conn.commit()  # an unnamed cluster tagged on one of Alice's photos
        begin_switch(conn, "sface", old2)
        conn.commit()
        chunk = {0: shared, 1: {pids[0], pids[1]}}  # a tie on 0; cluster 1 is Alice's weak match
        _set_clusters(conn, chunk)
        finish_switch(conn, chunk)
        placed = {c: n for c, n in _placed(conn).items() if not n.startswith("Person ")}
        assert placed == {1: "Alice"}, (
            "the 5/5 tie on group photos places no one; her own solo photos then put her on 1"
        )
        tags = _tags(conn)
        assert 1 not in tags, "the tag-only cluster did not take a cluster Alice may still get"
        assert list(tags.values()) == [{pids[0]}], "its tag is kept, on a cluster of its own"

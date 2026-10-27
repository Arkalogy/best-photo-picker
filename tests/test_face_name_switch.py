"""A face-model switch placing names when the run ends: nothing is placed
from a partial run, a partly failed run keeps the record, a second switch carries
unplaced names over, a tag-only person is placed by tags, a blocked move
chain is settled before any write, a replayed phase 7 is a no-op, and the
startup sweep leaves a live import alone.
"""

from __future__ import annotations

import logging
import sqlite3
import time

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
    _insert_embeddings,
    _make_db,
)


def _album_ids(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        n: a for n, a in conn.execute("SELECT name, id FROM albums WHERE album_type='smart_person'")
    }


def _placed(conn: sqlite3.Connection) -> dict[int, str]:
    return {c: n for c, n in _get_person_album_names(conn).items() if c >= 0}


def _tags(conn: sqlite3.Connection) -> dict[int, set[int]]:
    out: dict[int, set[int]] = {}
    for pid, cid in conn.execute("SELECT photo_id, cluster_id FROM photo_person_tags"):
        out.setdefault(cid, set()).add(pid)
    return out


def _set_clusters(conn: sqlite3.Connection, clusters: dict[int, set[int]]) -> None:
    conn.execute("DELETE FROM face_embeddings")
    conn.commit()
    _insert_embeddings(conn, {cid: sorted(pids) for cid, pids in clusters.items()})


@pytest.fixture
def people(tmp_path):
    conn, _ = _make_db(tmp_path, n_photos=20)
    pids = _get_photo_ids(conn)
    alice, bob = set(pids[0:5]), set(pids[5:10])
    group = pids[10]  # one photo with both of them
    old = {3: alice | {group}, 0: bob | {group}}
    _set_clusters(conn, old)
    _create_named_person_album(conn, "Alice", 3, sorted(old[3]))
    _create_named_person_album(conn, "Bob", 0, sorted(old[0]))
    return conn, old, {"alice": alice, "bob": bob, "group": group, "pids": pids}


class TestNothingPlacedMidRun:
    def test_names_stay_parked_until_the_run_finishes(self, people):
        conn, old, p = people
        begin_switch(conn, "sface", old)
        conn.commit()
        from bpp.db.smart_albums import refresh_smart_albums
        from bpp.web import face_extraction_phases as phases

        _set_clusters(conn, {0: {p["group"]} | p["alice"]})  # a first batch
        snapshot = phases.PreExtractSnapshot(
            stored_method="dlib",
            method_changed=True,
            old_cluster_photos=old,
            stale_photo_ids=frozenset(),
            dismissed_slots=frozenset(),
        )
        phases.reconstruct_identities(  # the per-batch phase 7, refresh included
            conn,
            snapshot,
            reconstruct_identities_fn=_reconstruct_identities,
            remap_names_and_tags_fn=lambda *_a: None,
        )
        refresh_smart_albums(conn)
        named = {c: n for c, n in _placed(conn).items() if not n.startswith("Person ")}
        assert named == {}, "no name is placed from a partial run"
        assert switch_pending(conn) is True

    def test_a_tie_in_the_complete_run_is_not_guessed(self, people):
        """One group photo is the only evidence for both: ambiguous, so
        neither name is put on either cluster; both are kept unplaced."""
        conn, old, p = people
        begin_switch(conn, "sface", old)
        conn.commit()
        only = {0: {p["group"]}, 1: {p["group"]}}
        _set_clusters(conn, {0: {p["group"]}})
        finish_switch(conn, only)
        named = {c: n for c, n in _placed(conn).items() if not n.startswith("Person ")}
        assert named == {}, "no arbitrary pick between Alice and Bob"
        kept = {
            n for (n,) in conn.execute("SELECT name FROM albums WHERE smart_person_cluster_id=-3")
        }
        assert kept == {"Alice", "Bob"}
        assert switch_pending(conn) is False


class TestFinishKeepsNames:
    def test_unplaced_names_are_kept_as_people_without_faces(self, people, caplog):
        conn, old, p = people
        begin_switch(conn, "sface", old)
        conn.commit()
        only_alice = {0: p["alice"]}
        _set_clusters(conn, only_alice)
        with caplog.at_level(logging.WARNING):
            left = finish_switch(conn, only_alice)
        assert left == 1
        assert _placed(conn) == {0: "Alice"}
        assert _get_person_album_names(conn)[CLUSTER_UNPLACED] == "Bob", "kept, not deleted"
        assert "Could not place the name 'Bob'" in caplog.text
        assert switch_pending(conn) is False

    def test_a_refresh_later_places_the_unplaced_person_by_photo_overlap(self, people):
        from bpp.db.smart_albums import refresh_smart_albums

        conn, old, p = people
        begin_switch(conn, "sface", old)
        conn.commit()
        _set_clusters(conn, {0: p["alice"]})
        finish_switch(conn, {0: p["alice"]})
        assert _get_person_album_names(conn)[CLUSTER_UNPLACED] == "Bob"

        _set_clusters(conn, {0: p["alice"], 4: p["bob"]})  # Bob's photos analysed later
        refresh_smart_albums(conn)
        assert _placed(conn) == {0: "Alice", 4: "Bob"}


class TestRunCompletion:
    def test_a_failed_chunk_keeps_the_record(self, monkeypatch, tmp_path):
        from bpp.web import analyze_face_extract as afe

        seen: list[bool] = []
        results = iter([(3, 1, None, True), (0, 0, None, False)])
        monkeypatch.setattr(afe, "_FACE_EXTRACTION_CHUNK_SIZE", 1)
        monkeypatch.setattr(afe, "_run_face_extraction_chunk", lambda *a, **k: next(results))
        monkeypatch.setattr(
            afe, "_finish_name_placement", lambda db, *, completed: seen.append(completed)
        )
        afe._run_chunked(
            [{"filepath": "a"}, {"filepath": "b"}], {}, str(tmp_path / "x.db"), None, None, None
        )
        assert seen == [False], "a chunk's photos were never processed: say nothing about them"

    def test_a_cancel_during_the_last_chunk_keeps_the_record(self, monkeypatch, tmp_path):
        from bpp.web import analyze_face_extract as afe

        class Token:
            armed = False

            def is_set(self):
                return self.armed

        token = Token()
        seen: list[bool] = []

        def chunk(*_a, **_k):
            token.armed = True  # the user cancels while this chunk runs
            return (2, 1, None, True)

        monkeypatch.setattr(afe, "_run_face_extraction_chunk", chunk)
        monkeypatch.setattr(
            afe, "_finish_name_placement", lambda db, *, completed: seen.append(completed)
        )
        afe._run_chunked([{"filepath": "a"}], {}, str(tmp_path / "x.db"), None, None, token)
        assert seen == [False]


class TestSecondSwitch:
    def test_an_unplaced_name_is_carried_into_the_next_switch(self, people):
        conn, old, p = people
        begin_switch(conn, "sface", old)
        conn.commit()
        _set_clusters(conn, {0: p["alice"]})
        bob_album = _album_ids(conn)["Bob"]

        begin_switch(conn, "buffalo_s", {0: p["alice"]})  # the user switches again
        conn.commit()
        pending = load_pending(conn)
        assert pending is not None and pending.to_method == "buffalo_s"
        carried = [k for k, n in pending.names.items() if n == "Bob"]
        assert len(carried) == 1 and pending.albums[carried[0]] == bob_album
        assert pending.evidence(carried[0]) == old[0], "Bob's original evidence travels with him"

        new = {0: p["alice"], 1: p["bob"]}
        _set_clusters(conn, new)
        finish_switch(conn, new)
        assert _placed(conn) == {0: "Alice", 1: "Bob"}


class TestTagOnlyPerson:
    def test_a_person_known_only_by_tags_is_placed_by_them(self, people):
        conn, old, p = people
        pids = p["pids"]
        grandma = set(pids[12:15])
        _create_named_person_album(conn, "Grandma", 12, sorted(grandma))
        conn.executemany(
            "INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (?, 12)",
            [(x,) for x in grandma],
        )
        conn.commit()
        begin_switch(conn, "sface", old)  # cluster 12 has no faces, only tags
        conn.commit()
        new = {0: p["alice"], 1: p["bob"], 2: grandma}
        _set_clusters(conn, new)
        finish_switch(conn, new)

        assert _placed(conn)[2] == "Grandma"
        assert _tags(conn) == {2: grandma}


class TestSettledMoves:
    def test_a_blocked_chain_does_not_raise_or_half_apply(self, people):
        conn, _, p = people
        pids = p["pids"]
        # Alice 3→0, Bob 0→2, Carol sits at 2 and stays: Bob's move is
        # blocked, so Alice's onto 0 must be dropped too (Bob never left).
        carol = set(pids[12:15])
        _set_clusters(conn, {0: p["alice"], 2: p["bob"] | carol})
        _create_named_person_album(conn, "Carol", 2, sorted(carol))
        conn.executemany(
            "UPDATE face_embeddings SET identity='Alice' WHERE photo_id=?",
            [(x,) for x in p["alice"]],
        )
        conn.executemany(
            "UPDATE face_embeddings SET identity='Bob' WHERE photo_id=?", [(x,) for x in p["bob"]]
        )
        conn.commit()

        _reconstruct_identities(conn)  # must not raise

        names = _get_person_album_names(conn)
        assert names[2] == "Carol"
        assert names[0] == "Bob", "Bob never moved"
        assert names[3] == "Alice", "Alice's move onto 0 was dropped with Bob's"


class TestReplaySafe:
    def test_replaying_phase_7_after_the_record_cleared_moves_nothing(self, people):
        from bpp.db.face_identity_remap import _remap_names_and_tags, _snapshot_cluster_photos
        from bpp.web import face_extraction_phases as phases

        conn, old, p = people
        begin_switch(conn, "sface", old)
        conn.commit()
        swapped = {0: p["alice"] | {p["group"]}, 3: p["bob"] | {p["group"]}}
        _set_clusters(conn, swapped)
        snapshot = phases.PreExtractSnapshot(
            stored_method="dlib",
            method_changed=True,
            old_cluster_photos=old,
            stale_photo_ids=frozenset(),
            dismissed_slots=frozenset(),
        )
        run = lambda: phases.reconstruct_identities(  # noqa: E731
            conn,
            snapshot,
            reconstruct_identities_fn=_reconstruct_identities,
            remap_names_and_tags_fn=_remap_names_and_tags,
        )
        run()
        finish_switch(conn, _snapshot_cluster_photos(conn))
        first = _placed(conn)
        assert first == {0: "Alice", 3: "Bob"}
        run()  # the crash-recovery replay, pending record already gone
        assert _placed(conn) == first, "a replay must not swap the names back"


class TestStartupSweep:
    def test_the_sweep_judges_age_by_when_the_copy_was_written(self, tmp_path, monkeypatch):
        from bpp.db.library_copy import remove_stale_import_tmp

        batch = tmp_path / "photos" / "trip"
        batch.mkdir(parents=True)
        (batch / "old.jpg.tmp").write_bytes(b"half")
        real_now = time.time()
        monkeypatch.setattr(time, "time", lambda: real_now + 3600)  # an hour passes
        assert remove_stale_import_tmp(str(tmp_path / "photos"), older_than_s=1800) == 1
        monkeypatch.undo()
        (batch / "fresh.jpg.tmp").write_bytes(b"half")
        assert remove_stale_import_tmp(str(tmp_path / "photos")) == 0, (
            "a file written just now stays"
        )

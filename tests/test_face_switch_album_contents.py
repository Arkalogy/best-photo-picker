"""When a face-model switch ends, the person albums hold the right photos
without waiting for a browser: a corrected name's album shows its final
cluster, and a name nothing matched keeps its ORIGINAL photos, so a later
extraction that finds that person places them.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from bpp.constants import CLUSTER_UNPLACED
from bpp.db.connection import close_all_connections, get_db, init_db
from bpp.db.face_name_placement import begin_switch
from bpp.db.photos import bulk_upsert_photos
from bpp.db.smart_album_people import _refresh_person_albums
from bpp.web.analyze_face_extract import _finish_name_placement


@pytest.fixture
def lib(tmp_path):
    path = str(tmp_path / "lib.db")
    init_db(path)
    conn = get_db(path)
    bulk_upsert_photos(conn, [{"filepath": f"/synthetic/{i}.jpg"} for i in range(12)])
    conn.execute("UPDATE photos SET missing=0")
    conn.commit()
    yield conn, path, [r[0] for r in conn.execute("SELECT id FROM photos ORDER BY id")]
    close_all_connections()


def _clusters(conn, mapping: dict[int, set[int]]) -> None:
    conn.execute("DELETE FROM face_embeddings")
    rows, seen = [], {}
    for cid, pids in mapping.items():
        for pid in sorted(pids):
            fi = seen.get(pid, 0)
            seen[pid] = fi + 1
            rows.append((pid, fi, cid, np.array([1.0, 0.0], dtype=np.float32).tobytes()))
    conn.executemany(
        "INSERT INTO face_embeddings (photo_id, face_index, cluster_id, embedding) "
        "VALUES (?, ?, ?, ?)",
        rows,
    )
    conn.commit()


def _person(conn, name: str, cid: int, pids: set[int]) -> int:
    aid = conn.execute(
        "INSERT INTO albums (name, album_type, rule_json) VALUES (?, 'smart_person', ?)",
        (name, json.dumps({"cluster_id": cid})),
    ).lastrowid
    conn.executemany(
        "INSERT INTO album_photos (album_id, photo_id) VALUES (?, ?)", [(aid, p) for p in pids]
    )
    conn.commit()
    return aid


def _members(conn, aid: int) -> set[int]:
    return {
        r[0] for r in conn.execute("SELECT photo_id FROM album_photos WHERE album_id=?", (aid,))
    }


def _cid(conn, aid: int) -> int:
    return conn.execute("SELECT smart_person_cluster_id FROM albums WHERE id=?", (aid,)).fetchone()[
        0
    ]


def _run_chunks(conn, path: str, chunks: list[dict[int, set[int]]]) -> None:
    """Each chunk: clusters and the per-chunk refresh (no name placed);
    then the parent's completion helper (no browser, no extra refresh)."""
    for chunk in chunks:
        _clusters(conn, chunk)
        _refresh_person_albums(conn)
    _finish_name_placement(path, completed=True)


def test_a_corrected_names_album_shows_its_final_cluster(lib):
    conn, path, p = lib
    alice, other, tag = set(p[:4]), {p[0], p[4], p[5]}, p[9]
    old = {7: alice, 8: other}
    _clusters(conn, old)
    aid = _person(conn, "Alice", 7, alice)
    conn.execute("INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (?, 7)", (tag,))
    conn.commit()
    begin_switch(conn, "sface", old)
    conn.commit()

    complete = {0: other, 1: alice - {p[0]}}
    _run_chunks(conn, path, [{0: {p[0]}}, complete])

    assert _cid(conn, aid) == 1
    assert _members(conn, aid) == complete[1] | {tag}, "rebuilt on the server"


def test_a_withdrawn_name_keeps_its_original_photos_and_is_placed_later(lib):
    conn, path, p = lib
    alice, bob, tag = set(p[:4]), set(p[4:8]), p[9]
    old = {7: alice, 8: bob}
    _clusters(conn, old)
    aid = _person(conn, "Alice", 7, alice)
    _person(conn, "Bob", 8, bob)
    conn.execute("INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (?, 7)", (tag,))
    conn.commit()
    begin_switch(conn, "sface", old)
    conn.commit()

    complete = {0: bob | {p[0]}}
    _run_chunks(conn, path, [{0: {p[0]}}, complete])

    assert _cid(conn, aid) == CLUSTER_UNPLACED
    assert _members(conn, aid) == alice, "her own photos, not Bob's (her tag waits stashed)"

    # A later extraction finds the rest of Alice.
    _clusters(conn, {0: complete[0], 1: alice - {p[0]}})
    _refresh_person_albums(conn)
    assert _cid(conn, aid) == 1
    assert conn.execute(
        "SELECT 1 FROM photo_person_tags WHERE photo_id=? AND cluster_id=1", (tag,)
    ).fetchone(), "her tag came back"


def test_an_unplaced_persons_album_keeps_photos_trashed_during_the_switch(lib):
    """Trash can be undone: a trashed photo stays part of the person's
    evidence (a photo deleted for good drops out)."""
    conn, path, p = lib
    alice = set(p[:4])
    _clusters(conn, {7: alice})
    aid = _person(conn, "Alice", 7, alice)
    begin_switch(conn, "sface", {7: alice})
    conn.commit()
    conn.execute("UPDATE photos SET deleted_at=datetime('now') WHERE id=?", (p[1],))
    conn.execute("DELETE FROM photos WHERE id=?", (p[2],))
    conn.commit()

    _run_chunks(conn, path, [{0: set(p[8:10])}])  # nothing of Alice's

    assert _cid(conn, aid) == CLUSTER_UNPLACED
    assert _members(conn, aid) == alice - {p[2]}


def test_a_tag_on_a_group_photo_does_not_pull_an_unplaced_name_onto_a_stranger(lib):
    conn, path, p = lib
    alice, group = set(p[:3]), p[9]
    _clusters(conn, {7: alice})
    aid = _person(conn, "Alice", 7, alice)
    conn.execute("INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (?, 7)", (group,))
    conn.commit()
    begin_switch(conn, "sface", {7: alice})
    conn.commit()

    # The only new cluster is the other person in the group photo.
    _run_chunks(conn, path, [{5: {group, p[10]}}])

    assert _cid(conn, aid) == CLUSTER_UNPLACED, "her faces matched nothing; the tag alone does not"


def test_a_config_value_mentioning_the_stash_key_does_not_keep_a_default_orphan(lib):
    conn, _path, p = lib
    _clusters(conn, {0: {p[1]}})  # the refresh only sweeps a library with clusters
    aid = _person(conn, "Person 12", 11, {p[0]})
    conn.execute(
        "UPDATE albums SET config_json=? WHERE id=?",
        (json.dumps({"note": "unplaced_tags", "x": {"unplaced_tags": 1}}), aid),
    )
    conn.commit()
    _refresh_person_albums(conn)
    assert conn.execute("SELECT 1 FROM albums WHERE id=?", (aid,)).fetchone() is None


def test_a_give_up_keeps_the_tags_of_a_cluster_nobody_named(lib):
    """Recovery gave up: nothing was matched, and an unnamed tagged
    cluster's manual tags (held only by the record) are kept, not dropped."""
    from bpp.db.face_name_placement import finish_switch

    conn, _path, p = lib
    _clusters(conn, {7: {p[0], p[1]}})
    conn.execute("INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (?, 7)", (p[5],))
    conn.commit()
    begin_switch(conn, "sface", {7: {p[0], p[1]}})
    conn.commit()

    finish_switch(conn)  # the give-up path: no clusters

    rows = conn.execute("SELECT photo_id, cluster_id FROM photo_person_tags").fetchall()
    assert [r[0] for r in rows] == [p[5]], "the tag survived"
    album = conn.execute(
        "SELECT name FROM albums WHERE smart_person_cluster_id=?", (rows[0][1],)
    ).fetchone()
    assert album is not None and album[0].startswith("Person ")

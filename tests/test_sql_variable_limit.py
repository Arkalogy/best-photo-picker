"""Lookups over every photo with a face stay under SQLite's limit on bound
values (32,766 in the bundled build; it depends on how SQLite was built):
a library with more face photos than that must still re-cluster, switch
the face model and refresh albums. The test lowers the limit on its own
connection so the check means the same on every platform."""

from __future__ import annotations

import json
import sqlite3

import numpy as np
import pytest

from bpp.db.connection import close_all_connections, get_db, init_db
from bpp.db.photos import bulk_upsert_photos

LIMIT = 999  # bound values one statement may take, on the test connection
N = 3_000  # more ids than that


@pytest.fixture
def big(tmp_path):
    path = str(tmp_path / "library.db")
    init_db(path)
    db = get_db(path)
    db.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, LIMIT)
    bulk_upsert_photos(db, [{"filepath": f"/p/{i}.jpg"} for i in range(1, N + 1)])
    db.execute("UPDATE photos SET missing=0")
    assert tuple(db.execute("SELECT MIN(id), MAX(id) FROM photos").fetchone()) == (1, N)
    emb = np.zeros(128, dtype=np.float32).tobytes()
    # Odd photos hold one face, even photos two.
    db.executemany(
        "INSERT INTO face_embeddings (photo_id, face_index, cluster_id, embedding, quality) "
        "VALUES (?, ?, ?, ?, 1.0)",
        [(i, f, i % 7, emb) for i in range(1, N + 1) for f in range(1 if i % 2 else 2)],
    )
    db.commit()
    yield db
    close_all_connections()


def test_the_lowered_limit_is_in_force(big):
    ph = ",".join("?" * N)
    with pytest.raises(sqlite3.OperationalError):
        big.execute(f"SELECT id FROM photos WHERE id IN ({ph})", list(range(N)))


def test_face_photo_lookups_take_more_ids_than_one_statement_binds(big):
    from bpp.db.face_switch_record import alive_photo_ids, group_photos, single_face_photos

    ids = set(range(1, N + 1)) | {N + 5}  # one id whose row is gone
    assert alive_photo_ids(big, ids) == set(range(1, N + 1))
    assert single_face_photos(big, ids) == set(range(1, N + 1, 2))
    assert group_photos(big, ids) == set(range(2, N + 1, 2))


def test_album_evidence_and_holds_take_more_ids_than_one_statement_binds(big):
    from bpp.db.face_switch_record import held_albums, hold_albums, set_album_evidence

    aid = big.execute(
        "INSERT INTO albums (name, album_type, rule_json) VALUES ('Ann', 'smart_person', ?)",
        (json.dumps({"cluster_id": 3}),),
    ).lastrowid
    photos = set(range(1, N + 1))
    set_album_evidence(big, {aid: photos}, active_only=True)
    n = big.execute("SELECT COUNT(*) FROM album_photos WHERE album_id=?", (aid,)).fetchone()[0]
    assert n == N

    big.execute("DELETE FROM face_embeddings WHERE photo_id = 1")
    hold_albums(big, {aid: photos})
    assert held_albums(big, [aid]) == {aid}, "photo 1 lost its face: still held"


def test_album_bulk_edits_take_more_ids_than_one_statement_binds(big):
    from bpp.db.albums import (
        add_photos_to_album,
        remove_photos_from_album,
        set_favorites_bulk,
        set_overrides_bulk,
    )

    aid = big.execute("INSERT INTO albums (name) VALUES ('All')").lastrowid
    ids = list(range(1, N + 1))
    add_photos_to_album(big, aid, ids)
    assert set_overrides_bulk(big, aid, ids, "include") == N
    assert set_favorites_bulk(big, aid, ids, True) == N
    remove_photos_from_album(big, aid, ids)
    assert big.execute("SELECT COUNT(*) FROM album_photos").fetchone()[0] == 0


def test_face_counts_resync_over_more_photos_than_one_statement_binds(big):
    from bpp.db.face_cluster_ops import sync_face_counts_for_clusters

    sync_face_counts_for_clusters(big, list(range(7)))  # see the cluster split test
    total = big.execute("SELECT SUM(face_count) FROM photos").fetchone()[0]
    assert total == N + N // 2


def test_rows_in_groups_stay_whole_across_batches(big, monkeypatch):
    from bpp.db import sql_batches

    monkeypatch.setattr(sql_batches, "SQL_BATCH_SIZE", 3)
    rows = sql_batches.rows_in(
        big,
        "SELECT photo_id, COUNT(*) FROM face_embeddings WHERE photo_id IN ({ph}) "
        "AND cluster_id != ? GROUP BY photo_id",
        [5, 1, 2, 2, 4, 3, 6],
        [-2],
    )
    assert dict(rows) == {1: 1, 2: 2, 3: 1, 4: 2, 5: 1, 6: 2}


def test_face_counts_stay_right_when_clusters_span_batches(big, monkeypatch):
    from bpp.db import sql_batches
    from bpp.db.face_cluster_ops import sync_face_counts_for_clusters

    monkeypatch.setattr(sql_batches, "SQL_BATCH_SIZE", 2)  # clusters split over batches
    big.execute("UPDATE face_embeddings SET cluster_id = face_index")  # a photo in 0 and 1
    sync_face_counts_for_clusters(big, [0, 1])
    total = big.execute("SELECT SUM(face_count) FROM photos").fetchone()[0]
    assert total == N + N // 2


def test_a_purge_with_ids_of_mixed_types_is_not_a_crash(big):
    from bpp.db.sql_batches import rows_in

    rows = rows_in(big, "SELECT id FROM photos WHERE id IN ({ph})", ["12", 13, 13])
    assert sorted(r[0] for r in rows) == [12, 13]

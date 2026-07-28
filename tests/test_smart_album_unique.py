"""Regression tests: no duplicate smart albums under concurrent refresh.

`_ensure_smart_album` used a check-then-insert with no DB-level guard, so
two `refresh_smart_albums` calls on separate WAL connections (the
dismiss/merge face handlers under the face lock vs. an unlocked background
phash/clustering refresh) could each pass the existence check and insert
the same person album twice. That surfaced as the flaky
`tests/test_web.py::TestFaceRoutes::test_dismiss_cluster` failure
(`assert 2 == 1`) under CI's parallel test job, once the OpenCV-5 crash
stopped masking it (CI 2026-07-28).

The fix (schema v44): a UNIQUE index on `albums(album_type, rule_json)`
plus IntegrityError handling in `_ensure_smart_album` so the race-loser
adopts the winner instead of duplicating.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

import bpp.db.smart_album_ensure as sae
from bpp.db.albums import create_album
from bpp.db.connection import init_db
from bpp.db.migrate import get_version
from bpp.db.schema import create_tables
from bpp.db.smart_album_ensure import _ensure_smart_album


@pytest.fixture()
def conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    yield c
    c.close()


def test_albums_type_rule_unique_constraint(conn):
    """The DB itself must reject a second album with the same
    (album_type, rule_json) — this is what makes the '2 == 1' duplicate
    structurally impossible regardless of concurrency."""
    create_album(conn, "Person 1", album_type="smart_person", rule={"cluster_id": 0})
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        create_album(conn, "Person 1 dup", album_type="smart_person", rule={"cluster_id": 0})
    conn.rollback()

    # Manual albums carry NULL rule_json; SQLite treats NULLs as distinct,
    # so multiple manual albums must still be allowed.
    create_album(conn, "Trip A", album_type="manual")
    create_album(conn, "Trip B", album_type="manual")
    conn.commit()
    n_manual = conn.execute("SELECT COUNT(*) FROM albums WHERE album_type='manual'").fetchone()[0]
    assert n_manual == 2, "manual albums (NULL rule_json) must not collide"


def test_ensure_smart_album_adopts_race_winner(conn, tmp_path, monkeypatch):
    """When a concurrent refresh creates the album between our existence
    check and our INSERT, `_ensure_smart_album` must adopt the winner's row
    (catch the IntegrityError and re-select) rather than raising or
    duplicating."""
    db_path = str(tmp_path / "test.db")
    rule = {"cluster_id": 0}
    rule_json = json.dumps(rule, sort_keys=True)

    # A side connection stands in for the concurrent background refresh: it
    # inserts + commits the same album, then the real INSERT conflicts.
    side = sqlite3.connect(db_path)

    def racing_create_album(c, name, album_type, rule, **kwargs):
        side.execute(
            "INSERT INTO albums(name, album_type, rule_json) VALUES(?, ?, ?)",
            (name, album_type, rule_json),
        )
        side.commit()
        raise sqlite3.IntegrityError(
            "UNIQUE constraint failed: albums.album_type, albums.rule_json"
        )

    monkeypatch.setattr(sae, "create_album", racing_create_album)

    album_id = _ensure_smart_album(
        conn, name="Person 1", album_type="smart_person", rule=rule, photo_ids=[]
    )
    side.close()

    assert album_id is not None, "must adopt the winner's id, not raise"
    winner = conn.execute(
        "SELECT id FROM albums WHERE album_type=? AND rule_json=?",
        ("smart_person", rule_json),
    ).fetchone()
    assert album_id == winner["id"]
    total = conn.execute("SELECT COUNT(*) FROM albums WHERE album_type='smart_person'").fetchone()[
        0
    ]
    assert total == 1, f"expected exactly one person album after the race, got {total}"


def test_migration_v44_dedupes_duplicate_person_albums(tmp_path):
    """A pre-v44 DB carrying the duplicate-album bug must be repaired on
    upgrade: duplicates collapsed to one (lowest id kept, membership
    preserved) and the index promoted to UNIQUE."""
    db_path = str(tmp_path / "old.db")
    c = sqlite3.connect(db_path)
    create_tables(c)  # fresh v44

    # Rewind to the v43 shape: non-unique index, version 43.
    c.execute("DROP INDEX idx_albums_type_rule")
    c.execute("CREATE INDEX idx_albums_type_rule ON albums(album_type, rule_json)")
    c.execute("PRAGMA user_version = 43")
    c.execute(
        "INSERT INTO photos(id, filepath, original_filename, file_size, file_mtime) "
        "VALUES (1, '/x/a.jpg', 'a.jpg', 100, 1000.0)"
    )
    for name in ("Person 1", "Person 1 DUP"):
        cur = c.execute(
            "INSERT INTO albums(name, album_type, rule_json) VALUES(?, 'smart_person', ?)",
            (name, '{"cluster_id": 0}'),
        )
        c.execute("INSERT INTO album_photos(album_id, photo_id) VALUES(?, 1)", (cur.lastrowid,))
    c.commit()
    assert (
        c.execute("SELECT COUNT(*) FROM albums WHERE album_type='smart_person'").fetchone()[0] == 2
    )

    create_tables(c)  # runs the v43 -> v44 migration

    assert get_version(c) == 44
    n_albums = c.execute("SELECT COUNT(*) FROM albums WHERE album_type='smart_person'").fetchone()[
        0
    ]
    n_members = c.execute("SELECT COUNT(*) FROM album_photos").fetchone()[0]
    idx_sql = c.execute(
        "SELECT sql FROM sqlite_master WHERE name='idx_albums_type_rule'"
    ).fetchone()[0]
    assert n_albums == 1, "duplicate person albums must be collapsed to one"
    assert n_members == 1, "the surviving album keeps its membership; the dup's is removed"
    assert "UNIQUE" in idx_sql, "index must be promoted to UNIQUE"
    c.close()

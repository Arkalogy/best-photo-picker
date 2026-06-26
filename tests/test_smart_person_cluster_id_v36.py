"""P5/v36 — smart_person_cluster_id shadow column + index.

The v36 migration adds an indexed shadow column populated from the
``cluster_id`` field of smart_person albums' ``rule_json``. Until v36,
the cluster→album lookup ran a full table scan + json_extract per row;
v36 makes it an O(log N) probe.

Tests verify:

* The migration adds the column idempotently.
* Backfill populates the column from rule_json for existing
  smart_person rows.
* Malformed rule_json doesn't crash the migration (logged + skipped).
* The partial index exists after migration.
* The four production reader sites
  (``bp_faces_photo``, three in ``bp_faces_bbox``) use the indexed
  column, not the JSON extract pattern (a source-scan gate).
* Writers (``create_album``, ``_ensure_smart_album`` via the
  refresh path) populate the column atomically with the row INSERT.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from bpp.db.albums import create_album
from bpp.db.migrations_recent import _migrate_v36

# ── Migration v36 ──


@pytest.fixture
def conn():
    """Fresh in-memory DB at v35 schema — i.e. without the
    smart_person_cluster_id column. We hand-roll the minimum to test
    the migration in isolation."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        "CREATE TABLE albums ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " name TEXT NOT NULL,"
        " album_type TEXT DEFAULT 'manual',"
        " rule_json TEXT,"
        " config_json TEXT,"
        " k INTEGER DEFAULT 50,"
        " parent_id INTEGER,"
        " created_at TEXT DEFAULT (datetime('now')),"
        " modified_at TEXT DEFAULT (datetime('now'))"
        ")"
    )
    c.commit()
    yield c
    c.close()


class TestMigrationV36:
    def test_adds_column_when_absent(self, conn):
        _migrate_v36(conn)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(albums)")}
        assert "smart_person_cluster_id" in cols

    def test_idempotent_when_column_already_exists(self, conn):
        conn.execute("ALTER TABLE albums ADD COLUMN smart_person_cluster_id INTEGER")
        conn.commit()
        # Must not raise on re-apply.
        _migrate_v36(conn)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(albums)")}
        assert "smart_person_cluster_id" in cols

    def test_backfills_from_rule_json(self, conn):
        conn.execute(
            "INSERT INTO albums (name, album_type, rule_json) VALUES (?, 'smart_person', ?)",
            ("Alice", json.dumps({"cluster_id": 42})),
        )
        conn.execute(
            "INSERT INTO albums (name, album_type, rule_json) VALUES (?, 'smart_person', ?)",
            ("Bob", json.dumps({"cluster_id": 99})),
        )
        conn.commit()
        _migrate_v36(conn)
        rows = conn.execute(
            "SELECT name, smart_person_cluster_id FROM albums ORDER BY name"
        ).fetchall()
        result = {r["name"]: r["smart_person_cluster_id"] for r in rows}
        assert result == {"Alice": 42, "Bob": 99}

    def test_skips_non_smart_person_rows(self, conn):
        conn.execute(
            "INSERT INTO albums (name, album_type, rule_json) VALUES (?, 'manual', ?)",
            ("My album", json.dumps({"cluster_id": 7})),
        )
        conn.commit()
        _migrate_v36(conn)
        row = conn.execute(
            "SELECT smart_person_cluster_id FROM albums WHERE name='My album'"
        ).fetchone()
        # Manual albums don't get the shadow column populated even if
        # their rule_json happens to contain a cluster_id key.
        assert row["smart_person_cluster_id"] is None

    def test_skips_malformed_rule_json(self, conn, caplog):
        import logging

        conn.execute(
            "INSERT INTO albums (name, album_type, rule_json) VALUES (?, 'smart_person', ?)",
            ("BadJson", "not-valid-json {"),
        )
        conn.execute(
            "INSERT INTO albums (name, album_type, rule_json) VALUES (?, 'smart_person', ?)",
            ("EmptyJson", None),
        )
        conn.execute(
            "INSERT INTO albums (name, album_type, rule_json) VALUES (?, 'smart_person', ?)",
            ("Good", json.dumps({"cluster_id": 5})),
        )
        conn.commit()
        with caplog.at_level(logging.WARNING, logger="bpp.db.migrations_recent"):
            _migrate_v36(conn)
        results = {
            r["name"]: r["smart_person_cluster_id"]
            for r in conn.execute("SELECT name, smart_person_cluster_id FROM albums").fetchall()
        }
        assert results == {"BadJson": None, "EmptyJson": None, "Good": 5}
        # Malformed json must be logged at WARNING.
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("malformed" in r.getMessage().lower() for r in warnings)

    def test_creates_partial_index(self, conn):
        _migrate_v36(conn)
        # Index lives in sqlite_master.
        idx = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND name='idx_albums_smart_person_cluster'"
        ).fetchone()
        assert idx is not None

    def test_non_int_cluster_id_skipped(self, conn):
        conn.execute(
            "INSERT INTO albums (name, album_type, rule_json) VALUES (?, 'smart_person', ?)",
            ("StringCid", json.dumps({"cluster_id": "5"})),
        )
        conn.commit()
        _migrate_v36(conn)
        row = conn.execute(
            "SELECT smart_person_cluster_id FROM albums WHERE name='StringCid'"
        ).fetchone()
        # Non-int cluster_id values are skipped — the column type is
        # INTEGER and the production writers always send ints.
        assert row["smart_person_cluster_id"] is None


# ── create_album writer ──


@pytest.fixture
def conn_v36():
    """DB already migrated to v36."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        "CREATE TABLE albums ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " name TEXT NOT NULL,"
        " album_type TEXT DEFAULT 'manual',"
        " rule_json TEXT,"
        " config_json TEXT,"
        " k INTEGER DEFAULT 50,"
        " parent_id INTEGER,"
        " created_at TEXT DEFAULT (datetime('now')),"
        " modified_at TEXT DEFAULT (datetime('now')),"
        " smart_person_cluster_id INTEGER"
        ")"
    )
    c.commit()
    yield c
    c.close()


class TestCreateAlbumWriter:
    def test_smart_person_with_cluster_id_populates_column(self, conn_v36):
        album_id = create_album(
            conn_v36,
            name="Alice",
            album_type="smart_person",
            rule={"cluster_id": 42},
        )
        row = conn_v36.execute(
            "SELECT smart_person_cluster_id FROM albums WHERE id=?",
            (album_id,),
        ).fetchone()
        assert row["smart_person_cluster_id"] == 42

    def test_manual_album_leaves_column_null(self, conn_v36):
        album_id = create_album(
            conn_v36,
            name="My album",
            album_type="manual",
            rule={"cluster_id": 42},  # ignored — album_type isn't smart_person
        )
        row = conn_v36.execute(
            "SELECT smart_person_cluster_id FROM albums WHERE id=?",
            (album_id,),
        ).fetchone()
        assert row["smart_person_cluster_id"] is None

    def test_smart_person_without_rule_leaves_column_null(self, conn_v36):
        album_id = create_album(
            conn_v36,
            name="Bare",
            album_type="smart_person",
            rule=None,
        )
        row = conn_v36.execute(
            "SELECT smart_person_cluster_id FROM albums WHERE id=?",
            (album_id,),
        ).fetchone()
        assert row["smart_person_cluster_id"] is None

    def test_smart_person_with_non_int_cluster_id_leaves_column_null(self, conn_v36):
        album_id = create_album(
            conn_v36,
            name="StringCid",
            album_type="smart_person",
            rule={"cluster_id": "5"},
        )
        row = conn_v36.execute(
            "SELECT smart_person_cluster_id FROM albums WHERE id=?",
            (album_id,),
        ).fetchone()
        assert row["smart_person_cluster_id"] is None


# ── Source-scan gates ──


class TestV38Triggers:
    """v38 — smart_person_cluster_id auto-populates from rule_json on
    INSERT/UPDATE via triggers. Without the triggers, every writer
    has to remember to set the shadow column; v38 makes the column
    self-maintaining."""

    @pytest.fixture
    def conn(self):
        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        c.execute(
            "CREATE TABLE albums ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " name TEXT NOT NULL,"
            " album_type TEXT DEFAULT 'manual',"
            " rule_json TEXT,"
            " config_json TEXT,"
            " k INTEGER DEFAULT 50,"
            " parent_id INTEGER,"
            " created_at TEXT DEFAULT (datetime('now')),"
            " modified_at TEXT DEFAULT (datetime('now')),"
            " smart_person_cluster_id INTEGER"
            ")"
        )
        # Apply v38 (which creates the triggers).
        from bpp.db.migrations_recent import _migrate_v38

        _migrate_v38(c)
        c.commit()
        yield c
        c.close()

    def test_raw_insert_populates_shadow_column(self, conn):
        """An INSERT without smart_person_cluster_id but with
        rule_json carrying cluster_id must end up with the column
        populated via the trigger."""
        conn.execute(
            "INSERT INTO albums (name, album_type, rule_json) VALUES (?, 'smart_person', ?)",
            ("Alice", json.dumps({"cluster_id": 42})),
        )
        conn.commit()
        row = conn.execute(
            "SELECT smart_person_cluster_id FROM albums WHERE name='Alice'"
        ).fetchone()
        assert row["smart_person_cluster_id"] == 42

    def test_raw_insert_non_smart_person_leaves_column_null(self, conn):
        conn.execute(
            "INSERT INTO albums (name, album_type, rule_json) VALUES (?, 'manual', ?)",
            ("Manual", json.dumps({"cluster_id": 7})),
        )
        conn.commit()
        row = conn.execute(
            "SELECT smart_person_cluster_id FROM albums WHERE name='Manual'"
        ).fetchone()
        # Manual albums don't get the shadow column populated.
        assert row["smart_person_cluster_id"] is None

    def test_update_rule_json_resyncs_shadow_column(self, conn):
        conn.execute(
            "INSERT INTO albums (name, album_type, rule_json) VALUES (?, 'smart_person', ?)",
            ("Alice", json.dumps({"cluster_id": 42})),
        )
        conn.commit()
        # Now update rule_json with a different cluster_id.
        conn.execute(
            "UPDATE albums SET rule_json=? WHERE name='Alice'",
            (json.dumps({"cluster_id": 99}),),
        )
        conn.commit()
        row = conn.execute(
            "SELECT smart_person_cluster_id FROM albums WHERE name='Alice'"
        ).fetchone()
        assert row["smart_person_cluster_id"] == 99

    def test_update_to_smart_person_type_populates_column(self, conn):
        """A manual album whose album_type changes to 'smart_person'
        gets its shadow column synced from its existing rule_json."""
        conn.execute(
            "INSERT INTO albums (name, album_type, rule_json) VALUES (?, 'manual', ?)",
            ("Foo", json.dumps({"cluster_id": 7})),
        )
        conn.commit()
        # Initially NULL (manual album).
        row = conn.execute("SELECT smart_person_cluster_id FROM albums WHERE name='Foo'").fetchone()
        assert row["smart_person_cluster_id"] is None

        # Convert to smart_person.
        conn.execute("UPDATE albums SET album_type='smart_person' WHERE name='Foo'")
        conn.commit()
        row = conn.execute("SELECT smart_person_cluster_id FROM albums WHERE name='Foo'").fetchone()
        assert row["smart_person_cluster_id"] == 7


class TestReaderSitesUseShadowColumn:
    """Every cluster→album lookup that previously used
    ``json_extract(rule_json, '$.cluster_id') = ?`` must now use
    ``smart_person_cluster_id = ?`` against the indexed column.

    Locked sites (P5 finding): bp_faces_photo, three in bp_faces_bbox.
    """

    @pytest.fixture
    def repo_root(self):
        return Path(__file__).resolve().parent.parent

    def test_bp_faces_photo_uses_shadow_column(self, repo_root):
        src = (repo_root / "bpp" / "web" / "bp_faces_photo.py").read_text()
        # Must contain at least one reference to the shadow column.
        assert "smart_person_cluster_id" in src
        # And must not contain the legacy json_extract-on-rule_json
        # pattern for cluster_id (the part this phase replaces).
        assert 'json_extract("rule_json", "$.cluster_id")' not in src
        assert 'json_extract("rule_json", "$.cluster_id")' not in src

    def test_bp_faces_bbox_uses_shadow_column(self, repo_root):
        # The three sites span two modules since the post-review decomp
        # split: the duplicate-guard lookup moved to face_create_helpers,
        # the two name-resolution lookups stay in bp_faces_bbox.
        bbox_src = (repo_root / "bpp" / "web" / "bp_faces_bbox.py").read_text()
        helper_src = (repo_root / "bpp" / "web" / "face_create_helpers.py").read_text()
        combined = bbox_src + helper_src
        assert combined.count("smart_person_cluster_id = ?") >= 3, (
            "the indexed shadow column must be used at all three lookup "
            "sites (duplicate-guard label, name resolution after split, "
            "name resolution after match) — counted across bp_faces_bbox.py "
            "+ face_create_helpers.py since the helpers moved out during "
            "the post-review decomposition"
        )

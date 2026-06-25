"""Tests for batch rename feature."""

from __future__ import annotations

import json
import os

import pytest

from bpp.db.batch_rename import (
    _journal_path,
    apply_rename,
    build_rename_map,
    parse_pattern,
    recover_interrupted_rename,
)
from bpp.db.connection import init_db


@pytest.fixture
def conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    c = init_db(db_path)
    yield c
    c.close()


def _insert(conn, filepath, date=None, score=0.5):
    fname = filepath.split("/")[-1] if filepath else "unknown"
    conn.execute(
        "INSERT INTO photos (filepath, sha256, date, date_month, aggregate_score, "
        "original_filename, file_size, file_mtime) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (filepath, "hash_" + fname, date, date[:7] if date else None, score, fname, 1000, 0.0),
    )
    conn.commit()
    return conn.execute("SELECT id FROM photos WHERE filepath=?", (filepath,)).fetchone()[0]


class TestParsePattern:
    """Test pattern string parsing into template tokens."""

    def test_simple_name(self):
        tokens = parse_pattern("{name}")
        assert tokens == [("var", "name")]

    def test_date_underscore_name(self):
        tokens = parse_pattern("{date}_{name}")
        assert tokens == [("var", "date"), ("lit", "_"), ("var", "name")]

    def test_counter(self):
        tokens = parse_pattern("{counter:3}_{name}")
        assert tokens == [("var", "counter:3"), ("lit", "_"), ("var", "name")]

    def test_literal_only(self):
        tokens = parse_pattern("photo")
        assert tokens == [("lit", "photo")]

    def test_mixed(self):
        tokens = parse_pattern("IMG_{year}-{month}_{name}")
        assert tokens == [
            ("lit", "IMG_"),
            ("var", "year"),
            ("lit", "-"),
            ("var", "month"),
            ("lit", "_"),
            ("var", "name"),
        ]

    def test_empty(self):
        tokens = parse_pattern("")
        assert tokens == []

    def test_unclosed_brace(self):
        """Unclosed brace should not crash — treat as literal."""
        tokens = parse_pattern("{date")
        # Should produce something without raising
        assert isinstance(tokens, list)

    def test_unclosed_brace_mid(self):
        """Unclosed brace after valid token should not crash."""
        tokens = parse_pattern("{name}_{date")
        assert tokens[0] == ("var", "name")
        assert len(tokens) >= 2


class TestBuildRenameMap:
    """Test generating rename mapping from pattern + photo list."""

    def test_name_only(self, conn):
        _insert(conn, "/lib/batch1/sunset.jpg", "2024-06-15 10:00:00")
        photos = [dict(r) for r in conn.execute("SELECT * FROM photos").fetchall()]
        mapping = build_rename_map(photos, "{name}")
        assert len(mapping) == 1
        # {name} = original name without extension
        assert mapping[0]["new_filename"] == "sunset.jpg"

    def test_date_name(self, conn):
        _insert(conn, "/lib/batch1/sunset.jpg", "2024-06-15 10:00:00")
        photos = [dict(r) for r in conn.execute("SELECT * FROM photos").fetchall()]
        mapping = build_rename_map(photos, "{date}_{name}")
        assert len(mapping) == 1
        assert mapping[0]["new_filename"] == "2024-06-15_sunset.jpg"

    def test_year_month_day(self, conn):
        _insert(conn, "/lib/batch1/sunset.jpg", "2024-06-15 10:00:00")
        photos = [dict(r) for r in conn.execute("SELECT * FROM photos").fetchall()]
        mapping = build_rename_map(photos, "{year}-{month}-{day}_{name}")
        assert len(mapping) == 1
        assert mapping[0]["new_filename"] == "2024-06-15_sunset.jpg"

    def test_counter(self, conn):
        _insert(conn, "/lib/batch1/a.jpg", "2024-06-15 10:00:00")
        _insert(conn, "/lib/batch1/b.jpg", "2024-06-16 10:00:00")
        photos = [dict(r) for r in conn.execute("SELECT * FROM photos ORDER BY date").fetchall()]
        mapping = build_rename_map(photos, "{counter:3}_{name}")
        assert len(mapping) == 2
        assert mapping[0]["new_filename"] == "001_a.jpg"
        assert mapping[1]["new_filename"] == "002_b.jpg"

    def test_no_date_fallback(self, conn):
        _insert(conn, "/lib/batch1/mystery.jpg")
        photos = [dict(r) for r in conn.execute("SELECT * FROM photos").fetchall()]
        mapping = build_rename_map(photos, "{date}_{name}")
        assert mapping[0]["new_filename"] == "unknown-date_mystery.jpg"

    def test_conflict_resolution(self, conn):
        _insert(conn, "/lib/batch1/a.jpg", "2024-06-15 10:00:00")
        _insert(conn, "/lib/batch1/b.jpg", "2024-06-15 10:00:00")
        rows = conn.execute("SELECT * FROM photos ORDER BY filepath").fetchall()
        photos = [dict(r) for r in rows]
        # Both produce same date, different names → no conflict
        mapping = build_rename_map(photos, "{date}_{name}")
        names = [m["new_filename"] for m in mapping]
        assert names[0] == "2024-06-15_a.jpg"
        assert names[1] == "2024-06-15_b.jpg"

    def test_duplicate_names_get_suffix(self, conn):
        """When pattern produces identical names, add numeric suffix."""
        _insert(conn, "/lib/batch1/a.jpg", "2024-06-15 10:00:00")
        _insert(conn, "/lib/batch1/b.jpg", "2024-06-15 10:00:00")
        rows = conn.execute("SELECT * FROM photos ORDER BY filepath").fetchall()
        photos = [dict(r) for r in rows]
        # Both produce same name with date-only pattern
        mapping = build_rename_map(photos, "{date}")
        names = [m["new_filename"] for m in mapping]
        assert names[0] == "2024-06-15.jpg"
        assert names[1] == "2024-06-15_2.jpg"

    def test_unchanged_skipped(self, conn):
        """Photos whose name doesn't change should be marked unchanged."""
        _insert(conn, "/lib/batch1/sunset.jpg", "2024-06-15 10:00:00")
        photos = [dict(r) for r in conn.execute("SELECT * FROM photos").fetchall()]
        mapping = build_rename_map(photos, "{name}")
        assert mapping[0]["changed"] is False

    def test_preserves_extension(self, conn):
        _insert(conn, "/lib/batch1/photo.PNG", "2024-06-15 10:00:00")
        photos = [dict(r) for r in conn.execute("SELECT * FROM photos").fetchall()]
        mapping = build_rename_map(photos, "{date}_{name}")
        assert mapping[0]["new_filename"].endswith(".PNG")


class TestApplyRename:
    """Test actual file + DB rename."""

    def test_rename_on_disk_and_db(self, conn, tmp_path):
        # Create real file
        photo_dir = tmp_path / "lib" / "batch1"
        photo_dir.mkdir(parents=True)
        old_path = photo_dir / "sunset.jpg"
        old_path.write_text("photo data")
        filepath = str(old_path)

        _insert(conn, filepath, "2024-06-15 10:00:00")
        photos = [dict(r) for r in conn.execute("SELECT * FROM photos").fetchall()]
        mapping = build_rename_map(photos, "{date}_{name}")

        results = apply_rename(conn, mapping)
        assert len(results) == 1
        assert results[0]["success"] is True

        # Verify file moved
        new_path = photo_dir / "2024-06-15_sunset.jpg"
        assert new_path.exists()
        assert not old_path.exists()

        # Verify DB updated
        row = conn.execute("SELECT filepath FROM photos WHERE id=?", (photos[0]["id"],)).fetchone()
        assert row[0] == str(new_path)

    def test_skip_unchanged(self, conn, tmp_path):
        photo_dir = tmp_path / "lib" / "batch1"
        photo_dir.mkdir(parents=True)
        old_path = photo_dir / "sunset.jpg"
        old_path.write_text("photo data")

        _insert(conn, str(old_path), "2024-06-15 10:00:00")
        photos = [dict(r) for r in conn.execute("SELECT * FROM photos").fetchall()]
        mapping = build_rename_map(photos, "{name}")

        results = apply_rename(conn, mapping)
        assert len(results) == 0  # Nothing changed

    def test_missing_file_skipped(self, conn):
        _insert(conn, "/nonexistent/photo.jpg", "2024-06-15 10:00:00")
        photos = [dict(r) for r in conn.execute("SELECT * FROM photos").fetchall()]
        mapping = build_rename_map(photos, "{date}_{name}")

        results = apply_rename(conn, mapping)
        assert len(results) == 1
        assert results[0]["success"] is False
        assert "not found" in results[0]["error"].lower()

    def test_path_traversal_blocked(self, conn, tmp_path):
        """Paths outside library_path must be rejected."""
        lib = tmp_path / "library"
        lib.mkdir()
        photo = lib / "photo.jpg"
        photo.write_text("data")

        _insert(conn, str(photo), "2024-06-15 10:00:00")
        # Craft a mapping that tries to escape the library
        mapping = [
            {
                "id": 1,
                "old_filepath": str(photo),
                "new_filepath": str(tmp_path / ".." / "evil.jpg"),
                "new_filename": "evil.jpg",
                "changed": True,
            }
        ]
        results = apply_rename(conn, mapping, library_path=str(lib))
        assert len(results) == 1
        assert results[0]["success"] is False
        assert "outside" in results[0]["error"].lower()
        # Original file untouched
        assert photo.exists()

    def test_single_commit_at_end(self, conn, tmp_path):
        """All DB updates should commit once, not per-file."""
        lib = tmp_path / "library"
        lib.mkdir()
        (lib / "a.jpg").write_text("a")
        (lib / "b.jpg").write_text("b")

        _insert(conn, str(lib / "a.jpg"), "2024-06-15 10:00:00")
        _insert(conn, str(lib / "b.jpg"), "2024-06-16 10:00:00")
        photos = [dict(r) for r in conn.execute("SELECT * FROM photos ORDER BY date").fetchall()]
        mapping = build_rename_map(photos, "{date}_{name}")

        results = apply_rename(conn, mapping, library_path=str(lib))
        assert all(r["success"] for r in results)
        # Verify both DB records updated
        rows = conn.execute("SELECT filepath FROM photos ORDER BY date").fetchall()
        assert "2024-06-15_a.jpg" in rows[0][0]
        assert "2024-06-16_b.jpg" in rows[1][0]

    def test_path_within_library_allowed(self, conn, tmp_path):
        """Renames within library_path should succeed."""
        lib = tmp_path / "library"
        lib.mkdir()
        photo = lib / "photo.jpg"
        photo.write_text("data")
        filepath = str(photo)

        _insert(conn, filepath, "2024-06-15 10:00:00")
        photos = [dict(r) for r in conn.execute("SELECT * FROM photos").fetchall()]
        mapping = build_rename_map(photos, "{date}_{name}")

        results = apply_rename(conn, mapping, library_path=str(lib))
        assert len(results) == 1
        assert results[0]["success"] is True


class TestBatchRenameBlueprint:
    """Test batch rename API endpoints."""

    @pytest.fixture
    def client(self, tmp_path):
        from bpp.web.app import create_app

        app = create_app(library_path=str(tmp_path))
        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c

    def test_preview(self, client):
        resp = client.post(
            "/api/v1/batch/rename/preview",
            json={"pattern": "{date}_{name}", "photo_ids": []},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "mapping" in data

    def test_preview_missing_pattern(self, client):
        resp = client.post("/api/v1/batch/rename/preview", json={})
        assert resp.status_code == 400

    def test_preview_pattern_too_long(self, client):
        resp = client.post(
            "/api/v1/batch/rename/preview",
            json={"pattern": "x" * 1001, "photo_ids": []},
        )
        assert resp.status_code == 400

    def test_apply(self, client):
        resp = client.post(
            "/api/v1/batch/rename/apply",
            json={"mapping": []},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "results" in data


class TestRenameJournal:
    """Tests for the rename undo journal (crash recovery)."""

    @pytest.fixture
    def lib(self, tmp_path):
        lib = tmp_path / "library"
        lib.mkdir()
        (lib / "data").mkdir()
        return lib

    @pytest.fixture
    def conn(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        c = init_db(db_path)
        yield c
        c.close()

    def test_journal_written_and_removed_on_success(self, conn, lib):
        """Journal is created before renames and deleted after commit."""
        photo = lib / "photo.jpg"
        photo.write_text("data")
        filepath = str(photo)

        _insert(conn, filepath, "2024-06-15 10:00:00")
        photos = [dict(r) for r in conn.execute("SELECT * FROM photos").fetchall()]
        mapping = build_rename_map(photos, "{date}_{name}")

        jp = _journal_path(str(lib))
        assert not os.path.exists(jp)

        results = apply_rename(conn, mapping, library_path=str(lib))
        assert any(r["success"] for r in results)
        # Journal should be cleaned up
        assert not os.path.exists(jp)

    def test_recover_reverts_disk_renames(self, conn, lib):
        """Recovery reverts files renamed on disk but not committed to DB."""
        old_file = lib / "original.jpg"
        old_file.write_text("data")
        old_path = str(old_file)
        new_path = str(lib / "renamed.jpg")

        _insert(conn, old_path, "2024-06-15 10:00:00")

        # Simulate crash: file renamed on disk, journal exists, DB not updated
        os.rename(old_path, new_path)
        journal = [{"old": old_path, "new": new_path, "id": 1}]
        jp = _journal_path(str(lib))
        with open(jp, "w") as f:
            json.dump(journal, f)

        reverted = recover_interrupted_rename(conn, str(lib))
        assert len(reverted) == 1
        assert os.path.exists(old_path)
        assert not os.path.exists(new_path)
        # Journal cleaned up
        assert not os.path.exists(jp)

    def test_recover_noop_when_no_journal(self, conn, lib):
        """No journal file means nothing to recover."""
        reverted = recover_interrupted_rename(conn, str(lib))
        assert reverted == []

    def test_recover_skips_already_consistent(self, conn, lib):
        """If old_path still exists, no revert needed."""
        old_file = lib / "original.jpg"
        old_file.write_text("data")
        old_path = str(old_file)
        new_path = str(lib / "renamed.jpg")

        journal = [{"old": old_path, "new": new_path, "id": 1}]
        jp = _journal_path(str(lib))
        with open(jp, "w") as f:
            json.dump(journal, f)

        reverted = recover_interrupted_rename(conn, str(lib))
        # old_path exists, so no revert needed
        assert reverted == []
        assert not os.path.exists(jp)

    def test_recover_corrupt_journal(self, conn, lib):
        """Corrupt journal is removed without error."""
        jp = _journal_path(str(lib))
        with open(jp, "w") as f:
            f.write("NOT VALID JSON{{{")

        reverted = recover_interrupted_rename(conn, str(lib))
        assert reverted == []
        assert not os.path.exists(jp)

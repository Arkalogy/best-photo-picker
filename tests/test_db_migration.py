"""Tests for the bpp.db package — part 2 of 3: TestMigration, TestLibraryImport."""

# Shared fixtures are imported by name and re-bound as test parameters.
# ruff: noqa: F811

from __future__ import annotations

import json
import sqlite3

import pytest

from bpp.db.migrate import (
    get_version,
    import_from_analysis_json,
    import_presets_from_json,
    migrate,
)
from bpp.db.photos import (
    get_photo_by_path,
    get_photo_count,
    upsert_photo,
)
from bpp.db.presets import (
    load_preset,
)
from bpp.db.schema import SCHEMA_VERSION
from tests._db_shared import conn, db_path, sample_image, sample_photos  # noqa: F401

# --- Migration tests ---


class TestMigration:
    def test_import_analysis_json(self, conn, tmp_path, sample_photos):
        data = [
            {
                "filepath": fp,
                "date": "2024-06-15T12:00:00",
                "date_day": "2024-06-15",
                "date_month": "2024-06",
                "blur_raw": 120.0 + i,
                "exposure_score": 0.7,
                "face_score": 0.5,
                "composition_score": 0.6,
            }
            for i, fp in enumerate(sample_photos)
        ]
        json_path = str(tmp_path / "analysis.json")
        with open(json_path, "w") as f:
            json.dump(data, f)

        count = import_from_analysis_json(conn, json_path)
        assert count == 5
        assert get_photo_count(conn) == 5

        photo = get_photo_by_path(conn, sample_photos[0])
        assert photo["blur_raw"] == 120.0
        assert photo["date_day"] == "2024-06-15"

    def test_import_presets_json(self, conn, tmp_path):
        presets = {
            "Sharp": {"blur_weight": 0.5},
            "Faces": {"face_weight": 0.8},
        }
        json_path = str(tmp_path / "presets.json")
        with open(json_path, "w") as f:
            json.dump(presets, f)

        count = import_presets_from_json(conn, json_path)
        assert count == 2
        assert load_preset(conn, "Sharp") == {"blur_weight": 0.5}

    def test_import_face_embeddings(self, conn, sample_image, tmp_path):
        # Create a photo in the new DB
        upsert_photo(conn, {"filepath": sample_image})

        # Create old-style face DB
        old_db_path = str(tmp_path / "old_cache.db")
        old_conn = sqlite3.connect(old_db_path)
        old_conn.execute("""
            CREATE TABLE face_embeddings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filepath TEXT NOT NULL,
                file_size INTEGER, file_mtime REAL,
                face_index INTEGER,
                bbox_x INTEGER, bbox_y INTEGER, bbox_w INTEGER, bbox_h INTEGER,
                embedding BLOB NOT NULL,
                cluster_id INTEGER DEFAULT -1
            )
        """)
        old_conn.execute(
            "INSERT INTO face_embeddings "
            "(filepath, file_size, file_mtime, face_index, "
            "bbox_x, bbox_y, bbox_w, bbox_h, embedding, cluster_id) "
            "VALUES (?, 100, 1000.0, 0, 10, 20, 50, 50, ?, 3)",
            (sample_image, b"\x00" * 128),
        )
        old_conn.commit()
        old_conn.close()

        from bpp.db.migrate import import_face_embeddings

        count = import_face_embeddings(conn, old_db_path)
        assert count == 1

        row = conn.execute(
            "SELECT photo_id, face_index, cluster_id FROM face_embeddings"
        ).fetchone()
        assert row is not None
        assert row[1] == 0  # face_index
        assert row[2] == 3  # cluster_id

    def test_migrate_idempotent(self, conn):
        migrate(conn)
        migrate(conn)
        assert get_version(conn) == SCHEMA_VERSION

    def test_migrate_savepoint_rollback(self, db_path):
        """A failed migration step rolls back via savepoint and leaves
        user_version at the last successful step."""
        from unittest.mock import patch

        from bpp.db.schema import _migrate

        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        # Create tables at v0 (no migration yet)
        from bpp.db.schema import TABLES_SQL

        conn.executescript(TABLES_SQL)
        conn.execute("PRAGMA user_version = 0")
        conn.commit()

        # Patch _backfill_exif_json (called in v18 step) to raise
        with (
            patch("bpp.db.schema._backfill_exif_json", side_effect=RuntimeError("boom")),
            pytest.raises(RuntimeError, match="boom"),
        ):
            _migrate(conn)

        # Version should have advanced up to v16 (the step before v18)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == 16

        # v18 changes should be rolled back — the step itself is a no-op
        # for fresh schemas but the version should NOT be 18
        assert version < 18

        conn.close()

    def test_migrate_incremental_version(self, db_path):
        """Each migration step bumps user_version incrementally."""
        from bpp.db.schema import TABLES_SQL, _migrate

        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(TABLES_SQL)
        conn.execute("PRAGMA user_version = 0")
        conn.commit()

        _migrate(conn)

        # Should end at the highest migration step
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == SCHEMA_VERSION
        conn.close()

    def test_import_nonexistent_json(self, conn):
        count = import_from_analysis_json(conn, "/nonexistent/analysis.json")
        assert count == 0


# --- Library import tests ---


class TestLibraryImport:
    def test_import_folder(self, conn, tmp_path):
        from bpp.db.library import import_folder

        # Create source folder with images
        source = tmp_path / "source"
        source.mkdir()
        for i in range(3):
            (source / f"photo_{i}.jpg").write_bytes(b"\xff\xd8\xff\xe0" + bytes([i]) * 200)

        library = tmp_path / "library"
        library.mkdir()

        result = import_folder(conn, str(source), str(library))
        assert result.imported == 3
        assert result.skipped == 0
        assert result.errors == 0
        assert result.batch_name == "source"
        assert len(result.imported_paths) == 3
        assert get_photo_count(conn) == 3

    def test_import_dedup_skips_existing(self, conn, tmp_path):
        from bpp.db.library import import_folder

        source = tmp_path / "source"
        source.mkdir()
        (source / "photo.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\xab" * 200)

        library = tmp_path / "library"
        library.mkdir()

        # First import
        result1 = import_folder(conn, str(source), str(library))
        assert result1.imported == 1

        # Second import — same content, should be skipped
        result2 = import_folder(conn, str(source), str(library))
        assert result2.imported == 0
        assert result2.skipped == 1
        assert get_photo_count(conn) == 1

    def test_import_preserves_filenames(self, conn, tmp_path):
        from bpp.db.library import import_folder

        source = tmp_path / "source"
        source.mkdir()
        (source / "vacation_sunset.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 200)

        library = tmp_path / "library"
        library.mkdir()

        result = import_folder(conn, str(source), str(library))
        assert result.imported == 1
        # Check the file exists in library/photos/source/
        assert (library / "photos" / "source" / "vacation_sunset.jpg").exists()

        photo = get_photo_by_path(conn, result.imported_paths[0])
        assert photo["original_filename"] == "vacation_sunset.jpg"
        assert photo["import_batch"] == "source"
        assert photo["sha256"] is not None

    def test_import_custom_batch_name(self, conn, tmp_path):
        from bpp.db.library import import_folder

        source = tmp_path / "source"
        source.mkdir()
        (source / "img.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x01" * 200)

        library = tmp_path / "library"
        library.mkdir()

        result = import_folder(conn, str(source), str(library), batch_name="hawaii_2024")
        assert result.batch_name == "hawaii_2024"
        assert (library / "photos" / "hawaii_2024" / "img.jpg").exists()

    def test_import_filename_collision(self, conn, tmp_path):
        from bpp.db.library import import_folder

        source1 = tmp_path / "source1"
        source1.mkdir()
        (source1 / "photo.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\xaa" * 200)

        source2 = tmp_path / "source2"
        source2.mkdir()
        (source2 / "photo.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\xbb" * 200)

        library = tmp_path / "library"
        library.mkdir()

        # Import both into same batch
        import_folder(conn, str(source1), str(library), batch_name="trip")
        result2 = import_folder(conn, str(source2), str(library), batch_name="trip")

        assert result2.imported == 1
        # Should have renamed to avoid collision
        assert (library / "photos" / "trip" / "photo.jpg").exists()
        assert (library / "photos" / "trip" / "photo_1.jpg").exists()

    def test_check_missing_files(self, conn, tmp_path):
        from bpp.db.library import check_missing_files

        # Create a file, import it, then delete it
        img = tmp_path / "temp_img.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
        upsert_photo(conn, {"filepath": str(img)})
        assert get_photo_count(conn) == 1

        img.unlink()
        missing = check_missing_files(conn)
        assert str(img) in missing
        # missing excluded by default
        assert get_photo_count(conn, include_missing=True) == 1

    def test_import_progress_callback(self, conn, tmp_path):
        from bpp.db.library import import_folder

        source = tmp_path / "source"
        source.mkdir()
        for i in range(3):
            (source / f"img_{i}.jpg").write_bytes(b"\xff\xd8\xff\xe0" + bytes([i]) * 200)

        library = tmp_path / "library"
        library.mkdir()

        progress_log = []

        def on_progress(current, total, filename, status):
            progress_log.append((current, total, filename, status))

        import_folder(conn, str(source), str(library), on_progress=on_progress)
        assert len(progress_log) == 3
        assert all(p[1] == 3 for p in progress_log), (
            f"every progress event should report total=3; got totals {[p[1] for p in progress_log]}"
        )
        assert all(p[3] == "imported" for p in progress_log), (
            f"every event status should be 'imported'; got {[p[3] for p in progress_log]}"
        )

    def test_backfill_sha256(self, conn, tmp_path):
        from bpp.db.library import backfill_sha256
        from bpp.db.photos import upsert_photo

        # Create real files and insert photos WITHOUT sha256
        photos_dir = tmp_path / "photos"
        photos_dir.mkdir()
        for i in range(3):
            fp = photos_dir / f"img_{i}.jpg"
            fp.write_bytes(b"\xff\xd8\xff\xe0" + bytes([i]) * 200)
            upsert_photo(conn, {"filepath": str(fp)})
        # Clear sha256 to simulate legacy data
        conn.execute("UPDATE photos SET sha256 = NULL")
        conn.commit()

        # Verify all are NULL
        null_count = conn.execute("SELECT COUNT(*) FROM photos WHERE sha256 IS NULL").fetchone()[0]
        assert null_count == 3

        updated = backfill_sha256(conn)
        assert updated == 3

        # All should now have sha256
        null_after = conn.execute("SELECT COUNT(*) FROM photos WHERE sha256 IS NULL").fetchone()[0]
        assert null_after == 0

        # SHA-256 values should be valid hex strings
        hashes = [r[0] for r in conn.execute("SELECT sha256 FROM photos").fetchall()]
        assert all(len(h) == 64 for h in hashes), (
            f"all sha256 values should be 64 hex chars; got lengths {[len(h) for h in hashes]}"
        )
        assert len(set(hashes)) == 3  # all unique

    def test_backfill_sha256_no_open_txn_during_hashing(self, conn, tmp_path, monkeypatch):
        """Regression: the slow per-file hashing must NOT run inside an open
        write transaction. The old version held the write lock across ~200
        file reads between commits; on a 6k-photo library that exceeded the
        30s busy_timeout and made a concurrent foreground write (a person
        rename's smart-album refresh) fail with 'database is locked'."""
        import bpp.db.library as lib
        from bpp.db.library import backfill_sha256
        from bpp.db.photos import upsert_photo

        photos_dir = tmp_path / "photos"
        photos_dir.mkdir()
        for i in range(5):
            fp = photos_dir / f"img_{i}.jpg"
            fp.write_bytes(b"\xff\xd8\xff\xe0" + bytes([i]) * 200)
            upsert_photo(conn, {"filepath": str(fp)})
        conn.execute("UPDATE photos SET sha256 = NULL")
        conn.commit()

        real_hash = lib._sha256_file
        txn_states: list[bool] = []

        def _spy(path):
            # Capture whether a write transaction is open at hash time.
            txn_states.append(conn.in_transaction)
            return real_hash(path)

        monkeypatch.setattr(lib, "_sha256_file", _spy)
        backfill_sha256(conn)

        assert txn_states, "hashing should have run for the seeded files"
        assert not any(txn_states), (
            "every file must be hashed with NO open write transaction so the "
            "backfill never holds the write lock across slow file reads; "
            f"in_transaction at each hash = {txn_states}"
        )

    def test_backfill_sha256_skips_missing_files(self, conn, tmp_path):
        from bpp.db.library import backfill_sha256
        from bpp.db.photos import upsert_photo

        # Insert photo, then delete file and clear sha256
        fp = tmp_path / "will_delete.jpg"
        fp.write_bytes(b"\xff\xd8\xff\xe0" + b"\xab" * 200)
        upsert_photo(conn, {"filepath": str(fp)})
        fp.unlink()
        conn.execute("UPDATE photos SET sha256 = NULL")
        conn.commit()

        updated = backfill_sha256(conn)
        assert updated == 0

    def test_backfill_sha256_noop_when_all_hashed(self, conn, tmp_path):
        from bpp.db.library import backfill_sha256
        from bpp.db.photos import upsert_photo

        fp = tmp_path / "img.jpg"
        fp.write_bytes(b"\xff\xd8\xff\xe0" + b"\xab" * 200)
        upsert_photo(conn, {"filepath": str(fp), "sha256": "abc123"})

        updated = backfill_sha256(conn)
        assert updated == 0

"""Tests for the bpp.db package."""

from __future__ import annotations

import json
import sqlite3

import pytest

from bpp.db.albums import (
    add_photos_to_album,
    create_album,
    delete_album,
    ensure_all_photos_album,
    get_album,
    get_album_photos,
    list_albums,
    set_album_selection,
    set_favorites_bulk,
    set_override,
    set_overrides_bulk,
    sync_all_photos_album,
    toggle_favorite,
    update_album,
)
from bpp.db.connection import get_db, init_db
from bpp.db.migrate import (
    get_version,
    import_from_analysis_json,
    import_presets_from_json,
    migrate,
)
from bpp.db.photos import (
    bulk_upsert_photos,
    check_missing,
    get_all_photos,
    get_deleted_photos,
    get_photo,
    get_photo_by_path,
    get_photo_count,
    mark_missing,
    permanent_delete_photos,
    purge_old_deleted,
    restore_photos,
    soft_delete_photos,
    update_hashes,
    update_scores,
    upsert_photo,
)
from bpp.db.presets import (
    delete_preset,
    list_presets,
    load_preset,
    save_preset,
)
from bpp.db.schema import SCHEMA_VERSION


@pytest.fixture
def db_path(tmp_path):
    """Create a temporary database and return its path."""
    path = str(tmp_path / "test.db")
    init_db(path)
    return path


@pytest.fixture
def conn(db_path):
    """Return a connection to the test database."""
    return get_db(db_path)


@pytest.fixture
def sample_image(tmp_path):
    """Create a small dummy image file and return its path."""
    img_path = tmp_path / "IMG_0001.jpg"
    img_path.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
    return str(img_path)


@pytest.fixture
def sample_photos(tmp_path):
    """Create multiple dummy image files and return their paths."""
    paths = []
    for i in range(5):
        p = tmp_path / f"IMG_{i:04d}.jpg"
        p.write_bytes(b"\xff\xd8\xff\xe0" + bytes([i]) * 100)
        paths.append(str(p))
    return paths


# --- Schema tests ---


class TestSchema:
    def test_init_db_creates_tables(self, conn):
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = {r[0] for r in tables}
        assert "photos" in table_names
        assert "albums" in table_names
        assert "album_photos" in table_names
        assert "presets" in table_names
        assert "face_embeddings" in table_names

    def test_schema_version(self, conn):
        assert get_version(conn) == SCHEMA_VERSION

    def test_init_db_idempotent(self, db_path):
        # Calling init_db again should not raise
        init_db(db_path)
        conn = get_db(db_path)
        assert get_version(conn) == SCHEMA_VERSION


# --- Photos tests ---


class TestPhotos:
    def test_upsert_and_get(self, conn, sample_image):
        photo_id = upsert_photo(
            conn,
            {
                "filepath": sample_image,
                "date": "2024-03-15T10:30:00",
                "date_day": "2024-03-15",
                "date_month": "2024-03",
                "blur_raw": 150.5,
                "exposure_score": 0.8,
                "face_score": 0.6,
                "composition_score": 0.7,
            },
        )
        assert photo_id > 0

        photo = get_photo(conn, photo_id)
        assert photo is not None
        assert photo["filepath"] == sample_image
        assert photo["blur_raw"] == 150.5
        assert photo["exposure_score"] == 0.8
        assert photo["original_filename"] == "IMG_0001.jpg"
        assert photo["missing"] == 0

    def test_get_by_path(self, conn, sample_image):
        upsert_photo(conn, {"filepath": sample_image})
        photo = get_photo_by_path(conn, sample_image)
        assert photo is not None
        assert photo["filepath"] == sample_image

    def test_upsert_updates_existing(self, conn, sample_image):
        upsert_photo(conn, {"filepath": sample_image, "blur_raw": 100.0})
        upsert_photo(conn, {"filepath": sample_image, "blur_raw": 200.0})
        photo = get_photo_by_path(conn, sample_image)
        assert photo["blur_raw"] == 200.0
        assert get_photo_count(conn) == 1

    def test_get_all_photos(self, conn, sample_photos):
        for fp in sample_photos:
            upsert_photo(conn, {"filepath": fp})
        all_photos = get_all_photos(conn)
        assert len(all_photos) == 5

    def test_update_scores(self, conn, sample_image):
        upsert_photo(conn, {"filepath": sample_image})
        update_scores(
            conn,
            sample_image,
            {
                "blur_score": 0.85,
                "aggregate_score": 0.72,
            },
        )
        photo = get_photo_by_path(conn, sample_image)
        assert photo["blur_score"] == 0.85
        assert photo["aggregate_score"] == 0.72

    def test_update_hashes(self, conn, sample_image):
        upsert_photo(conn, {"filepath": sample_image})
        update_hashes(conn, sample_image, 12345, 67890)
        photo = get_photo_by_path(conn, sample_image)
        assert photo["phash"] == 12345
        assert photo["ahash"] == 67890

    def test_bulk_upsert(self, conn, sample_photos):
        photos = [{"filepath": fp, "blur_raw": i * 10.0} for i, fp in enumerate(sample_photos)]
        count = bulk_upsert_photos(conn, photos)
        assert count == 5
        assert get_photo_count(conn) == 5

    def test_mark_missing(self, conn, sample_image):
        upsert_photo(conn, {"filepath": sample_image})
        mark_missing(conn, sample_image)
        # Missing photos excluded by default
        assert get_photo_count(conn) == 0
        # But included when requested
        assert get_photo_count(conn, include_missing=True) == 1

    def test_check_missing(self, conn, tmp_path):
        # Create a file, insert, then delete
        f = tmp_path / "ephemeral.jpg"
        f.write_bytes(b"\xff\xd8" + b"\x00" * 50)
        upsert_photo(conn, {"filepath": str(f)})
        f.unlink()
        missing = check_missing(conn)
        assert str(f) in missing
        assert get_photo_count(conn) == 0

    def test_missing_excluded_from_get_all(self, conn, sample_photos):
        for fp in sample_photos:
            upsert_photo(conn, {"filepath": fp})
        mark_missing(conn, sample_photos[0])
        assert len(get_all_photos(conn)) == 4
        assert len(get_all_photos(conn, include_missing=True)) == 5


# --- Albums tests ---


class TestAlbums:
    def test_create_and_get(self, conn):
        album_id = create_album(conn, "Vacation", config={"blur_weight": 0.5}, k=30)
        album = get_album(conn, album_id)
        assert album is not None
        assert album["name"] == "Vacation"
        assert album["config"]["blur_weight"] == 0.5
        assert album["k"] == 30
        assert album["album_type"] == "manual"

    def test_list_albums(self, conn):
        create_album(conn, "Album A")
        create_album(conn, "Album B")
        albums = list_albums(conn)
        assert len(albums) == 2

    def test_update_album(self, conn):
        album_id = create_album(conn, "Old Name")
        update_album(conn, album_id, name="New Name", k=100)
        album = get_album(conn, album_id)
        assert album["name"] == "New Name"
        assert album["k"] == 100

    def test_delete_album(self, conn):
        album_id = create_album(conn, "To Delete")
        delete_album(conn, album_id)
        assert get_album(conn, album_id) is None

    def test_delete_cascades_to_album_photos(self, conn, sample_image):
        album_id = create_album(conn, "Test")
        photo_id = upsert_photo(conn, {"filepath": sample_image})
        add_photos_to_album(conn, album_id, [photo_id])
        delete_album(conn, album_id)
        # album_photos should be cleaned up
        row = conn.execute(
            "SELECT COUNT(*) FROM album_photos WHERE album_id=?", (album_id,)
        ).fetchone()
        assert row[0] == 0

    def test_add_photos_to_album(self, conn, sample_photos):
        album_id = create_album(conn, "Test")
        photo_ids = [upsert_photo(conn, {"filepath": fp}) for fp in sample_photos]
        count = add_photos_to_album(conn, album_id, photo_ids)
        assert count == 5
        photos = get_album_photos(conn, album_id)
        assert len(photos) == 5

    def test_set_album_selection(self, conn, sample_photos):
        album_id = create_album(conn, "Test")
        photo_ids = [upsert_photo(conn, {"filepath": fp}) for fp in sample_photos]
        add_photos_to_album(conn, album_id, photo_ids)
        set_album_selection(conn, album_id, {photo_ids[0], photo_ids[2]})
        photos = get_album_photos(conn, album_id)
        selected = [p for p in photos if p["selected"]]
        assert len(selected) == 2

    def test_set_album_selection_clears_then_sets_atomically(self, conn, sample_photos):
        """R8-H6: a fresh selection must replace the prior one — every
        photo in the album ends up selected iff its id is in the new
        set. Locks the contract that "set selection" is a complete
        replacement, not an additive merge."""
        album_id = create_album(conn, "Test")
        photo_ids = [upsert_photo(conn, {"filepath": fp}) for fp in sample_photos]
        add_photos_to_album(conn, album_id, photo_ids)

        # First selection: photos 0, 1, 2
        set_album_selection(conn, album_id, set(photo_ids[:3]))
        # Second selection: photos 3, 4 — the previous three must be cleared
        set_album_selection(conn, album_id, set(photo_ids[3:5]))

        photos = get_album_photos(conn, album_id)
        selected_ids = {p["id"] for p in photos if p["selected"]}
        assert selected_ids == set(photo_ids[3:5]), (
            "set_album_selection must REPLACE the prior selection atomically "
            f"(got {selected_ids}, expected {set(photo_ids[3:5])})"
        )

    def test_set_album_selection_uses_single_update_statement(self, conn, sample_photos):
        """R8-H6: the implementation must collapse "clear then set"
        into ONE statement so two concurrent recompute writers can't
        interleave between the clear and the set. Use sqlite3's
        `set_trace_callback` to capture every executed SQL statement
        and assert exactly one UPDATE on album_photos when there's a
        non-empty selection."""
        album_id = create_album(conn, "Test")
        photo_ids = [upsert_photo(conn, {"filepath": fp}) for fp in sample_photos]
        add_photos_to_album(conn, album_id, photo_ids)

        captured: list[str] = []

        def _trace(sql: str) -> None:
            captured.append(sql)

        conn.set_trace_callback(_trace)
        try:
            set_album_selection(conn, album_id, {photo_ids[0], photo_ids[2]})
        finally:
            conn.set_trace_callback(None)

        update_calls = [s for s in captured if s.lstrip().upper().startswith("UPDATE ALBUM_PHOTOS")]
        assert len(update_calls) == 1, (
            f"R8-H6: expected ONE UPDATE statement to avoid the clear-then-set "
            f"race, got {len(update_calls)}: {update_calls}"
        )
        # The single statement must use a CASE expression to express
        # both states atomically
        assert "CASE" in update_calls[0].upper()

    def test_set_override(self, conn, sample_image):
        album_id = create_album(conn, "Test")
        photo_id = upsert_photo(conn, {"filepath": sample_image})
        add_photos_to_album(conn, album_id, [photo_id])

        set_override(conn, album_id, photo_id, "include")
        photos = get_album_photos(conn, album_id)
        assert photos[0]["override"] == "include"

        set_override(conn, album_id, photo_id, None)
        photos = get_album_photos(conn, album_id)
        assert photos[0]["override"] is None

    def test_toggle_favorite(self, conn, sample_image):
        album_id = create_album(conn, "Test")
        photo_id = upsert_photo(conn, {"filepath": sample_image})
        add_photos_to_album(conn, album_id, [photo_id])

        result = toggle_favorite(conn, album_id, photo_id)
        assert result is True
        result = toggle_favorite(conn, album_id, photo_id)
        assert result is False

    def test_set_overrides_bulk(self, conn, sample_photos):
        album_id = create_album(conn, "Test")
        photo_ids = [upsert_photo(conn, {"filepath": fp}) for fp in sample_photos]
        add_photos_to_album(conn, album_id, photo_ids)

        count = set_overrides_bulk(conn, album_id, photo_ids[:3], "exclude")
        assert count == 3
        photos = get_album_photos(conn, album_id)
        overrides = {p["filepath"]: p["override"] for p in photos}
        assert overrides[sample_photos[0]] == "exclude"
        assert overrides[sample_photos[2]] == "exclude"
        assert overrides[sample_photos[3]] is None

        # Clear overrides on a subset
        count = set_overrides_bulk(conn, album_id, photo_ids[:2], None)
        assert count == 2
        photos = get_album_photos(conn, album_id)
        overrides = {p["filepath"]: p["override"] for p in photos}
        assert overrides[sample_photos[0]] is None
        assert overrides[sample_photos[2]] == "exclude"

    def test_set_overrides_bulk_empty(self, conn):
        album_id = create_album(conn, "Test")
        count = set_overrides_bulk(conn, album_id, [], "include")
        assert count == 0

    def test_set_favorites_bulk(self, conn, sample_photos):
        album_id = create_album(conn, "Test")
        photo_ids = [upsert_photo(conn, {"filepath": fp}) for fp in sample_photos]
        add_photos_to_album(conn, album_id, photo_ids)

        count = set_favorites_bulk(conn, album_id, photo_ids[:3], True)
        assert count == 3
        photos = get_album_photos(conn, album_id)
        favs = {p["filepath"]: bool(p["favorite"]) for p in photos}
        assert favs[sample_photos[0]] is True
        assert favs[sample_photos[2]] is True
        assert favs[sample_photos[3]] is False

        # Unfavorite a subset
        count = set_favorites_bulk(conn, album_id, photo_ids[:2], False)
        assert count == 2
        photos = get_album_photos(conn, album_id)
        favs = {p["filepath"]: bool(p["favorite"]) for p in photos}
        assert favs[sample_photos[0]] is False
        assert favs[sample_photos[2]] is True

    def test_set_favorites_bulk_empty(self, conn):
        album_id = create_album(conn, "Test")
        count = set_favorites_bulk(conn, album_id, [], True)
        assert count == 0

    def test_ensure_all_photos_album(self, conn):
        album_id1 = ensure_all_photos_album(conn)
        album_id2 = ensure_all_photos_album(conn)
        assert album_id1 == album_id2
        album = get_album(conn, album_id1)
        assert album["album_type"] == "all"

    def test_sync_all_photos(self, conn, sample_photos):
        for fp in sample_photos:
            upsert_photo(conn, {"filepath": fp})
        sync_all_photos_album(conn)
        album_id = ensure_all_photos_album(conn)
        photos = get_album_photos(conn, album_id)
        assert len(photos) == 5

    def test_per_album_independence(self, conn, sample_photos):
        """Two albums can have different overrides for the same photo."""
        photo_ids = [upsert_photo(conn, {"filepath": fp}) for fp in sample_photos]
        album_a = create_album(conn, "A")
        album_b = create_album(conn, "B")
        add_photos_to_album(conn, album_a, photo_ids)
        add_photos_to_album(conn, album_b, photo_ids)

        set_override(conn, album_a, photo_ids[0], "include")
        set_override(conn, album_b, photo_ids[0], "exclude")

        photos_a = get_album_photos(conn, album_a)
        photos_b = get_album_photos(conn, album_b)
        first_a = next(p for p in photos_a if p["id"] == photo_ids[0])
        first_b = next(p for p in photos_b if p["id"] == photo_ids[0])
        assert first_a["override"] == "include"
        assert first_b["override"] == "exclude"


# --- Presets tests ---


class TestPresets:
    def test_save_and_load(self, conn):
        settings = {"blur_weight": 0.4, "exposure_weight": 0.3}
        save_preset(conn, "Sharp Focus", settings)
        loaded = load_preset(conn, "Sharp Focus")
        assert loaded == settings

    def test_overwrite(self, conn):
        save_preset(conn, "Test", {"a": 1})
        save_preset(conn, "Test", {"a": 2})
        loaded = load_preset(conn, "Test")
        assert loaded == {"a": 2}

    def test_list_presets(self, conn):
        save_preset(conn, "Preset A", {"x": 1})
        save_preset(conn, "Preset B", {"y": 2})
        presets = list_presets(conn)
        assert len(presets) == 2
        assert presets[0]["name"] == "Preset A"

    def test_delete(self, conn):
        save_preset(conn, "To Delete", {"z": 3})
        assert delete_preset(conn, "To Delete") is True
        assert load_preset(conn, "To Delete") is None
        assert delete_preset(conn, "To Delete") is False

    def test_load_nonexistent(self, conn):
        assert load_preset(conn, "nope") is None


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


# --- Smart albums tests ---


class TestSmartAlbums:
    def test_refresh_time_albums(self, conn, sample_photos):
        """Time-based smart albums are created for each year."""
        for i, fp in enumerate(sample_photos):
            upsert_photo(
                conn,
                {
                    "filepath": fp,
                    "original_filename": f"img_{i}.jpg",
                    "file_size": 100,
                    "file_mtime": 1000.0 + i,
                    "date": f"202{i}-06-15T12:00:00",
                },
            )

        from bpp.db.smart_albums import _refresh_time_albums

        _refresh_time_albums(conn)
        albums = list_albums(conn)
        time_albums = [a for a in albums if a["album_type"] == "smart_time"]
        year_names = {a["name"] for a in time_albums if a["name"] != "Last 30 Days"}
        assert "2020" in year_names
        assert "2024" in year_names

    def test_refresh_score_album(self, conn, sample_photos):
        """Top Rated smart album is created."""
        for i, fp in enumerate(sample_photos):
            upsert_photo(
                conn,
                {
                    "filepath": fp,
                    "original_filename": f"img_{i}.jpg",
                    "file_size": 100,
                    "file_mtime": 1000.0 + i,
                    "aggregate_score": 0.5 + i * 0.1,
                },
            )

        from bpp.db.smart_albums import _refresh_score_album

        _refresh_score_album(conn)
        albums = list_albums(conn)
        score_albums = [a for a in albums if a["album_type"] == "smart_score"]
        assert len(score_albums) == 1
        assert score_albums[0]["name"] == "Top Rated"

    def test_refresh_person_albums(self, conn, sample_photos, tmp_path):
        """Person smart albums are created for face clusters."""
        # Insert photos into DB
        photo_ids = []
        for i, fp in enumerate(sample_photos):
            pid = upsert_photo(
                conn,
                {
                    "filepath": fp,
                    "original_filename": f"img_{i}.jpg",
                    "file_size": 100,
                    "file_mtime": 1000.0 + i,
                },
            )
            photo_ids.append(pid)

        # Two clusters: cluster 0 with 3 photos, cluster 1 with 2 photos
        for i in range(3):
            conn.execute(
                "INSERT INTO face_embeddings "
                "(photo_id, face_index, embedding, cluster_id) "
                "VALUES (?, 0, X'00', 0)",
                (photo_ids[i],),
            )
        for i in range(3, 5):
            conn.execute(
                "INSERT INTO face_embeddings "
                "(photo_id, face_index, embedding, cluster_id) "
                "VALUES (?, 0, X'00', 1)",
                (photo_ids[i],),
            )
        conn.commit()

        from bpp.db.smart_albums import _refresh_person_albums

        _refresh_person_albums(conn)
        albums = list_albums(conn)
        person_albums = [a for a in albums if a["album_type"] == "smart_person"]
        assert len(person_albums) == 2
        names = {a["name"] for a in person_albums}
        assert "Person 1" in names
        assert "Person 2" in names

    def test_dismissed_cluster_excluded(self, conn, sample_photos, tmp_path):
        """Clusters with cluster_id=-2 (dismissed) are excluded."""
        photo_ids = []
        for i, fp in enumerate(sample_photos[:3]):
            pid = upsert_photo(
                conn,
                {
                    "filepath": fp,
                    "original_filename": f"img_{i}.jpg",
                    "file_size": 100,
                    "file_mtime": 1000.0 + i,
                },
            )
            photo_ids.append(pid)

        # Cluster 0: active, cluster -2: dismissed
        conn.execute(
            "INSERT INTO face_embeddings "
            "(photo_id, face_index, embedding, cluster_id) "
            "VALUES (?, 0, X'00', 0)",
            (photo_ids[0],),
        )
        conn.execute(
            "INSERT INTO face_embeddings "
            "(photo_id, face_index, embedding, cluster_id) "
            "VALUES (?, 0, X'00', -2)",
            (photo_ids[1],),
        )
        conn.commit()

        from bpp.db.smart_albums import _refresh_person_albums

        _refresh_person_albums(conn)
        albums = list_albums(conn)
        person_albums = [a for a in albums if a["album_type"] == "smart_person"]
        # Only cluster 0 should get an album, dismissed cluster excluded
        assert len(person_albums) == 1
        assert person_albums[0]["name"] == "Person 1"


class TestSoftDelete:
    def test_soft_delete_hides_from_get_all(self, conn, sample_photos):
        ids = []
        for i, fp in enumerate(sample_photos):
            pid = upsert_photo(
                conn,
                {
                    "filepath": fp,
                    "original_filename": f"img_{i}.jpg",
                    "file_size": 100,
                    "file_mtime": 1000.0 + i,
                },
            )
            ids.append(pid)

        assert get_photo_count(conn) == 5
        soft_delete_photos(conn, [ids[0], ids[1]])
        assert get_photo_count(conn) == 3
        assert len(get_all_photos(conn)) == 3

    def test_soft_delete_returns_count(self, conn, sample_photos):
        ids = []
        for i, fp in enumerate(sample_photos[:2]):
            pid = upsert_photo(
                conn,
                {
                    "filepath": fp,
                    "original_filename": f"img_{i}.jpg",
                    "file_size": 100,
                    "file_mtime": 1000.0 + i,
                },
            )
            ids.append(pid)
        assert soft_delete_photos(conn, ids) == 2
        # Deleting again returns 0 (already deleted)
        assert soft_delete_photos(conn, ids) == 0

    def test_get_deleted_photos(self, conn, sample_photos):
        ids = []
        for i, fp in enumerate(sample_photos[:3]):
            pid = upsert_photo(
                conn,
                {
                    "filepath": fp,
                    "original_filename": f"img_{i}.jpg",
                    "file_size": 100,
                    "file_mtime": 1000.0 + i,
                },
            )
            ids.append(pid)
        soft_delete_photos(conn, [ids[0]])
        deleted = get_deleted_photos(conn)
        assert len(deleted) == 1
        assert deleted[0]["filepath"] == sample_photos[0]
        assert deleted[0]["deleted_at"] is not None

    def test_restore_photos(self, conn, sample_photos):
        ids = []
        for i, fp in enumerate(sample_photos[:2]):
            pid = upsert_photo(
                conn,
                {
                    "filepath": fp,
                    "original_filename": f"img_{i}.jpg",
                    "file_size": 100,
                    "file_mtime": 1000.0 + i,
                },
            )
            ids.append(pid)
        soft_delete_photos(conn, ids)
        assert get_photo_count(conn) == 0
        count = restore_photos(conn, [ids[0]])
        assert count == 1
        assert get_photo_count(conn) == 1
        assert len(get_deleted_photos(conn)) == 1

    def test_permanent_delete(self, conn, sample_photos):
        ids = []
        for i, fp in enumerate(sample_photos[:2]):
            pid = upsert_photo(
                conn,
                {
                    "filepath": fp,
                    "original_filename": f"img_{i}.jpg",
                    "file_size": 100,
                    "file_mtime": 1000.0 + i,
                },
            )
            ids.append(pid)
        soft_delete_photos(conn, ids)
        paths = permanent_delete_photos(conn, [ids[0]])
        assert len(paths) == 1
        assert paths[0] == sample_photos[0]
        # Gone from both active and deleted
        assert get_photo_count(conn) == 0
        assert len(get_deleted_photos(conn)) == 1
        assert get_photo(conn, ids[0]) is None

    def test_permanent_delete_removes_album_links(self, conn, sample_photos):
        pid = upsert_photo(
            conn,
            {
                "filepath": sample_photos[0],
                "original_filename": "img.jpg",
                "file_size": 100,
                "file_mtime": 1000.0,
            },
        )
        album_id = create_album(conn, "Test Album")
        add_photos_to_album(conn, album_id, [pid])
        assert len(get_album_photos(conn, album_id)) == 1
        soft_delete_photos(conn, [pid])
        permanent_delete_photos(conn, [pid])
        # Photo gone from album too
        rows = conn.execute("SELECT * FROM album_photos WHERE photo_id=?", (pid,)).fetchall()
        assert len(rows) == 0

    def test_deleted_excluded_from_album_photos(self, conn, sample_photos):
        ids = []
        for i, fp in enumerate(sample_photos[:3]):
            pid = upsert_photo(
                conn,
                {
                    "filepath": fp,
                    "original_filename": f"img_{i}.jpg",
                    "file_size": 100,
                    "file_mtime": 1000.0 + i,
                },
            )
            ids.append(pid)
        album_id = create_album(conn, "Test")
        add_photos_to_album(conn, album_id, ids)
        assert len(get_album_photos(conn, album_id)) == 3
        soft_delete_photos(conn, [ids[0]])
        assert len(get_album_photos(conn, album_id)) == 2

    def test_purge_old_deleted(self, conn, sample_photos):
        pid = upsert_photo(
            conn,
            {
                "filepath": sample_photos[0],
                "original_filename": "img.jpg",
                "file_size": 100,
                "file_mtime": 1000.0,
            },
        )
        # Soft delete then backdate to 31 days ago
        soft_delete_photos(conn, [pid])
        conn.execute(
            "UPDATE photos SET deleted_at=datetime('now', '-31 days') WHERE id=?",
            (pid,),
        )
        conn.commit()
        paths = purge_old_deleted(conn, days=30)
        assert len(paths) == 1
        assert get_photo(conn, pid) is None

    def test_purge_keeps_recent_deletes(self, conn, sample_photos):
        pid = upsert_photo(
            conn,
            {
                "filepath": sample_photos[0],
                "original_filename": "img.jpg",
                "file_size": 100,
                "file_mtime": 1000.0,
            },
        )
        soft_delete_photos(conn, [pid])
        paths = purge_old_deleted(conn, days=30)
        assert len(paths) == 0
        assert len(get_deleted_photos(conn)) == 1

    def test_sync_all_photos_excludes_deleted(self, conn, sample_photos):
        ids = []
        for i, fp in enumerate(sample_photos[:3]):
            pid = upsert_photo(
                conn,
                {
                    "filepath": fp,
                    "original_filename": f"img_{i}.jpg",
                    "file_size": 100,
                    "file_mtime": 1000.0 + i,
                },
            )
            ids.append(pid)
        soft_delete_photos(conn, [ids[0]])
        sync_all_photos_album(conn)
        all_id = ensure_all_photos_album(conn)
        photos = get_album_photos(conn, all_id)
        filepaths = {p["filepath"] for p in photos}
        assert sample_photos[0] not in filepaths
        assert sample_photos[1] in filepaths

    def test_empty_list_operations(self, conn):
        assert soft_delete_photos(conn, []) == 0
        assert restore_photos(conn, []) == 0
        assert permanent_delete_photos(conn, []) == []

    def test_get_all_include_deleted(self, conn, sample_photos):
        ids = []
        for i, fp in enumerate(sample_photos[:3]):
            pid = upsert_photo(
                conn,
                {
                    "filepath": fp,
                    "original_filename": f"img_{i}.jpg",
                    "file_size": 100,
                    "file_mtime": 1000.0 + i,
                },
            )
            ids.append(pid)
        soft_delete_photos(conn, [ids[0]])
        assert len(get_all_photos(conn)) == 2
        all_inc = get_all_photos(conn, include_deleted=True)
        assert len(all_inc) == 3
        deleted_paths = [p["filepath"] for p in all_inc if p["deleted_at"]]
        assert sample_photos[0] in deleted_paths

    def test_smart_deleted_album_returns_deleted_ids(self, conn, sample_photos):
        from bpp.db.smart_albums import get_smart_album_photo_ids

        album = {"album_type": "smart_deleted", "rule": {}}
        ids = []
        for i, fp in enumerate(sample_photos[:3]):
            pid = upsert_photo(
                conn,
                {
                    "filepath": fp,
                    "original_filename": f"img_{i}.jpg",
                    "file_size": 100,
                    "file_mtime": 1000.0 + i,
                },
            )
            ids.append(pid)
        # No deleted photos yet
        result = get_smart_album_photo_ids(conn, album)
        assert result == []
        # Delete one
        soft_delete_photos(conn, [ids[0]])
        result = get_smart_album_photo_ids(conn, album)
        assert result == [ids[0]]
        # Delete another
        soft_delete_photos(conn, [ids[2]])
        result = get_smart_album_photo_ids(conn, album)
        assert set(result) == {ids[0], ids[2]}

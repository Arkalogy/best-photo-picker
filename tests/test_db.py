"""Tests for the bpp.db package — part 1 of 3."""

# Shared fixtures are imported by name and re-bound as test parameters.
# ruff: noqa: F811

from __future__ import annotations

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
)
from bpp.db.photos import (
    bulk_upsert_photos,
    check_missing,
    get_all_photos,
    get_photo,
    get_photo_by_path,
    get_photo_count,
    mark_missing,
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
from tests._db_shared import conn, db_path, sample_image, sample_photos  # noqa: F401

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
        """A fresh selection must replace the prior one — every
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
        """The implementation must collapse "clear then set"
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
            f"Expected ONE UPDATE statement to avoid the clear-then-set "
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

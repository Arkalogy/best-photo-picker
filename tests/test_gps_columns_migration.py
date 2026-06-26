"""R8-H9: GPS columns + partial index migration.

Before: map view scanned the full photos table and json_extract'd
gps_lat / gps_lon from exif_json on every row. With 50k+ photos
that's ~50k JSON parses + a full table scan, both visibly slow.

After: stable `gps_lat REAL` + `gps_lon REAL` columns plus a
partial index `idx_photos_gps` covering only rows where both are
non-null. Existing exif_json data is backfilled by migration v30.

This test class locks four contracts:
  1. The migration adds the columns.
  2. The migration creates the partial index with the right
     predicate so the planner uses it.
  3. The backfill copies coords out of exif_json into the new
     columns, and skips malformed JSON cleanly.
  4. New writes via `upsert_photo` populate the columns directly
     (exif_json's gps_lat/lon mirror is now redundant but kept
     for back-compat with downstream consumers that read the
     blob).
"""

from __future__ import annotations

import json
import sqlite3

import pytest


def _create_v18_db_for_v30_test(db_path: str) -> None:
    """Reuse the v18 seed from test_migration_multi_step (it has all
    the columns the intermediate migrations expect). Migrations
    will then run v18 -> v30 against this fixture, exercising the
    whole chain end-to-end including the new v30 step."""
    from tests.test_migration_multi_step import _create_v18_db

    _create_v18_db(db_path)


class TestGpsColumnsMigration:
    def test_migration_adds_columns(self, tmp_path):
        from bpp.db.connection import close_all_connections, init_db

        db_path = str(tmp_path / "test.db")
        _create_v18_db_for_v30_test(db_path)

        init_db(db_path)
        try:
            conn = sqlite3.connect(db_path)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(photos)").fetchall()}
            assert "gps_lat" in cols, "Migration v30 must add gps_lat column"
            assert "gps_lon" in cols, "Migration v30 must add gps_lon column"
        finally:
            conn.close()
            close_all_connections()

    def test_migration_creates_partial_index(self, tmp_path):
        from bpp.db.connection import close_all_connections, init_db

        db_path = str(tmp_path / "test.db")
        _create_v18_db_for_v30_test(db_path)

        init_db(db_path)
        try:
            conn = sqlite3.connect(db_path)
            idx_rows = conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='index' AND name='idx_photos_gps'"
            ).fetchall()
            assert idx_rows, "Migration v30 must create idx_photos_gps"
            sql = idx_rows[0][1] or ""
            assert "gps_lat" in sql and "gps_lon" in sql
            # Partial index — sparse data, only rows with both coords
            # set get an index entry. Without WHERE the index would
            # blow out to ~5x the size for typical libraries.
            assert "WHERE" in sql.upper(), (
                "idx_photos_gps must be a PARTIAL index (WHERE gps_lat IS NOT NULL "
                "AND gps_lon IS NOT NULL); plain index wastes space on "
                "no-GPS rows"
            )
        finally:
            conn.close()
            close_all_connections()

    def test_backfill_populates_from_exif_json(self, tmp_path):
        """The migration copies coords from existing exif_json blobs
        into the new columns so libraries don't have to re-analyze."""
        from bpp.db.connection import close_all_connections, init_db

        db_path = str(tmp_path / "test.db")
        _create_v18_db_for_v30_test(db_path)

        # Seed: one with GPS in exif_json, one without
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO photos (filepath, original_filename, file_size, file_mtime, exif_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                "/tmp/with_gps.jpg",
                "with_gps.jpg",
                1,
                1.0,
                json.dumps({"gps_lat": 37.7749, "gps_lon": -122.4194, "camera": "Test"}),
            ),
        )
        conn.execute(
            "INSERT INTO photos (filepath, original_filename, file_size, file_mtime, exif_json) "
            "VALUES (?, ?, ?, ?, ?)",
            ("/tmp/no_gps.jpg", "no_gps.jpg", 1, 1.0, json.dumps({"camera": "Test"})),
        )
        conn.commit()
        conn.close()

        # Run migrations
        init_db(db_path)
        try:
            conn = sqlite3.connect(db_path)
            row = conn.execute(
                "SELECT gps_lat, gps_lon FROM photos WHERE filepath=?",
                ("/tmp/with_gps.jpg",),
            ).fetchone()
            assert row[0] == pytest.approx(37.7749)
            assert row[1] == pytest.approx(-122.4194)

            row2 = conn.execute(
                "SELECT gps_lat, gps_lon FROM photos WHERE filepath=?",
                ("/tmp/no_gps.jpg",),
            ).fetchone()
            assert row2[0] is None
            assert row2[1] is None
        finally:
            conn.close()
            close_all_connections()

    def test_backfill_handles_malformed_exif_json(self, tmp_path):
        """A row with invalid JSON in exif_json must not abort the
        whole migration — `safe_json_loads` skips it cleanly."""
        from bpp.db.connection import close_all_connections, init_db

        db_path = str(tmp_path / "test.db")
        _create_v18_db_for_v30_test(db_path)

        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO photos (filepath, original_filename, file_size, file_mtime, exif_json) "
            "VALUES (?, ?, ?, ?, ?)",
            ("/tmp/garbage.jpg", "garbage.jpg", 1, 1.0, "not-json {{{ broken"),
        )
        conn.execute(
            "INSERT INTO photos (filepath, original_filename, file_size, file_mtime, exif_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                "/tmp/good.jpg",
                "good.jpg",
                1,
                1.0,
                json.dumps({"gps_lat": 1.0, "gps_lon": 2.0}),
            ),
        )
        conn.commit()
        conn.close()

        # Migration must complete without raising
        init_db(db_path)
        try:
            conn = sqlite3.connect(db_path)
            assert (
                conn.execute(
                    "SELECT user_version FROM (SELECT 1) JOIN pragma_user_version()"
                ).fetchone()[0]
                >= 30
            ), "Migration must reach v30+"

            # Good row was backfilled, garbage row was skipped
            assert conn.execute(
                "SELECT gps_lat FROM photos WHERE filepath=?", ("/tmp/good.jpg",)
            ).fetchone()[0] == pytest.approx(1.0)
            assert (
                conn.execute(
                    "SELECT gps_lat FROM photos WHERE filepath=?", ("/tmp/garbage.jpg",)
                ).fetchone()[0]
                is None
            )
        finally:
            conn.close()
            close_all_connections()

    def test_get_photos_with_gps_uses_columns_not_json_extract(self, tmp_path):
        """Read-side: the SQL produced by `get_photos_with_gps`
        must reference `p.gps_lat` directly, not call json_extract.
        Source-scan regression — if a future refactor accidentally
        reverts the read path, this fails."""
        import re
        from pathlib import Path

        # get_photos_with_gps moved to bpp/db/photos_gps.py during the
        # 500-LOC split (re-exported from photos.py, but the body lives
        # in the dedicated module).
        src = Path("bpp/db/photos_gps.py").read_text()
        # Find the get_photos_with_gps function block
        start = src.index("def get_photos_with_gps")
        end = src.index("\ndef ", start + 1)
        func_src = src[start:end]

        assert "p.gps_lat" in func_src, "get_photos_with_gps must read p.gps_lat directly (R8-H9)"
        # Match function CALLS to json_extract / dialect.json_extract,
        # not docstring mentions of the term.
        assert not re.search(r"\bjson_extract\s*\(", func_src), (
            "get_photos_with_gps must NOT call json_extract — that's the regression R8-H9 fixed"
        )

    def test_upsert_writes_gps_columns_directly(self, tmp_path):
        """Write-side: when a photo dict carries gps_lat / gps_lon
        keys, upsert_photo persists them as columns (not just into
        exif_json)."""
        from bpp.db.connection import close_all_connections, init_db
        from bpp.db.photos import upsert_photo

        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        try:
            photo_id = upsert_photo(
                sqlite3.connect(db_path),
                {
                    "filepath": "/tmp/new.jpg",
                    "file_size": 1,
                    "file_mtime": 1.0,
                    "gps_lat": 51.5074,
                    "gps_lon": -0.1278,
                    "exif_json": json.dumps({"gps_lat": 51.5074, "gps_lon": -0.1278}),
                },
            )
            assert photo_id > 0

            conn = sqlite3.connect(db_path)
            row = conn.execute(
                "SELECT gps_lat, gps_lon FROM photos WHERE id=?", (photo_id,)
            ).fetchone()
            assert row[0] == pytest.approx(51.5074)
            assert row[1] == pytest.approx(-0.1278)
            conn.close()
        finally:
            close_all_connections()


# ─── R9-rec-M2: range / NaN / type validation ─────────────────────────


class TestValidateGpsPair:
    """The GPS lift / backfill paths must reject corrupt or out-of-
    range coordinates so they don't land in the indexed
    gps_lat/gps_lon columns and corrupt downstream consumers (the
    map view, smart-album-by-location)."""

    @pytest.mark.parametrize(
        "lat,lon,expected",
        [
            (0.0, 0.0, True),
            (90.0, 180.0, True),
            (-90.0, -180.0, True),
            (37.7749, -122.4194, True),
            (None, None, False),
            (None, 0.0, False),
            (0.0, None, False),
            ("37.0", "-122.0", False),
            (90.5, 0.0, False),
            (0.0, 180.5, False),
            (-91.0, 0.0, False),
            (0.0, -181.0, False),
            (float("nan"), 0.0, False),
            (0.0, float("nan"), False),
            (float("inf"), 0.0, False),
            (0.0, float("-inf"), False),
            (True, 0.0, False),
        ],
    )
    def test_pairs(self, lat, lon, expected):
        from bpp.db.photos import _valid_gps_pair

        assert _valid_gps_pair(lat, lon) is expected, (
            f"_valid_gps_pair({lat!r}, {lon!r}) expected {expected}"
        )

    def test_lift_skips_out_of_range_coords(self):
        from bpp.db.photos import _maybe_lift_gps_from_exif

        values = {
            "filepath": "/x.jpg",
            "exif_json": json.dumps({"gps_lat": 999.0, "gps_lon": 0.0}),
        }
        _maybe_lift_gps_from_exif(values)
        assert "gps_lat" not in values, "out-of-range lat must NOT lift to the indexed column"
        assert "gps_lon" not in values

    def test_lift_skips_string_coords(self):
        from bpp.db.photos import _maybe_lift_gps_from_exif

        values = {
            "filepath": "/x.jpg",
            "exif_json": json.dumps({"gps_lat": "n/a", "gps_lon": 0.0}),
        }
        _maybe_lift_gps_from_exif(values)
        assert "gps_lat" not in values

    def test_lift_accepts_valid_coords(self):
        from bpp.db.photos import _maybe_lift_gps_from_exif

        values = {
            "filepath": "/x.jpg",
            "exif_json": json.dumps({"gps_lat": 37.7749, "gps_lon": -122.4194}),
        }
        _maybe_lift_gps_from_exif(values)
        assert values["gps_lat"] == pytest.approx(37.7749)
        assert values["gps_lon"] == pytest.approx(-122.4194)

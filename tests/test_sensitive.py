"""Tests for the sensitive-photo flag (NudeNet score + user override, v43).

Covers the four backend pieces:
1. The v43 migration (column + index, idempotent, missing-table safe).
2. The two derivations — SENSITIVE_PHOTO_SQL and is_sensitive_item —
   run against the SAME override x score matrix so they can never drift.
3. The override endpoint contract (set / clear / validation).
4. The Sensitive smart album lifecycle (appears with members, removed
   when empty, follows overrides).
"""

from __future__ import annotations

import sqlite3

import pytest

from bpp.constants import (
    SENSITIVE_NUDITY_THRESHOLD,
    sensitive_photo_sql,
)
from bpp.web.photo_dict import is_sensitive_item

# ---------------------------------------------------------------------------
# 1. Migration
# ---------------------------------------------------------------------------


class TestSchemaV43Migration:
    def test_schema_version_includes_v43(self) -> None:
        from bpp.db.schema import SCHEMA_VERSION

        assert SCHEMA_VERSION >= 43

    def test_canonical_schema_has_override_column_and_index(self) -> None:
        from bpp.db.schema import INDEXES_SQL, TABLES_SQL

        assert "sensitive_override" in TABLES_SQL
        assert "idx_photos_sensitive" in INDEXES_SQL

    def test_v43_migration_is_idempotent(self) -> None:
        from bpp.db.migrations_latest import _migrate_v43

        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE photos (id INTEGER PRIMARY KEY, filepath TEXT, nudity_score REAL)"
        )
        _migrate_v43(conn)
        _migrate_v43(conn)  # second call is a no-op
        cols = [row[1] for row in conn.execute("PRAGMA table_info(photos)").fetchall()]
        assert cols.count("sensitive_override") == 1

    def test_v43_migration_skips_missing_table(self) -> None:
        from bpp.db.migrations_latest import _migrate_v43

        conn = sqlite3.connect(":memory:")
        _migrate_v43(conn)  # no photos table — must skip, not raise


# ---------------------------------------------------------------------------
# 2. Derivation matrix — SQL and Python must agree on every cell
# ---------------------------------------------------------------------------

# (sensitive_override, nudity_score, expected_is_sensitive)
_MATRIX = [
    (None, None, False),
    (None, 0.0, False),
    (None, SENSITIVE_NUDITY_THRESHOLD - 0.01, False),
    (None, SENSITIVE_NUDITY_THRESHOLD, True),  # threshold is inclusive
    (None, 0.9, True),
    (0, None, False),
    (0, 0.9, False),  # explicit "not sensitive" beats a high score
    (1, None, True),  # explicit "sensitive" needs no score
    (1, 0.0, True),
    (1, 0.9, True),
]


class TestDerivationMatrix:
    @pytest.mark.parametrize(("override", "score", "expected"), _MATRIX)
    def test_python_derivation(self, override, score, expected) -> None:
        item = {"sensitive_override": override, "nudity_score": score}
        assert is_sensitive_item(item) is expected, (
            f"is_sensitive_item(override={override}, score={score}) should be {expected}"
        )

    @pytest.mark.parametrize(("override", "score", "expected"), _MATRIX)
    def test_sql_derivation_matches(self, override, score, expected) -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE photos (id INTEGER PRIMARY KEY,"
            " nudity_score REAL, sensitive_override INTEGER)"
        )
        conn.execute(
            "INSERT INTO photos (id, nudity_score, sensitive_override) VALUES (1, ?, ?)",
            (score, override),
        )
        row = conn.execute(f"SELECT 1 FROM photos WHERE {sensitive_photo_sql()}").fetchone()
        assert (row is not None) is expected, (
            f"SENSITIVE_PHOTO_SQL(override={override}, score={score}) "
            f"should match={expected} — SQL and Python derivations have drifted"
        )

    def test_build_photo_dict_exposes_verdict_and_override(self) -> None:
        from bpp.web.photo_dict import build_photo_dict

        item = {"filepath": "/x/a.jpg", "nudity_score": 0.8, "sensitive_override": None}
        photo = build_photo_dict(item, None)
        assert photo["is_sensitive"] is True
        assert photo["sensitive_override"] is None

        item = {"filepath": "/x/a.jpg", "nudity_score": 0.8, "sensitive_override": 0}
        photo = build_photo_dict(item, None)
        assert photo["is_sensitive"] is False
        assert photo["sensitive_override"] == 0


# ---------------------------------------------------------------------------
# 3 + 4. DB helper + smart album lifecycle (real schema)
# ---------------------------------------------------------------------------


def _make_db(tmp_path):
    from bpp.db.connection import init_db

    db_path = str(tmp_path / "test.db")
    conn = init_db(db_path)
    return conn


def _insert_photo(conn, pid: int, nudity: float | None) -> None:
    conn.execute(
        "INSERT INTO photos"
        " (id, filepath, original_filename, file_size, file_mtime, missing, nudity_score)"
        " VALUES (?, ?, ?, 1, 0, 0, ?)",
        (pid, f"/photos/img_{pid}.jpg", f"img_{pid}.jpg", nudity),
    )
    conn.commit()


class TestSetSensitiveOverride:
    def test_set_and_clear(self, tmp_path) -> None:
        from bpp.db.photos import set_sensitive_override

        conn = _make_db(tmp_path)
        _insert_photo(conn, 1, 0.5)

        set_sensitive_override(conn, 1, 0)
        assert conn.execute("SELECT sensitive_override FROM photos WHERE id=1").fetchone()[0] == 0

        set_sensitive_override(conn, 1, 1)
        assert conn.execute("SELECT sensitive_override FROM photos WHERE id=1").fetchone()[0] == 1

        set_sensitive_override(conn, 1, None)
        assert (
            conn.execute("SELECT sensitive_override FROM photos WHERE id=1").fetchone()[0] is None
        )

    def test_bulk_upsert_does_not_stomp_override(self, tmp_path) -> None:
        """Re-analysis must never reset a user's override — the column
        is deliberately NOT in the bulk_upsert write set."""
        from bpp.db.photos import bulk_upsert_photos, set_sensitive_override

        conn = _make_db(tmp_path)
        _insert_photo(conn, 1, 0.5)
        set_sensitive_override(conn, 1, 0)

        bulk_upsert_photos(
            conn,
            [{"filepath": "/photos/img_1.jpg", "nudity_score": 0.6}],
        )
        assert (
            conn.execute("SELECT sensitive_override FROM photos WHERE id=1").fetchone()[0] == 0
        ), "bulk upsert stomped the user's sensitive override"


class TestSensitiveSmartAlbum:
    def _album(self, conn):
        return conn.execute(
            "SELECT id, name FROM albums WHERE album_type='smart_sensitive'"
        ).fetchone()

    def test_album_appears_with_flagged_photos(self, tmp_path) -> None:
        from bpp.db.smart_album_sensitive import _refresh_sensitive_album

        conn = _make_db(tmp_path)
        _insert_photo(conn, 1, 0.8)  # flagged (>= 0.7 default)
        _insert_photo(conn, 2, 0.1)  # clean

        _refresh_sensitive_album(conn)
        album = self._album(conn)
        assert album is not None and album[1] == "Sensitive"
        members = [
            r[0]
            for r in conn.execute("SELECT photo_id FROM album_photos WHERE album_id=?", (album[0],))
        ]
        assert members == [1]

    def test_album_removed_when_nothing_flagged(self, tmp_path) -> None:
        from bpp.db.smart_album_sensitive import _refresh_sensitive_album

        conn = _make_db(tmp_path)
        _insert_photo(conn, 1, 0.8)
        _refresh_sensitive_album(conn)
        assert self._album(conn) is not None

        from bpp.db.photos import set_sensitive_override

        set_sensitive_override(conn, 1, 0)  # user: not sensitive
        _refresh_sensitive_album(conn)
        assert self._album(conn) is None, "album must disappear when the last flag is cleared"

    def test_override_adds_clean_photo_to_album(self, tmp_path) -> None:
        from bpp.db.photos import set_sensitive_override
        from bpp.db.smart_album_sensitive import _refresh_sensitive_album

        conn = _make_db(tmp_path)
        _insert_photo(conn, 1, 0.0)  # model says clean
        set_sensitive_override(conn, 1, 1)  # user says sensitive
        _refresh_sensitive_album(conn)
        album = self._album(conn)
        assert album is not None
        members = [
            r[0]
            for r in conn.execute("SELECT photo_id FROM album_photos WHERE album_id=?", (album[0],))
        ]
        assert members == [1]

    def test_registered_in_smart_album_registry(self) -> None:
        from bpp.db.smart_albums import SmartAlbumRegistry

        assert SmartAlbumRegistry.get("smart_sensitive") is not None


# ---------------------------------------------------------------------------
# 5. Configurable threshold (sensitive_nudity_threshold) — Python + SQL must
#    agree at WHATEVER threshold is in effect, and conn-only sites resolve it
#    from the settings table.
# ---------------------------------------------------------------------------


class TestConfigurableThreshold:
    def test_default_is_07(self) -> None:
        assert SENSITIVE_NUDITY_THRESHOLD == 0.7

    def test_baby_fp_band_not_flagged_at_default(self) -> None:
        """0.62 (the highest real-library false positive) is below 0.7."""
        assert is_sensitive_item({"nudity_score": 0.62}) is False
        assert is_sensitive_item({"nudity_score": 0.8}) is True  # genuine-explicit band

    def test_python_honors_explicit_threshold(self) -> None:
        item = {"nudity_score": 0.65}
        assert is_sensitive_item(item, 0.6) is True  # lowered cut catches it
        assert is_sensitive_item(item, 0.7) is False  # default does not

    def test_sql_and_python_agree_at_nondefault_threshold(self) -> None:
        """The matrix guard, but at a custom threshold — both derivations
        must flip together."""
        for score, threshold, expected in [
            (0.65, 0.6, True),
            (0.65, 0.7, False),
            (0.55, 0.5, True),
            (0.55, 0.6, False),
        ]:
            assert is_sensitive_item({"nudity_score": score}, threshold) is expected
            conn = sqlite3.connect(":memory:")
            conn.execute(
                "CREATE TABLE photos (id INTEGER PRIMARY KEY, nudity_score REAL,"
                " sensitive_override INTEGER)"
            )
            conn.execute("INSERT INTO photos VALUES (1, ?, NULL)", (score,))
            row = conn.execute(
                f"SELECT 1 FROM photos WHERE {sensitive_photo_sql(threshold)}"
            ).fetchone()
            assert (row is not None) is expected, (
                f"SQL/Python drift at score={score} threshold={threshold}"
            )

    def test_resolve_sensitive_threshold_from_settings(self, tmp_path) -> None:
        from bpp.db.settings import resolve_sensitive_threshold, set_setting

        conn = _make_db(tmp_path)
        # Unset → default.
        assert resolve_sensitive_threshold(conn) == SENSITIVE_NUDITY_THRESHOLD
        # Set → reads the stored value.
        set_setting(conn, "sensitive_nudity_threshold", 0.6)
        assert resolve_sensitive_threshold(conn) == 0.6
        # Garbage → default (never crashes the album refresh).
        set_setting(conn, "sensitive_nudity_threshold", "not-a-number")
        assert resolve_sensitive_threshold(conn) == SENSITIVE_NUDITY_THRESHOLD

    def test_album_membership_follows_configured_threshold(self, tmp_path) -> None:
        """End-to-end: a 0.65 photo is NOT in the album at the 0.7 default,
        but lowering the config threshold to 0.6 pulls it in — proving the
        SQL site reads the configured value."""
        from bpp.db.settings import set_setting
        from bpp.db.smart_album_sensitive import _refresh_sensitive_album

        conn = _make_db(tmp_path)
        _insert_photo(conn, 1, 0.65)  # in the baby-FP band

        _refresh_sensitive_album(conn)
        assert (
            conn.execute("SELECT id FROM albums WHERE album_type='smart_sensitive'").fetchone()
            is None
        ), "0.65 should not flag at the 0.7 default"

        set_setting(conn, "sensitive_nudity_threshold", 0.6)
        _refresh_sensitive_album(conn)
        album = conn.execute("SELECT id FROM albums WHERE album_type='smart_sensitive'").fetchone()
        assert album is not None, "lowering the threshold to 0.6 should flag the 0.65 photo"

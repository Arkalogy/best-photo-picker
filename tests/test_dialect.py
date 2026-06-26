"""Tests for the database dialect abstraction.

Pins:
- The SQLite implementation produces correct fragments / runs PRAGMAs.
- The DBDialect ABC has the methods every backend must provide
  (regression for accidentally narrowing the interface).
- Defensive checks (column name validation, JSON path quote escape).
"""

from __future__ import annotations

import sqlite3
from abc import ABC
from typing import ClassVar

import pytest


@pytest.fixture
def conn(tmp_path):
    p = str(tmp_path / "d.db")
    c = sqlite3.connect(p)
    c.row_factory = sqlite3.Row
    return c


class TestSQLiteDialectFragments:
    def test_autoincrement_pk(self):
        from bpp.db.dialect import dialect

        assert dialect.autoincrement_pk() == "INTEGER PRIMARY KEY AUTOINCREMENT"

    def test_json_extract_basic(self):
        from bpp.db.dialect import dialect

        assert dialect.json_extract("exif_json", "$.gps_lat") == (
            "json_extract(exif_json, '$.gps_lat')"
        )

    def test_json_extract_escapes_single_quotes(self):
        """Defensive: even though no current call site does this, the
        fragment helper must escape to keep injection-safe."""
        from bpp.db.dialect import dialect

        out = dialect.json_extract("col", "$.it's")
        # Standard SQL string literal escape: doubled single quote
        assert out == "json_extract(col, '$.it''s')"

    def test_dialect_name(self):
        from bpp.db.dialect import dialect

        assert dialect.name == "sqlite"


class TestSQLiteDialectConnectionSetup:
    def test_setup_applies_pragmas(self, conn):
        from bpp.db.dialect import dialect

        dialect.setup_connection(conn)
        # All three PRAGMAs are observable via their query forms
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].upper() == "WAL"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        # busy_timeout is at least the default we set
        bt = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert bt >= 1000  # SQLite normalises to ms

    def test_quick_check_passes_on_clean_db(self, conn):
        from bpp.db.dialect import dialect

        # Empty fresh DB is by definition consistent
        assert dialect.quick_check(conn) is None

    def test_user_version_roundtrip(self, conn):
        from bpp.db.dialect import dialect

        assert dialect.get_user_version(conn) == 0
        dialect.set_user_version(conn, 42)
        assert dialect.get_user_version(conn) == 42


class TestSQLiteDialectIntrospection:
    def test_column_names_returns_columns(self, conn):
        from bpp.db.dialect import dialect

        conn.execute("CREATE TABLE foo (id INTEGER PRIMARY KEY, name TEXT, ts INTEGER)")
        cols = dialect.column_names(conn, "foo")
        assert cols == {"id", "name", "ts"}

    def test_column_names_empty_for_unknown_table(self, conn):
        from bpp.db.dialect import dialect

        # PRAGMA table_info on a missing table returns no rows — empty set
        assert dialect.column_names(conn, "nope") == set()

    def test_column_names_rejects_injection_attempt(self, conn):
        from bpp.db.dialect import dialect

        with pytest.raises(ValueError, match="Invalid table name"):
            dialect.column_names(conn, "foo; DROP TABLE bar; --")

    def test_database_path_returns_path(self, conn, tmp_path):
        from bpp.db.dialect import dialect

        path = dialect.database_path(conn)
        # Should match the file we created the connection against
        assert str(tmp_path / "d.db") in path or path == ""


class TestDBDialectABC:
    """The interface the codebase depends on. Adding a new dialect is
    a one-file PR — these tests pin which methods that file must
    implement."""

    REQUIRED_METHODS: ClassVar[set[str]] = {
        "setup_connection",
        "checkpoint",
        "quick_check",
        "get_user_version",
        "set_user_version",
        "column_names",
        "database_path",
        "autoincrement_pk",
        "json_extract",
    }

    def test_all_required_methods_are_abstract(self):
        from bpp.db.dialect import DBDialect

        # Every method in REQUIRED_METHODS must be marked @abstractmethod
        # so a half-implemented subclass fails at instantiation, not at
        # the first call site that hits the missing method.
        abstracts = set(DBDialect.__abstractmethods__)
        missing = self.REQUIRED_METHODS - abstracts
        assert not missing, (
            f"Methods missing from DBDialect's abstract surface: {missing}. "
            "Add @abstractmethod or extend the contract intentionally."
        )

    def test_dialect_is_a_dbdialect(self):
        from bpp.db.dialect import DBDialect, dialect

        assert isinstance(dialect, DBDialect)

    def test_dbdialect_is_abstract(self):
        from bpp.db.dialect import DBDialect

        # Instantiating the ABC directly must fail
        assert ABC in DBDialect.__mro__
        with pytest.raises(TypeError):
            DBDialect()  # type: ignore[abstract]

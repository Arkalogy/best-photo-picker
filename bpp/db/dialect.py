"""Database dialect abstraction.

bpp ships with SQLite. The codebase used to spell every dialect-
specific idiom inline — `INTEGER PRIMARY KEY AUTOINCREMENT`,
`json_extract(col, '$.key')`, `PRAGMA user_version`, etc — across 30+
files. This module collects every dialect-coupled operation behind
one interface so a future contributor adding Postgres / MySQL
support has exactly one file to subclass and a clear list of
methods to provide.

The pattern:

    from bpp.db.dialect import dialect

    sql = f"CREATE TABLE foo (id {dialect.autoincrement_pk()}, ...)"
    cols = dialect.column_names(conn, "foo")
    where = f"WHERE {dialect.json_extract('exif_json', '$.gps_lat')} IS NOT NULL"

`dialect` is a module-level singleton — `SQLiteDialect()` today.
A future Postgres swap replaces it with `PostgresDialect()` (e.g.,
configurable via `BPP_DB_DIALECT` env var). All call sites stay
the same.

See docs/security.md → "Extension hooks" for the broader OSS
extensibility story.
"""

from __future__ import annotations

import sqlite3
from abc import ABC, abstractmethod


class DBDialect(ABC):
    """Abstract dialect — the surface every backend must implement.

    Methods fall into three groups:
    1. Connection setup / lifecycle (WAL, busy timeout, integrity check)
    2. Schema introspection (user_version, column_names, database_path)
    3. SQL fragment helpers (autoincrement_pk, json_extract)
    """

    name: str  # short identifier for logging ("sqlite", "postgres", …)

    # ── Connection setup / lifecycle ─────────────────────────────────

    @abstractmethod
    def setup_connection(self, conn: sqlite3.Connection) -> None:
        """Apply per-connection settings (journal mode, foreign keys,
        busy timeout). Called once per new pool connection."""

    @abstractmethod
    def checkpoint(self, conn: sqlite3.Connection) -> None:
        """Force a WAL checkpoint / equivalent. Called at shutdown to
        keep the DB compact and consistent."""

    @abstractmethod
    def quick_check(self, conn: sqlite3.Connection) -> str | None:
        """Run an integrity check. Returns an error string on failure
        or None on success."""

    # ── Schema versioning + introspection ────────────────────────────

    @abstractmethod
    def get_user_version(self, conn: sqlite3.Connection) -> int:
        """Read the current schema version (used by migrations)."""

    @abstractmethod
    def set_user_version(self, conn: sqlite3.Connection, version: int) -> None:
        """Persist the schema version."""

    @abstractmethod
    def column_names(self, conn: sqlite3.Connection, table: str) -> set[str]:
        """Return the set of column names defined on `table`."""

    @abstractmethod
    def database_path(self, conn: sqlite3.Connection) -> str:
        """Return the on-disk path of the connected database (for
        workers spawning subprocesses that need their own connection)."""

    # ── SQL fragment helpers ─────────────────────────────────────────

    @abstractmethod
    def autoincrement_pk(self) -> str:
        """Column-definition fragment for an auto-incrementing primary
        key. SQLite: `INTEGER PRIMARY KEY AUTOINCREMENT`. Postgres:
        `BIGSERIAL PRIMARY KEY`."""

    @abstractmethod
    def json_extract(self, column: str, path: str) -> str:
        """Build an SQL fragment that extracts a JSON path from `column`.

        `path` is in JSONPath form (`$.foo.bar`). SQLite uses
        `json_extract(column, '$.foo.bar')`; Postgres would use
        `column #>> '{foo,bar}'` or similar. Returns the fragment
        suitable for SELECT/WHERE clauses.
        """


class SQLiteDialect(DBDialect):
    """The default. Today it's the only dialect, but the abstraction
    is what makes adding Postgres a one-file PR rather than a
    grep-and-replace through 30+ files."""

    name = "sqlite"

    # Tunable: SQLite's busy timeout in ms. Set generously to absorb
    # NAS / iCloud sync slowdowns.
    DEFAULT_BUSY_TIMEOUT_MS = 30_000

    def setup_connection(self, conn: sqlite3.Connection) -> None:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(f"PRAGMA busy_timeout={self.DEFAULT_BUSY_TIMEOUT_MS}")

    def checkpoint(self, conn: sqlite3.Connection) -> None:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def quick_check(self, conn: sqlite3.Connection) -> str | None:
        row = conn.execute("PRAGMA quick_check").fetchone()
        if row is None:
            return "no result from quick_check"
        result = row[0]
        return None if result == "ok" else str(result)

    def get_user_version(self, conn: sqlite3.Connection) -> int:
        row = conn.execute("PRAGMA user_version").fetchone()
        return int(row[0]) if row else 0

    def set_user_version(self, conn: sqlite3.Connection, version: int) -> None:
        # PRAGMA user_version doesn't accept a parameter binding —
        # has to be inline. Version is a Python int we control, so
        # no injection risk.
        conn.execute(f"PRAGMA user_version = {int(version)}")

    def column_names(self, conn: sqlite3.Connection, table: str) -> set[str]:
        # PRAGMA table_info doesn't accept parameter bindings. Validate
        # `table` to be a plain identifier to keep this safe even if
        # callers ever pass user-controlled input.
        if not table.replace("_", "").isalnum():
            raise ValueError(f"Invalid table name: {table!r}")
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}

    def database_path(self, conn: sqlite3.Connection) -> str:
        # Returns the path of the main attached database. SQLite-specific.
        row = conn.execute("PRAGMA database_list").fetchone()
        return str(row[2]) if row else ""

    def autoincrement_pk(self) -> str:
        return "INTEGER PRIMARY KEY AUTOINCREMENT"

    def json_extract(self, column: str, path: str) -> str:
        # `path` is JSONPath. Caller supplies it as a literal — escape
        # single quotes defensively even though our call sites all use
        # static path strings today.
        escaped = path.replace("'", "''")
        return f"json_extract({column}, '{escaped}')"


# Module-level singleton. Future Postgres support replaces this
# (e.g., factory keyed off an env var or config setting).
dialect: DBDialect = SQLiteDialect()

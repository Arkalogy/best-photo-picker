"""Remove triggers and views the app did not define from an opened database.

A library database can come from someone else, and a trigger stored in
it runs on the owner's own writes: one on ``share_devices`` can undo a
revoke, one on ``settings`` can turn sharing back on. Views can stand in
for tables. The app defines two triggers (on ``albums``) and no views, so
on open everything else is dropped.

A trigger is kept only when its whole definition is the app's, not when
it merely has an app trigger's name: ``CREATE TRIGGER IF NOT EXISTS``
would otherwise leave an impostor in place.
"""

from __future__ import annotations

import re
import sqlite3

from bpp.utils.logging import get_logger

log = get_logger(__name__)

_TRIGGER_RE = re.compile(r"CREATE TRIGGER IF NOT EXISTS\s+(\w+)(.*?\bEND)\s*;", re.S)


def _squeeze(sql: str) -> str:
    """A definition without its whitespace: the same trigger was written
    with different line breaks over time."""
    return re.sub(r"\s+", "", sql).rstrip(";")


def _own_triggers() -> dict[str, str]:
    from bpp.db.schema_indexes import INDEXES_SQL

    return {
        name: _squeeze(f"CREATE TRIGGER {name}{rest}")
        for name, rest in _TRIGGER_RE.findall(INDEXES_SQL)
    }


#: The only column defaults the app's ``share_devices`` table has. Any
#: other default (``trusted_at ... DEFAULT 1``) would decide trust for
#: rows the app inserts.
_DEVICE_DEFAULTS = {"name": "''", "ip_at_pair": "''", "prev_revoked": "0"}


def _device_table_is_foreign(conn: sqlite3.Connection) -> bool:
    """True when ``share_devices`` has a column default or a generated
    column the app's own definition doesn't."""
    for row in conn.execute("PRAGMA table_xinfo(share_devices)").fetchall():
        name, default, hidden = row[1], row[4], row[6]
        if hidden or (default is not None and _DEVICE_DEFAULTS.get(name) != str(default)):
            return True
    return False


def drop_foreign_schema_objects(conn: sqlite3.Connection) -> int:
    """Drop every view, every trigger that is not one of the app's own
    text for text, and a device table that isn't shaped like the app's
    (its rows go with it; the table is created again). Returns how many
    were dropped."""
    own = _own_triggers()
    rows = conn.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE type IN ('trigger', 'view')"
    ).fetchall()
    dropped = 0
    for kind, name, sql in rows:
        if kind == "trigger" and own.get(name) == _squeeze(sql or ""):
            continue
        quoted = '"' + str(name).replace('"', '""') + '"'
        conn.execute(f"DROP {'TRIGGER' if kind == 'trigger' else 'VIEW'} IF EXISTS {quoted}")
        log.warning(
            "Removed a %s this app did not define from the library database: %s", kind, name
        )
        dropped += 1
    if _device_table_is_foreign(conn):
        conn.execute("DROP TABLE share_devices")
        log.warning("Removed a device table this app did not define from the library database")
        dropped += 1
    if dropped:
        conn.commit()
    return dropped

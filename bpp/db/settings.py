"""Library-level settings stored in the DB."""

from __future__ import annotations

import sqlite3
from typing import Any


def get_all_settings(conn: sqlite3.Connection) -> dict[str, str]:
    """Return all settings as a dict."""
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    """Return a single setting value, or default if not found."""
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def resolve_sensitive_threshold(conn: sqlite3.Connection) -> float:
    """Resolve the runtime sensitive-flag threshold for a conn-only site.

    Reads the ``sensitive_nudity_threshold`` config value from the settings
    table (where Config.set persists it), falling back to the default
    SENSITIVE_NUDITY_THRESHOLD. Lets the raw-SQL sites (smart_album_sensitive,
    analyze_finalize) flag with the SAME threshold the Python derivation
    uses, so per-photo flag and album membership never drift.
    """
    from bpp.constants import SENSITIVE_NUDITY_THRESHOLD

    raw = get_setting(conn, "sensitive_nudity_threshold")
    if raw is None:
        return SENSITIVE_NUDITY_THRESHOLD
    try:
        return float(raw)
    except (TypeError, ValueError):
        return SENSITIVE_NUDITY_THRESHOLD


def set_setting(conn: sqlite3.Connection, key: str, value: Any) -> None:
    """Set a single setting (upsert)."""
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
    conn.commit()


def set_settings(conn: sqlite3.Connection, settings: dict[str, Any]) -> None:
    """Set multiple settings at once."""
    if not settings:
        return
    conn.executemany(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        [(key, str(value)) for key, value in settings.items()],
    )
    conn.commit()


def delete_setting(conn: sqlite3.Connection, key: str) -> None:
    """Remove a setting."""
    conn.execute("DELETE FROM settings WHERE key = ?", (key,))
    conn.commit()

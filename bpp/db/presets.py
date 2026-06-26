"""CRUD operations for presets."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from bpp.utils.json_utils import safe_json_loads


def save_preset(conn: sqlite3.Connection, name: str, settings: dict[str, Any]) -> None:
    """Save or overwrite a preset."""
    conn.execute(
        "INSERT INTO presets (name, settings_json) VALUES (?, ?)"
        " ON CONFLICT(name) DO UPDATE SET settings_json=excluded.settings_json",
        (name, json.dumps(settings)),
    )
    conn.commit()


def load_preset(conn: sqlite3.Connection, name: str) -> dict[str, Any] | None:
    """Load a saved scoring/selection preset by name.

    Returns the parsed settings dict (weights, k, dedup params, …) or
    None if no preset exists with that name. Corrupt JSON falls
    through to an empty dict via `safe_json_loads`.
    """
    row = conn.execute("SELECT settings_json FROM presets WHERE name=?", (name,)).fetchone()
    if row is None:
        return None
    return safe_json_loads(row[0], default={}, context="preset")


def list_presets(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return all presets, ordered by name.

    Each entry has `name`, `settings` (parsed dict), and `created_at`.
    Used by the Settings UI to render the preset picker.
    """
    rows = conn.execute(
        "SELECT name, settings_json, created_at FROM presets ORDER BY name"
    ).fetchall()
    return [
        {
            "name": r[0],
            "settings": safe_json_loads(r[1], default={}, context="preset"),
            "created_at": r[2],
        }
        for r in rows
    ]


def delete_preset(conn: sqlite3.Connection, name: str) -> bool:
    """Delete a preset. Returns True if it existed."""
    cur = conn.execute("DELETE FROM presets WHERE name=?", (name,))
    conn.commit()
    return cur.rowcount > 0

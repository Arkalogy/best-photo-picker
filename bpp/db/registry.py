"""Library registry: track known library paths across sessions."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from typing import Any


def get_registry_path() -> str:
    """Return path to the libraries.json registry file."""
    config_home = os.environ.get(
        "XDG_CONFIG_HOME", os.path.join(os.path.expanduser("~"), ".config")
    )
    return os.path.join(config_home, "bpp", "libraries.json")


def load_registry() -> dict[str, Any]:
    """Load the registry from disk. Returns default if missing or corrupted."""
    path = get_registry_path()
    if not os.path.exists(path):
        return {"libraries": [], "active": None}
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"libraries": [], "active": None}


def save_registry(data: dict[str, Any]) -> None:
    """Atomically write the registry to disk."""
    path = get_registry_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Write to temp file then rename for atomicity
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        os.unlink(tmp)
        raise


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def list_libraries() -> list[dict[str, Any]]:
    """Return all registered libraries sorted by last_opened descending."""
    data = load_registry()
    libs = data.get("libraries", [])
    libs.sort(key=lambda x: x.get("last_opened", ""), reverse=True)
    return libs


def add_library(path: str, name: str | None = None) -> dict[str, Any]:
    """Register a library path. If already registered, update last_opened.

    Only overwrites the name if an explicit *name* is provided.
    """
    path = os.path.abspath(path)
    data = load_registry()
    now = _now_iso()

    for lib in data["libraries"]:
        if lib["path"] == path:
            if name is not None:
                lib["name"] = name
            lib["last_opened"] = now
            save_registry(data)
            return lib

    # New entry — default name to folder basename
    if name is None:
        name = os.path.basename(path)

    entry = {
        "path": path,
        "name": name,
        "created_at": now,
        "last_opened": now,
    }
    data["libraries"].append(entry)
    save_registry(data)
    return entry


def remove_library(path: str) -> None:
    """Remove a library from the registry (does NOT delete from disk)."""
    path = os.path.abspath(path)
    data = load_registry()
    data["libraries"] = [lib for lib in data["libraries"] if lib["path"] != path]
    if data.get("active") == path:
        data["active"] = None
    save_registry(data)


def rename_library(path: str, name: str) -> None:
    """Rename a library in the registry."""
    path = os.path.abspath(path)
    data = load_registry()
    for lib in data["libraries"]:
        if lib["path"] == path:
            lib["name"] = name
            break
    save_registry(data)


def rename_library_path(old_path: str, new_path: str, name: str) -> None:
    """Update a library's path in the registry (after folder rename)."""
    old_path = os.path.abspath(old_path)
    new_path = os.path.abspath(new_path)
    data = load_registry()
    for lib in data["libraries"]:
        if lib["path"] == old_path:
            lib["path"] = new_path
            lib["name"] = name
            break
    if data.get("active") == old_path:
        data["active"] = new_path
    save_registry(data)


def get_active_library() -> str | None:
    """Return the path of the active library, or None."""
    data = load_registry()
    return data.get("active")


def set_active_library(path: str) -> None:
    """Set the active library and update its last_opened timestamp."""
    path = os.path.abspath(path)
    data = load_registry()
    data["active"] = path
    for lib in data["libraries"]:
        if lib["path"] == path:
            lib["last_opened"] = _now_iso()
            break
    save_registry(data)

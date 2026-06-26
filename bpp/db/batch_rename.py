"""Batch rename: pattern-based file renaming on disk + DB."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import sqlite3
import tempfile
from typing import Any

logger = logging.getLogger(__name__)

JOURNAL_FILENAME = "rename_journal.json"


def _journal_path(library_path: str) -> str:
    """Return the path to the rename journal file inside the library data dir."""
    data_dir = os.path.join(library_path, "data")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, JOURNAL_FILENAME)


def _write_journal(journal_path: str, entries: list[dict[str, Any]]) -> None:
    """Atomically write the rename journal before starting renames.

    Uses NamedTemporaryFile in the SAME directory as the destination
    so os.replace() is guaranteed atomic — same-filesystem rename. A
    naive `journal_path + ".tmp"` works on most setups but breaks on
    overlay/NFS mounts where the parent dir spans devices. os.replace
    instead of os.rename so an existing target is overwritten cleanly
    on POSIX + Windows."""
    journal_dir = os.path.dirname(journal_path)
    fd, tmp = tempfile.mkstemp(prefix=".rename_journal.", suffix=".tmp", dir=journal_dir)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(entries, f)
        os.replace(tmp, journal_path)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            os.remove(tmp)
        raise


def _remove_journal(journal_path: str) -> None:
    """Delete the rename journal after successful completion."""
    with contextlib.suppress(FileNotFoundError):
        os.remove(journal_path)


def recover_interrupted_rename(conn: sqlite3.Connection, library_path: str) -> list[dict[str, Any]]:
    """Check for a stale rename journal and revert incomplete disk renames.

    Because ``apply_rename`` commits the DB only after ALL disk renames
    succeed, a mid-rename crash leaves the DB with old paths but files
    renamed on disk.  This function reverts those disk renames to match
    the DB state.

    Returns a list of reverted entries (empty if no journal found).
    """
    jp = _journal_path(library_path)
    if not os.path.exists(jp):
        return []

    try:
        with open(jp) as f:
            entries: list[dict[str, Any]] = json.load(f)
    except (json.JSONDecodeError, OSError):
        logger.warning("Corrupt rename journal at %s — removing", jp)
        _remove_journal(jp)
        return []

    reverted: list[dict[str, Any]] = []
    for entry in entries:
        old_path = entry.get("old")
        new_path = entry.get("new")
        if not old_path or not new_path:
            continue
        # If new_path exists on disk but old_path does not, revert.
        if os.path.exists(new_path) and not os.path.exists(old_path):
            try:
                os.rename(new_path, old_path)
                reverted.append(entry)
                logger.info("Reverted interrupted rename: %s -> %s", new_path, old_path)
            except OSError as e:
                logger.error("Failed to revert rename %s -> %s: %s", new_path, old_path, e)

    _remove_journal(jp)
    if reverted:
        logger.info("Recovered %d interrupted renames from journal", len(reverted))
    return reverted


def parse_pattern(pattern: str) -> list[tuple[str, str]]:
    """Parse a rename pattern into tokens.

    Returns list of ("var", name) or ("lit", text) tuples.
    Example: "{date}_{name}" -> [("var", "date"), ("lit", "_"), ("var", "name")]
    """
    tokens: list[tuple[str, str]] = []
    i = 0
    while i < len(pattern):
        if pattern[i] == "{":
            closing = pattern.find("}", i)
            if closing == -1:
                # Unclosed brace — treat rest as literal
                tokens.append(("lit", pattern[i:]))
                break
            tokens.append(("var", pattern[i + 1 : closing]))
            i = closing + 1
        else:
            # Collect literal chars until next { or end
            j = i
            while j < len(pattern) and pattern[j] != "{":
                j += 1
            tokens.append(("lit", pattern[i:j]))
            i = j
    return tokens


def _resolve_var(var: str, photo: dict[str, Any], counter: int) -> str:
    """Resolve a single variable token."""
    date_str = photo.get("date") or ""

    if var == "name":
        fname = photo.get("original_filename") or os.path.basename(photo.get("filepath", ""))
        return os.path.splitext(fname)[0]
    elif var == "date":
        if date_str:
            return date_str[:10]
        return "unknown-date"
    elif var == "year":
        return date_str[:4] if len(date_str) >= 4 else "unknown"
    elif var == "month":
        return date_str[5:7] if len(date_str) >= 7 else "00"
    elif var == "day":
        return date_str[8:10] if len(date_str) >= 10 else "00"
    elif var.startswith("counter"):
        # counter:3 -> zero-padded to 3 digits
        match = re.match(r"counter:(\d+)", var)
        width = int(match.group(1)) if match else 1
        return str(counter).zfill(width)
    else:
        return f"{{{var}}}"


def build_rename_map(photos: list[dict[str, Any]], pattern: str) -> list[dict[str, Any]]:
    """Build a rename mapping from a pattern and photo list.

    Returns list of dicts with:
      - id: photo DB id
      - old_filepath: current path
      - new_filepath: proposed new path
      - new_filename: just the new filename
      - changed: whether the name actually changed
    """
    tokens = parse_pattern(pattern)
    if not tokens:
        return []

    mapping: list[dict[str, Any]] = []
    seen_names: dict[str, int] = {}  # track name collisions

    for i, photo in enumerate(photos):
        filepath = photo.get("filepath", "")
        directory = os.path.dirname(filepath)
        old_fname = os.path.basename(filepath)
        ext = os.path.splitext(old_fname)[1]

        # Build new name from tokens
        parts = []
        for ttype, tval in tokens:
            if ttype == "var":
                parts.append(_resolve_var(tval, photo, i + 1))
            else:
                parts.append(tval)

        new_base = "".join(parts)
        new_fname = new_base + ext

        # Resolve collisions
        if new_fname in seen_names:
            seen_names[new_fname] += 1
            suffix = seen_names[new_fname]
            new_fname = f"{new_base}_{suffix}{ext}"
        else:
            seen_names[new_fname] = 1

        new_filepath = os.path.join(directory, new_fname)
        changed = new_filepath != filepath

        mapping.append(
            {
                "id": photo.get("id"),
                "old_filepath": filepath,
                "new_filepath": new_filepath,
                "new_filename": new_fname,
                "changed": changed,
            }
        )

    return mapping


def apply_rename(
    conn: sqlite3.Connection,
    mapping: list[dict[str, Any]],
    library_path: str | None = None,
) -> list[dict[str, Any]]:
    """Execute renames on disk and in DB.

    Only renames entries where changed=True.
    If library_path is provided, both old and new paths must be within it.
    A journal file is written before starting so that incomplete renames
    can be reverted on next launch via ``recover_interrupted_rename()``.
    Returns list of results with success/error per file.
    """
    from pathlib import Path

    allowed_base = Path(library_path).resolve() if library_path else None
    results: list[dict[str, Any]] = []

    # Write journal before starting so crash recovery can revert.
    changed = [e for e in mapping if e.get("changed", False)]
    jp: str | None = None
    if library_path and changed:
        jp = _journal_path(library_path)
        journal_entries = [
            {"old": e["old_filepath"], "new": e["new_filepath"], "id": e.get("id")} for e in changed
        ]
        _write_journal(jp, journal_entries)

    for entry in mapping:
        if not entry.get("changed", False):
            continue

        old_path = entry["old_filepath"]
        new_path = entry["new_filepath"]
        photo_id = entry.get("id")

        # Path traversal check
        if allowed_base is not None:
            try:
                old_resolved = Path(old_path).resolve()
                new_resolved = Path(new_path).resolve()
                if not (
                    old_resolved.is_relative_to(allowed_base)
                    and new_resolved.is_relative_to(allowed_base)
                ):
                    results.append(
                        {
                            "id": photo_id,
                            "old": old_path,
                            "new": new_path,
                            "success": False,
                            "error": f"Path outside library: {new_path}",
                        }
                    )
                    continue
            except (ValueError, OSError) as e:
                results.append(
                    {
                        "id": photo_id,
                        "old": old_path,
                        "new": new_path,
                        "success": False,
                        "error": f"Invalid path: {e}",
                    }
                )
                continue

        if os.path.exists(new_path) and new_path != old_path:
            results.append(
                {
                    "id": photo_id,
                    "old": old_path,
                    "new": new_path,
                    "success": False,
                    "error": f"Target already exists: {new_path}",
                }
            )
            logger.warning("Skipping rename, target exists: %s", new_path)
            continue

        if not os.path.exists(old_path):
            results.append(
                {
                    "id": photo_id,
                    "old": old_path,
                    "new": new_path,
                    "success": False,
                    "error": f"File not found: {old_path}",
                }
            )
            logger.warning("File not found for rename: %s", old_path)
            continue

        try:
            os.rename(old_path, new_path)
        except OSError as e:
            results.append(
                {
                    "id": photo_id,
                    "old": old_path,
                    "new": new_path,
                    "success": False,
                    "error": str(e),
                }
            )
            logger.warning("Failed to rename %s -> %s: %s", old_path, new_path, e)
            continue

        try:
            conn.execute(
                "UPDATE photos SET filepath = ? WHERE id = ?",
                (new_path, photo_id),
            )
        except Exception as e:
            # DB update failed — revert file rename to stay consistent
            try:
                os.rename(new_path, old_path)
            except OSError:
                logger.error("CRITICAL: Could not revert rename %s -> %s", new_path, old_path)
            results.append(
                {
                    "id": photo_id,
                    "old": old_path,
                    "new": new_path,
                    "success": False,
                    "error": f"DB update failed: {e}",
                }
            )
            logger.warning("DB update failed for rename %s -> %s: %s", old_path, new_path, e)
            continue

        results.append(
            {
                "id": photo_id,
                "old": old_path,
                "new": new_path,
                "success": True,
                "error": None,
            }
        )
        logger.info("Renamed %s -> %s", old_path, new_path)

    # Single commit for all successful renames
    conn.commit()

    # All renames committed — remove the journal.
    if jp:
        _remove_journal(jp)

    return results

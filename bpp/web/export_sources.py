"""The folders outside a library that its exports may read, remembered.

Photos analyzed in place stay where they are; an export reads them from
there. Which folders those are is the owner's decision (they asked for
the analysis), so it is kept with this installation's own files, beside
``libraries.json``, and never in the library: a library's rows can name
any path.

One private file, ``export-sources.json``: for each library path, which
database file was there, the folders analyzed in place and whether
links inside them were followed. Another library put at the same path
(a different database file) does not inherit the folders.
A file that can't be read or saved never stops the app: the folders of
this run are still held in memory, and analyzing a folder again adds it
back.
"""

from __future__ import annotations

import json
import os
import threading

from bpp.utils.logging import get_logger

log = get_logger(__name__)

#: Per library. The oldest are dropped beyond this.
MAX_FOLDERS = 200

_lock = threading.Lock()


def _file() -> str:
    from bpp.web.share_seal_store import _folder

    return os.path.join(_folder(), "export-sources.json")


def _key(library_path: str) -> str:
    return os.path.realpath(library_path)


def _identity(library_path: str) -> list[int] | None:
    """Which database file the library at this path has: its inode and,
    where the system records it, when it was created. (Not the device
    number: an external drive gets a new one each time it is mounted.)"""
    try:
        st = os.stat(os.path.join(library_path, "data", "photopicker.db"))
    except (OSError, ValueError):
        return None
    return [st.st_ino, int(getattr(st, "st_birthtime", 0))]


def _load() -> dict[str, dict]:
    from bpp.utils.json_utils import load_json_file

    data = load_json_file(_file(), {}, expected_type=dict, context="export source folders")
    out: dict[str, dict] = {}
    for lib, entry in data.items():
        folders = entry.get("folders") if isinstance(entry, dict) else None
        if isinstance(lib, str) and isinstance(folders, dict):
            out[lib] = {
                "id": entry.get("id"),
                "folders": {f: bool(v) for f, v in folders.items() if isinstance(f, str)},
            }
    return out


def _folders_of(data: dict[str, dict], library_path: str) -> dict[str, bool]:
    entry = data.get(_key(library_path))
    if entry is None or entry["id"] != _identity(library_path):
        return {}
    return entry["folders"]


def remembered(library_path: str | None) -> tuple[set[str], set[str]]:
    """``(folders, followed)`` for a library: every folder analyzed in
    place while it was open, and those of them analyzed with links
    followed."""
    if not library_path:
        return set(), set()
    with _lock:
        folders = _folders_of(_load(), library_path)
    return set(folders), {f for f, followed in folders.items() if followed}


def remember(library_path: str | None, folder: str, *, followed: bool) -> None:
    """Record that the owner analyzed *folder* in place for this library."""
    if not library_path or not folder:
        return
    from bpp.web.share_seal_store import _write_private

    with _lock:
        data = _load()
        folders = dict(_folders_of(data, library_path))
        if folders.get(folder) == followed:
            return
        data[_key(library_path)] = {"id": _identity(library_path), "folders": folders}
        folders.pop(folder, None)  # re-added last: the most recent is kept longest
        folders[folder] = followed
        for old in list(folders)[:-MAX_FOLDERS]:
            del folders[old]
        try:
            _write_private(_file(), json.dumps(data), once=False)
        except OSError as exc:
            log.warning(
                "Can't save the folders exports may read (%s); "
                "after a restart, analyze the folder again before exporting from it",
                exc,
            )

"""Copies of photo data the app keeps beside the database, and their end.

Analysis results are also written to ``analysis.json`` (in the library's
data folder; older versions wrote it to the library root) and to
``analysis_cache.db``, and archives are unpacked into ``extracted/``.
They hold the paths, capture times and coordinates of photos (and, for
``extracted/``, the photos themselves). When a photo is permanently
deleted, or the library is cleared, these go with it: an entry left in
``analysis.json`` is also what would bring a deleted photo back at the
next start.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import sqlite3
import threading

from bpp.db.sql_batches import in_batches
from bpp.utils.fresh_file import replace_fresh
from bpp.utils.json_utils import load_json_file
from bpp.utils.logging import get_logger
from bpp.utils.retry import retry_io

log = get_logger(__name__)

ANALYSIS_JSON = "analysis.json"
ANALYSIS_CACHE = "analysis_cache.db"
EXTRACTED_DIR = "extracted"

#: One rewrite of an analysis file at a time in this process.
_rewrite_lock = threading.Lock()


def forget_deleted(
    conn: sqlite3.Connection,
    workdir: str | None,
    filepaths: list[str],
    library_path: str | None = None,
) -> None:
    """After a permanent delete: drop from the analysis stores every one
    of *filepaths* that no longer has a row. Decided by the database, not
    by whether the file could be removed: a photo whose row is gone is
    deleted, even when its file is still waiting to be unlinked."""
    if not filepaths:
        return
    still_there: set[str] = set()
    for marks, batch in in_batches(filepaths):
        still_there.update(
            r[0]
            for r in conn.execute(f"SELECT filepath FROM photos WHERE filepath IN ({marks})", batch)
        )
    forget_analysis(workdir, [p for p in filepaths if p not in still_there], library_path)


def forget_interrupted_deletes(
    conn: sqlite3.Connection, workdir: str | None, library_path: str | None = None
) -> None:
    """Before anything is imported from the analysis file at start-up:
    drop what a permanent delete that was cut short had already deleted.
    Its rows went first and its own clean-up never ran, so the file
    still lists those photos and the import would bring them back."""
    from bpp.db.journal import pending_journals

    named: set[str] = set()
    for entry in pending_journals(conn, "permanent_delete"):
        payload = entry.get("payload") or {}
        for part in (payload.get("filepaths"), payload.get("files")):
            if isinstance(part, (list, dict)):
                named.update(fp for fp in part if isinstance(fp, str))
    forget_deleted(conn, workdir, sorted(named), library_path)


def forget_analysis(
    workdir: str | None, filepaths: list[str], library_path: str | None = None
) -> None:
    """Remove *filepaths* from the analysis stores. Best-effort: a store
    that can't be updated is logged, and the delete the caller just did
    stands."""
    if not filepaths:
        return
    gone = set(filepaths)
    for folder in {d for d in (workdir, library_path) if d}:
        _forget_in_json(os.path.join(folder, ANALYSIS_JSON), gone)
    if workdir:
        _forget_in_cache(os.path.join(workdir, ANALYSIS_CACHE), sorted(gone))


def _forget_in_json(path: str, gone: set[str]) -> None:
    if not os.path.isfile(path) or os.path.islink(path):
        return
    with _rewrite_lock:
        if not _may_name_any(path, gone):
            return
        data = load_json_file(path, None, expected_type=list, context=path)
        if data is None:
            return
        kept = [d for d in data if not (isinstance(d, dict) and d.get("filepath") in gone)]
        if len(kept) == len(data):
            return
        try:
            # A part file of this thread's own: two rewrites never share one.
            part = f"{path}.{os.getpid()}.{threading.get_ident()}.part"
            with replace_fresh(path, "w", part=part) as f:
                json.dump(kept, f)
        except OSError:
            log.warning("Could not remove deleted photos from %s", path, exc_info=True)


#: Looking for a path costs a pass over the file; a parse costs about ten.
_SCAN_AT_MOST = 4


def _may_name_any(path: str, gone: set[str]) -> bool:
    """A cheap look before the file is parsed (it can be hundreds of
    megabytes, and most deletes are of photos it does not list): does
    it contain any of the paths, as JSON writes them? Read in pieces,
    never whole."""
    needles = set()
    for fp in gone:
        for ascii_only in (True, False):
            needles.add(json.dumps(fp, ensure_ascii=ascii_only).encode("utf-8", "surrogatepass"))
    if not needles or len(gone) > _SCAN_AT_MOST:
        return True  # more than a few paths: parsing once is the cheaper way
    longest = max(len(n) for n in needles)
    try:
        with open(path, "rb") as f:
            tail = b""
            while chunk := f.read(1 << 20):
                window = tail + chunk
                if any(n in window for n in needles):
                    return True
                tail = window[-longest:]
    except OSError:
        return True  # let the load report it
    return False


def _erasing(path: str) -> sqlite3.Connection:
    """The cache database, set to overwrite what it deletes: a deleted
    row otherwise stays readable in the file's free pages."""
    from bpp.db.connection import get_db

    conn = get_db(path)
    conn.execute("PRAGMA secure_delete = ON")
    return conn


def _settle(conn: sqlite3.Connection) -> None:
    """Fold the write-ahead log into the file and empty it: until then
    the log still holds the pages as they were before the delete."""
    with contextlib.suppress(sqlite3.Error):
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def _forget_in_cache(path: str, gone: list[str]) -> None:
    if not os.path.isfile(path) or os.path.islink(path):
        return
    try:
        conn = _erasing(path)
        for marks, batch in in_batches(gone):
            conn.execute(f"DELETE FROM image_analysis WHERE filepath IN ({marks})", batch)
        conn.commit()
        _settle(conn)
    except sqlite3.Error:
        log.warning("Could not remove deleted photos from %s", path, exc_info=True)


def forget_all_analysis(workdir: str | None, library_path: str | None = None) -> None:
    """Clear Library: the analysis stores and everything unpacked from
    archives go too. A link at one of those names is removed, never
    followed."""
    for folder in {d for d in (workdir, library_path) if d}:
        results = os.path.join(folder, ANALYSIS_JSON)
        if os.path.lexists(results):
            try:
                os.unlink(results)
            except OSError:
                log.warning("Could not remove %s", results, exc_info=True)
    if not workdir:
        return
    _empty_cache(os.path.join(workdir, ANALYSIS_CACHE))
    extracted = os.path.join(workdir, EXTRACTED_DIR)
    if os.path.islink(extracted):
        with contextlib.suppress(OSError):
            os.unlink(extracted)
    elif os.path.isdir(extracted):
        try:
            retry_io(shutil.rmtree, extracted, label="rmtree_extracted")
        except OSError:
            log.warning("Could not remove %s", extracted, exc_info=True)


def _empty_cache(path: str) -> None:
    """Empty the analysis cache in place (this process may hold it open:
    removing the file would leave that handle writing into nothing)."""
    if os.path.islink(path):
        with contextlib.suppress(OSError):
            os.unlink(path)
        return
    if not os.path.isfile(path):
        return
    try:
        conn = _erasing(path)
        conn.execute("DELETE FROM image_analysis")
        conn.commit()
        with contextlib.suppress(sqlite3.Error):
            conn.execute("VACUUM")  # give the emptied pages back
        _settle(conn)
    except sqlite3.Error:
        log.warning("Could not empty %s", path, exc_info=True)


def forget_database_copies(db_path: str) -> int:
    """Clear Library: the copies of the database kept beside it (the
    start-up backups, the ones set aside before a restore or a reset,
    damaged ones kept for inspection) still hold every photo's path,
    date and location. Remove them and take one fresh backup of the
    now empty database. Returns how many were removed."""
    from bpp.db.connection import backup_db

    folder, name = os.path.split(db_path)
    removed = 0
    try:
        entries = os.listdir(folder)
    except OSError:
        return 0
    for entry in entries:
        # "<db>." copies only: "<db>-wal" / "-shm" belong to the live
        # database, and a pending-restore marker is not a copy.
        if not entry.startswith(name + ".") or entry.startswith(name + ".restore-pending"):
            continue
        path = os.path.join(folder, entry)
        if os.path.isdir(path) and not os.path.islink(path):
            continue
        try:
            os.unlink(path)
            removed += 1
        except OSError:
            log.warning("Could not remove the database copy %s", path, exc_info=True)
    _scrub(db_path)
    backup_db(db_path)
    return removed


def _scrub(db_path: str) -> None:
    """Rewrite the database file without the pages its deleted rows
    left behind, and empty its write-ahead log: until then the "empty"
    database (and a backup copied from it) still holds their text."""
    from bpp.db.connection import get_db

    try:
        conn = get_db(db_path)
        conn.commit()
        conn.execute("VACUUM")
        _settle(conn)
    except sqlite3.Error:
        log.warning("Could not compact %s after clearing it", db_path, exc_info=True)

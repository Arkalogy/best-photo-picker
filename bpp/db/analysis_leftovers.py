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
import stat
import threading

from bpp.db.sql_batches import in_batches
from bpp.scoring.aggregate import ANALYSIS_FILE_LOCK, DB_NAME
from bpp.utils.fresh_file import replace_fresh
from bpp.utils.logging import get_logger
from bpp.utils.retry import retry_io

log = get_logger(__name__)

ANALYSIS_JSON = "analysis.json"
ANALYSIS_CACHE = DB_NAME  # the name its writers use
EXTRACTED_DIR = "extracted"

#: One writer of an analysis file at a time in this process (the
#: analysis that writes it takes the same lock).
_rewrite_lock = ANALYSIS_FILE_LOCK


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
    drop what a permanent delete that was cut short had already deleted
    (its rows went first and its own clean-up never ran), and finish any
    rewrite of the file that was still waiting. Otherwise the file still
    lists those photos and the import would bring them back."""
    from bpp.db.journal import pending_journals

    named: set[str] = set()
    for entry in pending_journals(conn, "permanent_delete"):
        payload = entry.get("payload") or {}
        for part in (payload.get("filepaths"), payload.get("files")):
            if isinstance(part, (list, dict)):
                named.update(fp for fp in part if isinstance(fp, str))
    forget_deleted(conn, workdir, sorted(named), library_path)
    apply_pending_forgets(workdir, library_path)


def forget_analysis(
    workdir: str | None, filepaths: list[str], library_path: str | None = None
) -> None:
    """Remove *filepaths* from the analysis stores. Best-effort: a store
    that can't be updated is logged, and the delete the caller just did
    stands.

    The cache is updated at once. The analysis file can be hundreds of
    megabytes and is rewritten whole, so its part is noted in a small
    file beside it and done in the background; a rewrite that the
    process did not live to finish is done at the next start, before
    the file is read (:func:`forget_interrupted_deletes`)."""
    if not filepaths:
        return
    gone = sorted(set(filepaths))
    if workdir:
        _forget_in_cache(os.path.join(workdir, ANALYSIS_CACHE), gone)
    folders = [d for d in (workdir, library_path) if d]
    if not any(os.path.isfile(os.path.join(d, ANALYSIS_JSON)) for d in folders):
        return
    if not workdir or not _note_pending(workdir, gone):
        with _rewrite_lock:  # nowhere to note it: do it now
            _forget_in_json_files(folders, set(gone))
        return
    _start_applying(workdir, library_path)


# ── the analysis file: noted now, rewritten in the background ────────────

PENDING = "analysis.forget"
#: Suffix of the file a rewrite is written to before it replaces the original.
PART = ".part"

#: Guards the pending file (appends are quick; never held across a rewrite).
_pending_lock = threading.Lock()
_appliers: dict[tuple[str, str | None], threading.Thread] = {}


def _note_pending(workdir: str, gone: list[str]) -> bool:
    """Append the paths to the pending file, one JSON string per line."""
    try:
        with _pending_lock, open(os.path.join(workdir, PENDING), "a", encoding="utf-8") as f:
            f.writelines(json.dumps(fp) + "\n" for fp in gone)
            f.flush()
            os.fsync(f.fileno())
        return True
    except OSError:
        log.warning("Could not note deleted photos in %s", workdir, exc_info=True)
        return False


def path_key(path: str) -> str:
    """How deleted paths are matched: ``/a/./b.jpg`` and ``/a//b.jpg``
    are ``/a/b.jpg``. Not case-folded: on a volume that tells cases
    apart they are different files."""
    return os.path.normpath(path)


def _names_one_of(entry: object, keys: set[str]) -> bool:
    fp = entry.get("filepath") if isinstance(entry, dict) else None
    return isinstance(fp, str) and path_key(fp) in keys


def without_pending(rows: list, *folders: str | None) -> list:
    """*rows* (entries of an analysis file) without the photos noted as
    deleted for good in *folders*: every reader of that file uses this."""
    gone = pending_forgets(*folders)
    return [d for d in rows if not _names_one_of(d, gone)] if gone else rows


def drop_notes_for_present_files(workdir: str | None, listed: list) -> None:
    """A new analysis file was just written listing *listed*: a photo
    noted as deleted that is on disk again (put back from a backup and
    analysed) is not deleted any more. Its note goes, so the next start
    does not remove it from the file. One still missing keeps its note."""
    if not workdir:
        return
    path = os.path.join(workdir, PENDING)
    with _pending_lock:
        gone, _size = _read_pending(path)
        if not gone:
            return
        back = {
            fp
            for fp in (d.get("filepath") for d in listed if isinstance(d, dict))
            if isinstance(fp, str)
            and path_key(fp) in {path_key(g) for g in gone}
            and os.path.exists(fp)
        }
        if not back:
            return
        keys = {path_key(fp) for fp in back}
        kept = [g for g in sorted(gone) if path_key(g) not in keys]
        try:
            if kept:
                with replace_fresh(path, "w") as f:
                    f.writelines(json.dumps(g) + "\n" for g in kept)
            else:
                os.unlink(path)
        except OSError:
            log.warning("Could not update %s", path, exc_info=True)


def pending_forgets(*folders: str | None) -> set[str]:
    """The paths noted as deleted whose removal from the analysis file
    has not been done yet (in any of *folders*). An importer of that
    file leaves them out."""
    gone: set[str] = set()
    with _pending_lock:
        for folder in folders:
            if folder:
                gone |= _read_pending(os.path.join(folder, PENDING))[0]
    return {path_key(p) for p in gone}


def _read_pending(path: str) -> tuple[set[str], int]:
    """The noted paths, and how many bytes of the file they are."""
    from bpp.utils.json_utils import safe_json_loads

    try:
        # A regular file only: a pipe or a link left at this name in a
        # library folder from elsewhere would block or lead out.
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return set(), 0
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return set(), 0
    gone = set()
    for line in raw.splitlines():
        fp = (
            safe_json_loads(line, None, context=PENDING, expected_type=str)
            if line.strip()
            else None
        )
        if isinstance(fp, str):
            gone.add(fp)
    return gone, len(raw)


def apply_pending_forgets(workdir: str | None, library_path: str | None = None) -> None:
    """Rewrite the analysis files without the paths noted as deleted,
    then drop the note. Safe to run again: paths noted meanwhile stay."""
    if not workdir:
        return
    path = os.path.join(workdir, PENDING)
    if os.path.islink(path) or not os.path.isfile(path):
        return
    with _rewrite_lock:
        with _pending_lock:
            gone, size = _read_pending(path)
        if gone and not _forget_in_json_files([d for d in (workdir, library_path) if d], gone):
            return  # keep the note: tried again at the next delete or start
        with _pending_lock:
            try:
                with open(path, "rb") as f:
                    f.seek(size)
                    later = f.read()
                if later:
                    with replace_fresh(path, "wb") as f:
                        f.write(later)
                else:
                    os.unlink(path)
            except OSError:
                log.warning("Could not update %s", path, exc_info=True)


def _start_applying(workdir: str, library_path: str | None) -> None:
    key = (workdir, library_path)
    with _pending_lock:
        running = _appliers.get(key)
        if running is not None and running.is_alive():
            # It may already have read the note: run once more after it.
            follow = threading.Thread(
                target=lambda: (running.join(), apply_pending_forgets(workdir, library_path)),
                name="analysis-forget",
                daemon=True,
            )
        else:
            follow = threading.Thread(
                target=apply_pending_forgets,
                args=(workdir, library_path),
                name="analysis-forget",
                daemon=True,
            )
        _appliers[key] = follow
    log.info("Removing deleted photos from the saved analysis in the background")
    follow.start()


def wait_for_forgets(timeout: float = 60.0) -> None:
    """Wait for background rewrites started so far (shutdown, tests)."""
    with _pending_lock:
        running = list(_appliers.values())
    for thread in running:
        thread.join(timeout)


def _forget_in_json_files(folders: list[str], gone: set[str]) -> bool:
    """True when every analysis file is now without *gone*."""
    return all([_forget_in_json(os.path.join(folder, ANALYSIS_JSON), gone) for folder in folders])


def _forget_in_json(path: str, gone: set[str]) -> bool:
    """Caller holds ``_rewrite_lock``."""
    from bpp.utils.json_utils import safe_json_loads

    if not os.path.isfile(path) or os.path.islink(path):
        return True
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        # Not read is not "nothing to remove": the note is kept.
        log.warning("Could not read %s to remove deleted photos", path, exc_info=True)
        return False
    data = safe_json_loads(raw, None, context=path, expected_type=list)
    del raw
    if data is None:
        return True  # not a list of photos: nothing in it is imported either
    keys = {path_key(p) for p in gone}
    kept = [d for d in data if not _names_one_of(d, keys)]
    if len(kept) == len(data):
        return True
    try:
        # One fixed name: a rewrite cut short leaves its part file where
        # the next one (or Clear Library) removes it.
        with replace_fresh(path, "w", part=path + PART) as f:
            f.write(json.dumps(kept))  # one C-encoded pass; json.dump is several times slower
        log.info("Removed %d deleted photos from %s", len(data) - len(kept), path)
        return True
    except OSError:
        log.warning("Could not remove deleted photos from %s", path, exc_info=True)
        return False


def _erasing(path: str) -> sqlite3.Connection:
    """The cache database, set to overwrite what it deletes: a deleted
    row otherwise stays readable in the file's free pages."""
    from bpp.db.connection import get_db

    conn = get_db(path)
    conn.execute("PRAGMA secure_delete = ON")
    return conn


def _settle(conn: sqlite3.Connection, tries: int = 1) -> None:
    """Fold the write-ahead log into the file and empty it: until then
    the log still holds the pages as they were before the delete."""
    import time

    for attempt in range(tries):
        try:
            row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        except sqlite3.Error:
            log.warning("Could not checkpoint the database", exc_info=True)
            return
        if not row or not row[0]:  # (busy, log pages, checkpointed)
            return
        if attempt + 1 < tries:
            time.sleep(0.2)  # a reader holds an older snapshot: let it finish
    log.warning(
        "The database's write-ahead log could not be emptied (it is in use); "
        "deleted rows stay in it until the next checkpoint"
    )


def _forget_in_cache(path: str, gone: list[str]) -> None:
    if not os.path.isfile(path) or os.path.islink(path):
        return
    conn = None
    try:
        conn = _erasing(path)
        for marks, batch in in_batches(gone):
            conn.execute(f"DELETE FROM image_analysis WHERE filepath IN ({marks})", batch)
        conn.commit()
        _settle(conn)
    except sqlite3.Error:
        log.warning("Could not remove deleted photos from %s", path, exc_info=True)
        if conn is not None:  # a pooled connection: never left mid-transaction
            with contextlib.suppress(sqlite3.Error):
                conn.rollback()


def forget_analysis_files(workdir: str | None, library_path: str | None = None) -> None:
    """Remove the analysis files (and the note of pending removals).
    Raises OSError when one can't be removed: with an empty database it
    would be imported whole at the next start, so the caller must not
    go on to empty the database. Waits for a rewrite in progress, which
    would otherwise put the file back."""
    with _rewrite_lock, _pending_lock:
        for folder in {d for d in (workdir, library_path) if d}:
            names = [ANALYSIS_JSON, ANALYSIS_JSON + PART]
            for name in [*names, PENDING] if folder == workdir else names:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(os.path.join(folder, name))


def forget_all_analysis(workdir: str | None, library_path: str | None = None) -> None:
    """Clear Library: the analysis stores and everything unpacked from
    archives go too. A link at one of those names is removed, never
    followed."""
    forget_analysis_files(workdir, library_path)
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
        _settle(conn, tries=10)
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
        _settle(conn, tries=10)
    except sqlite3.Error:
        log.warning("Could not compact %s after clearing it", db_path, exc_info=True)

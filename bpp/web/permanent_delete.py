"""The disk half of a permanent delete: remove the files and every
cached variant. Shared by the delete endpoint, its journal replay and
the 30-day auto purge, so it lives outside the endpoint file.

Order of a delete, so that no step can remove a file the owner did not
choose and no crash can lose one they still have:

1. **Journal** the selected files with their row id, their identity
   (inode, size, mtime) and the *claim name* each will get: a hidden
   sibling in the same folder, ``.<name>.bpp-trash-<token>``, with a
   token unique to this operation.
2. **Claim**: each file is renamed to its claim name. The rename is
   atomic, never crosses a volume (same folder), and from here on the
   file is ours by a name nobody else writes. The moved file is checked
   against the identity taken at selection; a stranger is put back,
   never over another file. A file that cannot be claimed keeps its
   row: it is reported, not deleted.
3. **DB delete** of the claimed recycle-bin rows.
4. **Reconcile** against the database, not against what step 3
   returned: a row (same id) that still exists gets its claimed file
   put back — never over a file that has since appeared at the path —
   and a row that is gone gets its claimed file unlinked.

A replay after a crash runs step 4 on the journaled records, whatever
was reached before the crash. Before any unlink the file under the
claim name is checked against the selection identity again: a stranger
that landed under our name (a rejected claim, a crash before the check)
is put back, never unlinked, whatever happened to the row. Whatever is still stuck (a put-back
blocked by an occupied path, an unlink the OS refuses) stays journaled
with the same records, retried at the next start a bounded number of
times, then logged by its claim name. Nothing is ever unlinked at the
original path: the only unlink is of a claim name derived from the
operation's own token, so a file that appears at the original path
later is never ours.

Accepted windows. The identity used to accept a claim is inode, size
and nanosecond mtime: a writer that replaces a binned file's bytes in
place *and* restores its exact mtime between selection and claim
(microseconds apart in one request) is not something this code can tell
from the selected file without reading every file first. And the claim
name is private to one operation (a hidden sibling carrying a 64-bit
random token): POSIX offers no "unlink this inode" call, so every unlink
of a claim name has a check-to-unlink window, and a writer that puts its
only copy of a file under that exact hidden name inside that window
loses it. No part of this app, and no ordinary tool (sync, backup,
Finder, a camera import), writes such a name; both windows are accepted.
"""

from __future__ import annotations

import errno
import os
import secrets
import sqlite3
import stat
import sys
from collections.abc import Callable
from typing import Any, NamedTuple

from bpp.utils.logging import get_logger

log = get_logger(__name__)


#: Starts after which a file the OS keeps refusing is given up on (logged
#: by its claim name) instead of being retried at every launch.
MAX_DELETE_RETRIES = 5

#: Marker in a claim name: ``.<original name>.bpp-trash-<token>``. Hidden
#: and without an image extension, so no scan ever imports one.
CLAIM_MARKER = ".bpp-trash-"

#: Per-file record in the journal payload (``files[path]``): ``id`` of
#: the row; ``ino``/``size``/``mtime_ns`` from the stat at selection
#: (absent when the file was already gone); ``claimed`` whether the
#: rename is known to have happened; ``skip_disk`` for a photo analysed
#: in place outside the library (row only, file never touched).
FileRecord = dict[str, Any]


class DeleteOutcome(NamedTuple):
    """What a whole delete did: rows gone, files removed, and the paths
    that still need attention (unclaimed, stuck, or refused by the OS)."""

    rows_deleted: int
    removed: int
    failed: list[str]


def claim_name(fp: str, token: str) -> str:
    """Where ``fp`` goes while it is being deleted: a hidden sibling."""
    folder, name = os.path.split(fp)
    return os.path.join(folder, f".{name}{CLAIM_MARKER}{token}")


def _stat_record(st: os.stat_result) -> FileRecord:
    return {"ino": int(st.st_ino), "size": int(st.st_size), "mtime_ns": int(st.st_mtime_ns)}


def _same_file(st: os.stat_result, rec: FileRecord) -> bool:
    return (
        int(st.st_ino) == int(rec.get("ino", -1))
        and int(st.st_size) == int(rec.get("size", -1))
        and int(st.st_mtime_ns) == int(rec.get("mtime_ns", -1))
    )


def plan_delete(
    path_ids: dict[str, int], token: str, allowed_dirs: list[str]
) -> dict[str, FileRecord]:
    """The journal's ``files`` map for a delete about to start. A photo
    analysed in place outside the library and workdir loses its row
    only (``skip_disk``). A file that cannot be stat'ed gets no record
    and keeps its row."""
    from bpp.utils.path_validation import is_path_under_any

    files: dict[str, FileRecord] = {}
    for fp, photo_id in path_ids.items():
        if not is_path_under_any(fp, allowed_dirs):
            log.warning("Skipping file outside library/workdir: %s", fp)
            files[fp] = {"id": int(photo_id), "skip_disk": True, "claimed": False}
            continue
        try:
            st = os.stat(fp)
        except FileNotFoundError:
            st = None  # nothing on disk: the row alone goes
        except OSError as e:
            log.warning("Could not check file %s; keeping it: %s", fp, e)
            continue
        rec: FileRecord = _stat_record(st) if st is not None else {}
        rec["id"] = int(photo_id)
        rec["claimed"] = False
        files[fp] = rec
    return files


def _rename_noreplace(src: str, dst: str) -> None:
    """Rename that fails with EEXIST when ``dst`` exists, atomically:
    ``renamex_np(RENAME_EXCL)`` on macOS, ``renameat2(RENAME_NOREPLACE)``
    on Linux. Raises ``OSError(ENOTSUP)`` where neither exists."""
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        fn, args = libc.renamex_np, (src.encode(), dst.encode(), 4)  # RENAME_EXCL
    elif sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        at_fdcwd = -100
        fn, args = libc.renameat2, (at_fdcwd, src.encode(), at_fdcwd, dst.encode(), 1)
    else:
        raise OSError(errno.ENOTSUP, "no atomic no-replace rename on this platform")
    if fn(*args) != 0:
        e = ctypes.get_errno()
        raise OSError(e, os.strerror(e), src, None, dst)


def _is_hard_link_pair(fp: str, claimed: str) -> bool:
    """Is the entry at ``fp`` the very same file as the claim name, as a
    second hard link? Only a regular file (not a symlink, which lstat
    does not follow) on the same device with the same non-zero inode,
    at least two links, and the same size and mtime counts. Anything
    unavailable (inode 0, a stat error) is not evidence."""
    try:
        a, b = os.lstat(fp), os.lstat(claimed)
    except OSError:
        return False
    return (
        stat.S_ISREG(a.st_mode)
        and stat.S_ISREG(b.st_mode)
        and a.st_ino != 0
        and (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)
        and b.st_nlink >= 2
        and (a.st_size, a.st_mtime_ns) == (b.st_size, b.st_mtime_ns)
    )


def _put_back(claimed: str, fp: str) -> str:
    """Move a claimed file back to its path without ever replacing a
    file that is there. A hard link refuses an existing destination
    atomically; where links are unsupported, an atomic no-replace
    rename; never a plain rename. Returns ``"back"`` (the file is at
    its path and the claim name is gone), ``"leftover"`` (the file is
    back but the claim name could not be removed: a second link to the
    same bytes, to be removed later), or ``"stuck"`` (the file stays
    under its claim name)."""
    try:
        os.link(claimed, fp)
    except FileExistsError:
        if _is_hard_link_pair(fp, claimed):
            os.unlink(claimed)  # linked back earlier; this is the extra name
            return "back"
        return "stuck"
    except FileNotFoundError:
        return "back" if not os.path.lexists(claimed) else "stuck"
    except OSError:
        try:
            _rename_noreplace(claimed, fp)
        except FileNotFoundError:
            return "back"
        except OSError as e:
            log.error("Could not put %s back: %s", fp, e)
            return "stuck"
        return "back"
    try:
        os.unlink(claimed)
    except FileNotFoundError:
        pass
    except OSError as e:
        log.warning("Put %s back but its claim name remains (%s); removed later", fp, e)
        return "leftover"
    return "back"


def claim(files: dict[str, FileRecord], token: str) -> list[str]:
    """Step 2: rename each selected file to its claim name, then check
    the claimed file against the selection identity. A stranger goes
    back (never over another file); a file the OS will not rename stays
    where it is. Returns the paths that keep their rows."""
    unclaimed: list[str] = []
    for fp, rec in files.items():
        if "ino" not in rec:
            continue  # absent at selection, or a row-only record
        name = claim_name(fp, token)
        try:
            os.rename(fp, name)
        except FileNotFoundError:
            continue  # gone since selection: nothing to claim
        except OSError as e:
            log.warning("Could not claim %s; keeping it: %s", fp, e)
            unclaimed.append(fp)
            continue
        rec["claimed"] = True
        try:
            st = os.stat(name)
        except OSError:
            st = None
        if st is None or not _same_file(st, rec):
            # Not the file that was selected: put it back untouched. If
            # that fails the record stays, so a replay keeps trying.
            log.warning("File at %s changed since it was selected; keeping it", fp)
            unclaimed.append(fp)
            rec["rejected"] = True  # never unlink it, whatever the row does
            if _put_back(name, fp) == "back":
                rec["claimed"] = False
            else:
                log.error("Could not put %s back; it stays at %s", fp, name)
                rec["stuck"] = True
    return unclaimed


class Reconciled(NamedTuple):
    removed: int
    stuck: list[str]  # put-backs that did not happen; rows still there
    failed: list[str]  # unlinks the OS refused; rows gone


def reconcile(
    ctx: object,
    conn: sqlite3.Connection,
    files: dict[str, FileRecord],
    token: str,
    sha256_map: dict,
    allowed_dirs: list[str],
) -> Reconciled:
    """Step 4, from the database's point of view: a record whose row
    (same id) still exists gets its claimed file put back; a record
    whose row is gone gets its claimed file unlinked — only while the
    file under the claim name is still the selected one (inode, size,
    mtime): a stranger under our name is put back, never unlinked.
    Cached variants of gone rows are swept. Idempotent, so a replay
    can run it again."""
    from bpp.db.photos import get_photo_id_map_by_paths
    from bpp.utils.path_validation import is_path_under_any

    still_rows = get_photo_id_map_by_paths(conn, list(files))
    removed = 0
    stuck: list[str] = []
    failed: list[str] = []
    thumbs = getattr(ctx, "thumbs", None)
    dirs = getattr(ctx, "dirs", None) or {}
    path_hashes: set[str] = set()
    for fp, rec in files.items():
        row_here = still_rows.get(fp) == rec.get("id")
        name = claim_name(fp, token)
        if "ino" in rec and not rec.get("skip_disk"):
            if not is_path_under_any(fp, allowed_dirs):
                # Moved outside the library since it was claimed: never
                # touched from here, but not forgotten either.
                log.warning("Claimed file %s is now outside the library; left as %s", fp, name)
                stuck.append(fp)
                continue
            # A rename just before a crash may not be journaled: the claim
            # name on disk is the truth.
            held = rec.get("claimed") or os.path.lexists(name)
            if held:
                try:
                    st: os.stat_result | None = os.stat(name)
                except FileNotFoundError:
                    st = None
                except OSError as e:
                    log.warning("Could not check %s: %s", name, e)
                    stuck.append(fp)
                    continue
                if st is not None:
                    ours = _same_file(st, rec) and not rec.get("rejected")
                    if row_here or not ours:
                        # The row is still there, or this is not the file
                        # that was selected: back it goes, never unlinked.
                        outcome = _put_back(name, fp)
                        if outcome == "stuck":
                            stuck.append(fp)
                        elif outcome == "leftover":
                            failed.append(fp)
                        continue
                    try:
                        os.unlink(name)
                        removed += 1
                    except FileNotFoundError:
                        pass
                    except OSError as e:
                        log.warning("Failed to remove file %s (as %s): %s", fp, name, e)
                        failed.append(fp)
        if row_here:
            continue
        if thumbs is not None:
            path_hash = thumbs.get_hash(fp)
            path_hashes.add(path_hash)
            thumbs.remove_for_hash(
                path_hash,
                content_hash=sha256_map.get(fp) if isinstance(sha256_map, dict) else None,
            )
    if thumbs is not None and path_hashes:
        # The crop folders once per purge, not two globs per photo
        # (a purge of thousands used to block startup for minutes).
        thumbs.remove_crops_for_hashes(dirs.get("face_crops"), dirs.get("pet_crops"), path_hashes)
    return Reconciled(removed, stuck, failed)


def retry_payload(
    payload: dict[str, Any], leftover: list[str], attempts: int
) -> dict[str, Any] | None:
    """The journal payload that retries what is still stuck at the next
    start — only those records, as taken, and only ``MAX_DELETE_RETRIES``
    times. None once given up (logged by claim name) or when nothing is
    left."""
    files = {fp: payload["files"][fp] for fp in leftover if fp in payload.get("files", {})}
    if not files:
        return None
    if attempts >= MAX_DELETE_RETRIES:
        log.error(
            "Giving up on %d files a permanent delete could not finish after %d starts: %s",
            len(files),
            attempts,
            ", ".join(claim_name(fp, payload["token"]) for fp in files),
        )
        return None
    log.warning(
        "%d files are not finished; retrying at the next start (attempt %d of %d)",
        len(files),
        attempts,
        MAX_DELETE_RETRIES,
    )
    return {
        **payload,
        "filepaths": sorted(files),
        "sha256_map": {fp: payload.get("sha256_map", {}).get(fp) for fp in files},
        "files": files,
        "attempts": attempts,
    }


def run_permanent_delete(
    ctx: object,
    conn: sqlite3.Connection,
    path_ids: dict[str, int],
    sha256_map: dict,
    allowed_dirs: list[str],
    delete_rows: Callable[[list[int]], list[str]],
) -> DeleteOutcome:
    """The whole delete, journaled: plan → claim → ``delete_rows(ids)`` →
    reconcile → journal closed (or rewritten as the retry).
    ``delete_rows`` runs the DB half for the claimed rows and returns
    the paths whose rows it removed (informational: the reconcile reads
    the database itself)."""
    from bpp.db.journal import journal_complete, journal_start, journal_update

    token = secrets.token_hex(8)
    payload: dict[str, Any] = {
        "filepaths": sorted(path_ids),
        "sha256_map": dict(sha256_map),
        "allowed_dirs": list(allowed_dirs),
        "token": token,
        "files": {},
        "attempts": 0,
    }
    journal_id = journal_start(conn, "permanent_delete", payload)
    files = plan_delete(path_ids, token, allowed_dirs)
    payload["files"] = files
    journal_update(conn, journal_id, payload)
    unclaimed = claim(files, token)
    unclaimed += [fp for fp in path_ids if fp not in files]
    # Rows to delete: every record that is claimed, absent on disk, or
    # row-only. An unclaimed file keeps its row; a stranger still stuck
    # under our claim name keeps its record so the replay keeps trying.
    deletable = {fp: rec for fp, rec in files.items() if fp not in unclaimed}
    for fp in unclaimed:
        if fp in files and not files[fp].get("stuck"):
            files.pop(fp)
    journal_update(conn, journal_id, payload)  # identities durable before any row goes

    def _close(leftover: list[str]) -> None:
        retry = retry_payload(payload, leftover, attempts=1)
        if retry is None:
            journal_complete(conn, journal_id)
        else:
            journal_update(conn, journal_id, retry)

    try:
        deleted = set(delete_rows([rec["id"] for rec in deletable.values()]))
    except BaseException:
        # Whatever the DB half did or did not do, the database knows.
        outcome = reconcile(ctx, conn, files, token, sha256_map, allowed_dirs)
        _close(outcome.stuck + outcome.failed)
        raise
    outcome = reconcile(ctx, conn, files, token, sha256_map, allowed_dirs)
    _close(outcome.stuck + outcome.failed)
    if unclaimed:
        log.warning("%d files could not be claimed and keep their rows", len(unclaimed))
    failed = sorted(set(outcome.stuck) | set(outcome.failed) | set(unclaimed))
    return DeleteOutcome(len(deleted), outcome.removed, failed)


def register_permanent_delete_recovery(ctx: object, library_path: str | None = None) -> None:
    """Bind a recovery handler for the 'permanent_delete' journal kind.

    Called once during WebAppState.startup() with the live ctx so the
    handler can reach `ctx.thumbs`, `ctx.dirs`, etc. Idempotent
    registration (the journal module itself rejects different handlers
    for the same kind, so re-running this with the same ctx is safe;
    re-running with a different ctx during library switch picks up
    the new ctx via closure).

    ``library_path`` (optional, but passed by the state lifecycle):
    when provided, the handler is wrapped with
    :func:`bpp.db.journal.library_bound_recovery` so a switch_library
    between registration and recovery refuses to delete files in the
    wrong library. Without this, the disk-deletion branch would resolve
    paths against the new library's allowlist and ``ctx.thumbs``, which
    could remove files that share names across libraries.
    """
    from bpp.db.journal import journal_start, library_bound_recovery, register_recovery_handler

    def _recover(conn: sqlite3.Connection, payload: dict) -> bool:
        files: dict[str, FileRecord] = dict(payload.get("files") or {})
        token = payload.get("token") or ""
        if not files or not token:
            # Nothing was claimed (a crash before the plan was journaled,
            # or an entry from a build without records): nothing on disk
            # is touched.
            if payload.get("filepaths"):
                log.warning(
                    "Permanent-delete entry names %d files without records; leaving them",
                    len(payload["filepaths"]),
                )
            return True
        log.info("Recovering interrupted permanent_delete for %d files", len(files))
        outcome = reconcile(
            ctx,
            conn,
            files,
            token,
            payload.get("sha256_map") or {},
            payload.get("allowed_dirs") or [],
        )
        leftover = outcome.stuck + outcome.failed
        if leftover:
            # A fresh entry naming only what is still stuck replaces this
            # one (returning True consumes it); both carry the records.
            attempts = int(payload.get("attempts") or 0) + 1
            retry = retry_payload(payload, leftover, attempts)
            if retry is not None:
                journal_start(conn, "permanent_delete", retry)
        return True

    if library_path is not None:

        def _live_library_path() -> str | None:
            paths = getattr(ctx, "paths", None)
            return getattr(paths, "library_path", None) if paths is not None else None

        handler = library_bound_recovery(
            library_path, _recover, library_path_getter=_live_library_path
        )
    else:
        handler = _recover
    register_recovery_handler("permanent_delete", handler, replace=True)

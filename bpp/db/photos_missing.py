"""Photo missing-on-disk detection + SHA-256 relocation.

Extracted from :mod:`bpp.db.photos` as part of the 500-LOC cap split.
The file-health check loop in the web layer marks photos whose files
can no longer be read; the relocation pass tries to recover them by
hashing the user's chosen ``search_dir`` and matching SHA-256.

Missing photos remain in the DB so re-importing reuses the same id;
they're excluded from active queries via ``ACTIVE_PHOTO_SQL``.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from dataclasses import dataclass

from bpp.db.sql_batches import in_batches
from bpp.utils.logging import get_logger
from bpp.utils.retry import retry_io

log = get_logger(__name__)


def mark_missing(conn: sqlite3.Connection, filepath: str) -> None:
    """Flag a photo as missing on disk.

    Set by the file-health check loop when a previously-imported file
    can't be read (e.g., NAS unmounted, file deleted out of band).
    Missing photos are excluded from active queries via
    `ACTIVE_PHOTO_SQL`. The DB row is kept so re-importing later
    re-uses the same id.
    """
    conn.execute("UPDATE photos SET missing=1 WHERE filepath=?", (filepath,))
    conn.commit()
    log.info("Marked photo as missing: %s", filepath)


#: Below this many rows the mass-flip guard doesn't apply (a tiny library
#: really can lose most of its photos to a cleanup).
MASS_FLIP_MIN_ROWS = 20
#: This many consecutive files with no answer from storage ends the scan.
UNANSWERED_LIMIT = 3


def file_present(filepath: str) -> bool | None:
    """True / False when the filesystem answered; None when it didn't
    (a transient error that outlasted the retries). ``os.path.exists``
    swallows errors into False, so only ``os.stat`` can tell "gone"
    from "unreachable right now"."""
    try:
        retry_io(os.stat, filepath, label="check_missing", max_retries=1)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError as exc:
        log.warning("Can't tell whether %s exists (%s); leaving it as is", filepath, exc)
        return None
    return True


@dataclass(frozen=True)
class MissingScan:
    """Outcome of one missing-file scan: the paths newly marked missing,
    and ``refused`` as ``(missing, total)`` when the scan declined to mark
    more than half the library at once."""

    newly_missing: list[str]
    refused: tuple[int, int] | None = None
    #: False when the scan was skipped (storage unreachable) or aborted
    #: (storage stopped answering): it says nothing about the library.
    completed: bool = True


def check_missing(
    conn: sqlite3.Connection, *, photos_dir: str | None = None, force: bool = False
) -> list[str]:
    """:func:`scan_missing`'s newly-missing paths (the historical shape)."""
    return scan_missing(conn, photos_dir=photos_dir, force=force).newly_missing


def scan_missing(
    conn: sqlite3.Connection, *, photos_dir: str | None = None, force: bool = False
) -> MissingScan:
    """Find photos whose files no longer exist on disk and mark them.

    Transient storage errors never count as missing. Unless ``force``,
    the scan is skipped when ``photos_dir`` isn't reachable, and refused
    when more than half the library would flip at once: that is an
    outage, not a cleanup, and rows marked missing vanish from every view.
    A refusal is reported in the result (and by /api/v1/health) so a
    real mass deletion isn't mistaken for a healthy library; the manual
    recheck (``force``) marks the files.
    """
    from bpp.utils.retry import check_storage_accessible

    if photos_dir and not force:
        access = check_storage_accessible(photos_dir)
        if not access["accessible"]:
            log.warning(
                "Skipping the missing-file scan: %s isn't reachable (%s)",
                photos_dir,
                access["error"],
            )
            return MissingScan([], completed=False)
    rows = conn.execute("SELECT filepath FROM photos WHERE missing=0").fetchall()
    newly_missing = []
    unanswered = 0
    for row in rows:
        present = file_present(row[0])
        if present is None:
            unanswered += 1
            if unanswered >= UNANSWERED_LIMIT:
                log.warning(
                    "Stopping the missing-file scan: %d files in a row got no answer from storage",
                    unanswered,
                )
                return MissingScan([], completed=False)
            continue
        unanswered = 0
        if present is False:
            newly_missing.append(row[0])
    if not force and len(rows) >= MASS_FLIP_MIN_ROWS and len(newly_missing) * 2 > len(rows):
        log.warning(
            "%d of %d photos look missing at once; treating that as a storage outage "
            "and not marking them (the manual recheck forces the scan)",
            len(newly_missing),
            len(rows),
        )
        return MissingScan([], refused=(len(newly_missing), len(rows)))
    if newly_missing:
        # Batched: a forced scan after an outage can name most of the
        # library, past SQLite's bound-variable limit in one statement.
        for placeholders, batch in in_batches(newly_missing):
            conn.execute(
                f"UPDATE photos SET missing=1 WHERE filepath IN ({placeholders})",
                batch,
            )
        conn.commit()
        log.info("Marked %d photos as missing", len(newly_missing))
    return MissingScan(newly_missing)


def relocate_missing(conn: sqlite3.Connection, search_dir: str) -> int:
    """Try to find missing photos by SHA-256 in search_dir.

    If a missing photo's hash matches a file at a new path, update the filepath
    and clear the missing flag. Returns the number of relocated files.
    """
    rows = conn.execute(
        "SELECT id, filepath, sha256 FROM photos WHERE missing=1 AND sha256 IS NOT NULL"
    ).fetchall()
    if not rows:
        return 0

    # Build a map of SHA-256 → new filepath by scanning the search directory
    hash_to_path: dict[str, str] = {}
    need_hashes = {r[2] for r in rows}  # Only compute hashes we care about
    skipped_count = 0
    for dirpath, _dirnames, filenames in os.walk(search_dir):
        for name in filenames:
            fp = os.path.join(dirpath, name)
            try:
                h = hashlib.sha256()
                with open(fp, "rb") as f:
                    for chunk in iter(lambda: f.read(8192), b""):
                        h.update(chunk)
                digest = h.hexdigest()
                if digest in need_hashes:
                    hash_to_path[digest] = fp
            except OSError:
                # Permission denied, broken symlink, etc. Track for a
                # single end-of-scan summary instead of silent skips —
                # otherwise a sudden drop in relocation success rate
                # has no breadcrumb.
                skipped_count += 1
                continue
        # Early exit if we found all missing files
        if len(hash_to_path) >= len(need_hashes):
            break
    if skipped_count > 0:
        log.info(
            "Hash-scan skipped %d inaccessible files under %s "
            "(permission denied / broken symlinks / etc.)",
            skipped_count,
            search_dir,
        )

    relocated = 0
    for row in rows:
        pid, old_fp, sha = row[0], row[1], row[2]
        new_fp = hash_to_path.get(sha)
        if new_fp and new_fp != old_fp and os.path.isfile(new_fp):
            conn.execute(
                "UPDATE photos SET filepath=?, missing=0 WHERE id=?",
                (new_fp, pid),
            )
            relocated += 1
    if relocated:
        conn.commit()
        log.info("Relocated %d missing photos via SHA-256 match", relocated)
    return relocated

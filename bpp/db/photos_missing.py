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


def check_missing(conn: sqlite3.Connection) -> list[str]:
    """Find photos whose files no longer exist on disk. Marks them and returns paths.

    Uses retry_io to avoid false positives from transient NAS errors.
    """
    rows = conn.execute("SELECT filepath FROM photos WHERE missing=0").fetchall()
    newly_missing = []
    for row in rows:
        fp = row[0]
        try:
            exists = retry_io(os.path.exists, fp, label="check_missing")
        except OSError:
            exists = False
        if not exists:
            newly_missing.append(fp)
    if newly_missing:
        placeholders = ", ".join(["?"] * len(newly_missing))
        conn.execute(
            f"UPDATE photos SET missing=1 WHERE filepath IN ({placeholders})",
            newly_missing,
        )
        conn.commit()
        log.info("Marked %d photos as missing", len(newly_missing))
    return newly_missing


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

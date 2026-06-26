"""DB integrity checks + restore-from-backup recovery.

Extracted from :mod:`bpp.db.connection` (LOC gate split, 2026-06-12).
Re-exported from connection so historical imports keep working.
"""

from __future__ import annotations

import os
import sqlite3

from bpp.constants import SQLITE_TIMEOUT_S
from bpp.db.dialect import dialect
from bpp.utils.logging import get_logger

_log = get_logger(__name__)


def check_integrity(db_path: str) -> bool:
    """Run an integrity check via the dialect.

    Returns True if the DB passes, False otherwise. Uses a temporary
    connection to avoid interfering with the pool.
    """
    if not os.path.isfile(db_path):
        return True  # No DB yet is fine
    try:
        conn = sqlite3.connect(db_path, timeout=SQLITE_TIMEOUT_S)
        try:
            err = dialect.quick_check(conn)
        finally:
            conn.close()
        if err is None:
            _log.info("DB integrity check passed: %s", db_path)
            return True
        _log.error("DB integrity check FAILED: %s — %s", db_path, err)
        return False
    except Exception as e:
        _log.error("DB integrity check error: %s — %s", db_path, e)
        return False


def full_integrity_check(db_path: str) -> tuple[bool, list[str]]:
    """Run ``PRAGMA integrity_check`` (the FULL version, not
    ``quick_check``). Returns ``(ok, errors)``.

    The Jun-2 demo lib incident: ``quick_check`` returned "ok" but
    ``integrity_check`` revealed dozens of "Page N: never used"
    entries. The quick check is a sanity probe; the full check is
    the diagnostic. Protection C runs the full version at startup
    so we catch this class before any endpoint does.

    ``ok=True`` means SQLite returned exactly one row containing
    "ok". Anything else (including no rows, or multiple rows with
    error descriptions) means the database is corrupt and we
    populate ``errors`` with the literal SQLite output for the log /
    UI.
    """
    if not os.path.isfile(db_path):
        return True, []
    try:
        conn = sqlite3.connect(db_path, timeout=SQLITE_TIMEOUT_S)
        try:
            rows = conn.execute("PRAGMA integrity_check").fetchall()
        finally:
            conn.close()
    except Exception as exc:
        _log.error("full_integrity_check raised: %s — %s", db_path, exc)
        return False, [f"check raised: {exc}"]
    if len(rows) == 1 and rows[0][0] == "ok":
        return True, []
    errors = [r[0] for r in rows if r and r[0] != "ok"]
    return False, errors


# Bug #9 / 256-d corruption: each row's ``embedding`` BLOB must be
# exactly this many bytes. SFace and dlib both produce 128-d float32
# = 512 bytes. Anything else is the SIGKILL-mid-write debris pattern.
_EMBEDDING_BYTES_EXPECTED = 128 * 4

# How many affected (id, filepath) tuples to name in the prune-summary
# log line. 20 keeps the log readable on a Jun-2-sized incident (136
# rows) while still giving support a handle to chase. Bigger libraries
# get a "+N more" suffix; the full list lives in the DB before the
# delete fires.
_PRUNE_LOG_SAMPLE = 20


def prune_corrupt_face_embeddings(db_path: str) -> int:
    """Scan ``face_embeddings`` and DELETE rows whose ``embedding``
    BLOB is missing (NULL) or the wrong size. Returns the number of
    pruned rows.

    Wrong-size BLOBs (we've seen 1024-byte garbage interleaved with
    valid 512-byte rows) crash the ``np.stack`` in cluster/recompute
    paths with ``ValueError: all input arrays must have the same
    shape``. NULL embeddings shouldn't exist per the schema, but a
    SIGKILL-mid-write can leave one behind and Protection A's
    read-side defense already returns None for them. Sweeping both
    in one pass keeps the table consistent with what the app
    actually reads.

    Skips silently when the table doesn't exist (fresh DB, or pre-v36
    migrated DB that hasn't reached the face_embeddings step yet).
    """
    if not os.path.isfile(db_path):
        return 0
    affected: list[tuple[int, str | None]] = []
    try:
        conn = sqlite3.connect(db_path, timeout=SQLITE_TIMEOUT_S)
        try:
            try:
                # SELECT the affected (face_embedding id, photo filepath)
                # tuples before the DELETE so a user noticing "face
                # data missing for photo X" after startup can grep one
                # log line and find the cause. Capped at _PRUNE_LOG_SAMPLE
                # so a library with thousands of bad rows doesn't
                # produce an unreadable summary line.
                try:
                    affected = list(
                        conn.execute(
                            "SELECT fe.id, p.filepath FROM face_embeddings fe "
                            "LEFT JOIN photos p ON p.id = fe.photo_id "
                            "WHERE fe.embedding IS NULL "
                            "   OR length(fe.embedding) != ? "
                            "LIMIT ?",
                            (
                                _EMBEDDING_BYTES_EXPECTED,
                                _PRUNE_LOG_SAMPLE + 1,
                            ),
                        )
                    )
                except sqlite3.OperationalError:
                    # The photos JOIN can fail (table missing in a
                    # half-migrated DB). Don't block the DELETE on
                    # cosmetic logging — record an empty sample and
                    # the warning below falls back to the count-only
                    # form.
                    affected = []
                # Catch NULL too: the schema declares embedding NOT
                # NULL, but a SIGKILL-mid-write could leave a half-
                # state behind, and Protection A's read-side defense
                # already returns None for NULL blobs. Sweep both in
                # one pass so the face count surfaces (Settings →
                # Storage, Activity) match the count the app actually
                # uses.
                cur = conn.execute(
                    "DELETE FROM face_embeddings WHERE embedding IS NULL OR length(embedding) != ?",
                    (_EMBEDDING_BYTES_EXPECTED,),
                )
                n = cur.rowcount or 0
                conn.commit()
            except sqlite3.OperationalError as exc:
                # Table not present yet — that's fine, nothing to prune.
                _log.debug("prune_corrupt_face_embeddings: %s", exc)
                return 0
        finally:
            conn.close()
    except Exception as exc:
        _log.warning("prune_corrupt_face_embeddings raised: %s", exc)
        return 0
    if n > 0:
        if affected:
            sample = affected[:_PRUNE_LOG_SAMPLE]
            more = max(0, n - len(sample))
            sample_str = ", ".join(
                f"{row[0]}={os.path.basename(row[1]) if row[1] else '<no-filepath>'}"
                for row in sample
            )
            tail = f" (+{more} more)" if more else ""
            _log.warning(
                "Pruned %d corrupt face_embeddings row(s) at startup "
                "(non-512-byte embedding BLOBs — likely SIGKILL-mid-write debris). "
                "Affected rows [id=filename]: %s%s",
                n,
                sample_str,
                tail,
            )
        else:
            _log.warning(
                "Pruned %d corrupt face_embeddings row(s) at startup "
                "(non-512-byte embedding BLOBs — likely SIGKILL-mid-write debris)",
                n,
            )
    return n

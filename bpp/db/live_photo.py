"""Live Photo sidecar detection and linking.

Background
----------
iPhone Live Photos export from iCloud / Google Photos / macOS Photos as
two files per shot:

  IMG_4214.HEIC   — the key still frame the photographer chose
  IMG_4214_1.HEIC — the motion-component sidecar (a still extracted
                    from the 1.5-second video clip)

Both files have the same perceptual hash (phash) because they are
visually identical.  Without explicit tracking, sidecars:

  * double the apparent library size
  * inflate the Duplicates smart album (every sidecar is a "duplicate"
    of its parent)
  * consume scoring compute for a photo nobody ever intended to keep

This module detects, links, and (optionally) filters these sidecars.

Detection assumptions
---------------------
A photo row is a Live Photo sidecar when ALL of the following hold:

  1. Its ``original_filename`` matches ``<base>_<N>.<ext>`` where
     N is a single digit 1-9 and the extension is the same as the
     base file's extension (case-insensitive).  Examples:
       IMG_4214_1.HEIC  (parent: IMG_4214.HEIC)
       DSC_0012_1.JPG   (parent: DSC_0012.JPG)
       a78f_1.HEIC      (parent: a78f.HEIC)

  2. A photo row exists in the SAME directory (``os.path.dirname``)
     with filename ``<base>.<ext>``.  This directory check prevents
     false positives when two unrelated batches happen to share a
     basename.

  3. The parent photo is NOT itself a sidecar (avoids transitive
     chains).

What is NOT treated as a sidecar:
  * Files where the ``_N`` suffix is part of a longer numeric sequence
    e.g. ``photo_12.jpg`` — only single digits are matched.
  * Files whose parent does not exist in the same library directory.
  * ``_N`` files with a DIFFERENT extension from the parent
    (e.g. IMG_4214.HEIC / IMG_4214_1.MOV are both sidecars of the
    same Live Photo but under separate extension groups — future work
    to link them; for now each is detected independently if its parent
    exists).
  * Burst-mode camera files with consecutive numbers in a different
    naming scheme (e.g. DSC_0878 / DSC_0879 — no ``_N`` suffix).

Import-time user choice
-----------------------
``import_folder`` accepts ``import_live_photo_sidecars: bool`` (default
``False``).  When False the sidecar files are filtered out at scan time
and never written to disk.  When True they are imported, linked, and
hidden from the grid via ``is_live_photo_sidecar = 1``.

The user's preference is stored in per-library settings under key
``"import_live_photo_sidecars"`` so it applies consistently to future
imports into the same library.
"""

from __future__ import annotations

import os
import re
import sqlite3

from bpp.db.sql_batches import in_batches
from bpp.dedupe.phash import hamming_distance
from bpp.utils.logging import get_logger

log = get_logger(__name__)

# When phash-confirmation is requested, a candidate is only hidden as a
# sidecar if its perceptual hash is within this Hamming distance of its
# parent's. Live Photo sidecars are byte-for-byte the same still frame as
# the parent, so a confirmed sidecar should score 0; a tiny budget (2 bits)
# absorbs re-encode/EXIF-rotation jitter without ever swallowing a genuinely
# different photo that merely shares the ``_N`` naming convention.
_SIDECAR_PHASH_MAX_HAMMING = 2

# Self-limiting pre-scan (see split_scan_for_confirmed_sidecars). The pre-scan
# pays one cheap decode per '_N' candidate + parent up front to save the much
# costlier scoring pass on confirmed sidecars. That bet wins on Live-Photo
# libraries (~all candidates confirm) but LOSES on burst-style libraries
# (DSC_1.jpg … DSC_9.jpg — sequential, distinct, none confirm): there the
# pre-scan decodes everything for no payoff and analyze ends up slower. So we
# probe the first _SIDECAR_PROBE_SIZE candidates and, if fewer than
# _SIDECAR_MIN_CONFIRM_RATE of them confirm, abort the rest — the remaining
# candidates flow straight to scoring undecoded. Worst-case wasted work is
# bounded to one probe slice instead of the whole library.
_SIDECAR_PROBE_SIZE = 200
_SIDECAR_MIN_CONFIRM_RATE = 0.2

# Matches "<anything>_<single digit>.<ext>" — the Apple Live Photo
# sidecar naming pattern.  Single digit only: avoids false positives
# for files like "vacation_12.jpg" which are sequential shots, not
# Live Photo companions.
_SIDECAR_RE = re.compile(
    r"^(?P<base>.+)_(?P<n>[1-9])(?P<ext>\.[^.]+)$",
    re.IGNORECASE,
)


def _phash_confirms_sidecar(
    cand_phash: int | None,
    cand_ahash: int | None,
    parent_phash: int | None,
    parent_ahash: int | None,
    max_hamming: int,
) -> bool:
    """True when the candidate's phash proves it's the parent's still frame.

    Requires both sides to have hashes; a missing hash means "can't confirm"
    (the phash backfill re-runs detection once hashes land), so we return
    False rather than risk hiding a unique photo.

    BOTH the dHash and the aHash must be within ``max_hamming`` bits — not the
    ``min()`` of the two that ``dual_hash_distance`` returns. ``min()`` is the
    right call for *near*-duplicate clustering (catch a match on whichever
    hash is more discriminating), but it's the wrong direction here: this gate
    decides whether to HIDE a photo, so we want maximum precision. A genuinely
    distinct ``_N`` photo should have to fool both fingerprints, not just one,
    before it disappears from the library. True Live Photo sidecars are
    byte-identical, so both hashes score 0 — the strict check costs them
    nothing.
    """
    if cand_phash is None or cand_ahash is None or parent_phash is None or parent_ahash is None:
        return False
    return (
        hamming_distance(cand_phash, parent_phash) <= max_hamming
        and hamming_distance(cand_ahash, parent_ahash) <= max_hamming
    )


def is_live_photo_sidecar_filename(filename: str) -> tuple[str, str] | None:
    """Return (parent_filename, ext) if filename looks like a sidecar, else None.

    >>> is_live_photo_sidecar_filename("IMG_4214_1.HEIC")
    ('IMG_4214.HEIC', '.HEIC')
    >>> is_live_photo_sidecar_filename("IMG_4214_12.HEIC")  # double digit
    >>> is_live_photo_sidecar_filename("IMG_4214.HEIC")
    >>> is_live_photo_sidecar_filename("vacation_1.jpg")
    ('vacation.jpg', '.jpg')
    """
    m = _SIDECAR_RE.match(filename)
    if not m:
        return None
    parent_filename = m.group("base") + m.group("ext")
    return parent_filename, m.group("ext")


def detect_and_link_live_photo_sidecars(
    conn: sqlite3.Connection,
    *,
    require_phash_match: bool = False,
    max_hamming: int = _SIDECAR_PHASH_MAX_HAMMING,
) -> int:
    """Scan the photos table and mark Live Photo sidecar rows.

    For each photo whose filename matches the sidecar pattern, look for
    the parent in the same directory.  If found, set::

        is_live_photo_sidecar = 1
        live_photo_parent_id  = <parent row id>

    Returns the number of rows updated.

    Idempotent: already-linked rows are skipped (WHERE clause on
    ``is_live_photo_sidecar = 0``).  Safe to call on every startup —
    only unprocessed candidates are touched.

    Performance note: the query pulls only candidate rows (filename
    contains ``_`` before the extension) so it stays fast on large
    libraries.  The parent lookup uses the filepath index.

    Phash confirmation (``require_phash_match=True``):
        The filename pattern alone is a heuristic — a genuinely distinct
        photo named ``beach_2.jpg`` next to ``beach.jpg`` would be wrongly
        hidden.  When ``require_phash_match`` is set, a candidate is only
        tagged when BOTH it and its parent have perceptual hashes AND those
        hashes are within ``max_hamming`` bits (i.e. the candidate really is
        the same still frame).  Candidates lacking a phash on either side, or
        whose phash diverges, are left visible.  Pass this on any path that
        runs AFTER hashes exist (startup backfill, post-analyze) so the
        filename heuristic can never silently drop a unique photo.  The
        import-folder path leaves it ``False`` because it filters by filename
        at scan time, before any hash is computed.
    """
    # Only names shaped like a sidecar (``…_N.ext``, one digit): every
    # iPhone photo has an underscore, so a broader pull would read the
    # whole library on each startup. The regex below is the precise check.
    candidates = conn.execute(
        """
        SELECT id, filepath, original_filename, phash, ahash
        FROM photos
        WHERE is_live_photo_sidecar = 0
          AND deleted_at IS NULL
          AND original_filename GLOB '*_[1-9].*'
        """
    ).fetchall()

    if not candidates:
        return 0

    # One batched query for the possible parents (matched on filename,
    # then narrowed to the candidate's own directory) instead of a LIKE
    # scan per folder. Both the exact parent name and its lower-cased
    # form are asked for: SQLite's lower() folds ASCII only, so an
    # accented or Cyrillic name is matched exactly and a plain-ASCII
    # case difference through lower(); the map is keyed by Python's
    # lower(), which handles both.
    parent_names: set[str] = set()
    for row in candidates:
        result = is_live_photo_sidecar_filename(row[2])
        if result is not None:
            parent_names.update((result[0], result[0].lower()))
    dir_map: dict[str, dict[str, tuple[int, int | None, int | None]]] = {}
    for ph, batch in in_batches(sorted(parent_names)):
        rows = conn.execute(
            "SELECT id, filepath, original_filename, phash, ahash FROM photos "
            f"WHERE (original_filename IN ({ph}) OR lower(original_filename) IN ({ph})) "
            "AND deleted_at IS NULL AND is_live_photo_sidecar = 0",
            (*batch, *batch),
        ).fetchall()
        for r in rows:
            dir_map.setdefault(os.path.dirname(r[1]), {})[r[2].lower()] = (r[0], r[3], r[4])

    updates: list[tuple[int, int]] = []
    skipped_phash = 0
    for photo_id, filepath, filename, cand_phash, cand_ahash in candidates:
        result = is_live_photo_sidecar_filename(filename)
        if not result:
            continue
        parent_filename, _ = result
        directory = os.path.dirname(filepath)
        parent = dir_map.get(directory, {}).get(parent_filename.lower())
        if parent is None:
            continue
        parent_id, parent_phash, parent_ahash = parent
        if parent_id == photo_id:
            continue
        # When phash confirmation is requested, only hide a candidate we
        # can prove is the same frame: both sides must have hashes and be
        # within the budget. Otherwise (missing hash, or divergent) leave it
        # visible — never drop a unique photo on filename alone.
        if require_phash_match and not _phash_confirms_sidecar(
            cand_phash, cand_ahash, parent_phash, parent_ahash, max_hamming
        ):
            skipped_phash += 1
            continue
        updates.append((parent_id, photo_id))

    if skipped_phash:
        log.info(
            "Live Photo detection: kept %d '_N' candidate(s) visible — "
            "phash did not confirm them as duplicates of their parent",
            skipped_phash,
        )

    if not updates:
        return 0

    conn.executemany(
        "UPDATE photos SET is_live_photo_sidecar = 1, live_photo_parent_id = ? WHERE id = ?",
        updates,
    )
    log.info(
        "Live Photo detection: linked %d sidecar(s) to their parent still frames",
        len(updates),
    )
    return len(updates)


def __getattr__(name: str):
    # Scan-time helpers live in live_photo_scan; keep the historical
    # import path working.
    if name in (
        "split_scan_for_confirmed_sidecars",
        "filter_sidecar_paths",
        "_sidecar_candidate_pairs",
        "_confirm_sidecars_by_phash",
    ):
        from bpp.db import live_photo_scan

        return getattr(live_photo_scan, name)
    raise AttributeError(name)

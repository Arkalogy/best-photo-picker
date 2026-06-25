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
    # Broad pull: any file with '_' in the name.  Fast enough at startup
    # (libraries are <=100k photos); the Python regex below filters precisely.
    candidates = conn.execute(
        """
        SELECT id, filepath, original_filename, phash, ahash
        FROM photos
        WHERE is_live_photo_sidecar = 0
          AND deleted_at IS NULL
          AND instr(original_filename, '_') > 0
        """
    ).fetchall()

    if not candidates:
        return 0

    # Build a directory→{filename: (id, phash, ahash)} map for fast parent
    # lookup. We only need entries in directories with at least one candidate.
    candidate_dirs: set[str] = {os.path.dirname(row[1]) for row in candidates}

    dir_map: dict[str, dict[str, tuple[int, int | None, int | None]]] = {}
    for directory in candidate_dirs:
        rows = conn.execute(
            "SELECT id, original_filename, phash, ahash FROM photos "
            "WHERE filepath LIKE ? AND deleted_at IS NULL AND is_live_photo_sidecar = 0",
            (directory + "/%",),
        ).fetchall()
        dir_map[directory] = {r[1].lower(): (r[0], r[2], r[3]) for r in rows}

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


def split_scan_for_confirmed_sidecars(
    image_paths: list[str],
    *,
    max_hamming: int = _SIDECAR_PHASH_MAX_HAMMING,
    compute_hashes=None,
    on_progress=None,
    parent_alive=None,
) -> tuple[list[str], list[dict]]:
    """Pre-scan split for the analyze pipeline: pull phash-confirmed Live
    Photo sidecars out of the to-score list so the expensive scoring / face /
    CLIP passes never run on them.

    This is the performance complement to
    :func:`detect_and_link_live_photo_sidecars`. Scoring a sidecar (faces +
    CLIP + aesthetics, many model inferences) is wasted compute — it's a
    near-identical motion frame nobody composed. But we must NEVER drop a
    photo on its filename alone, so confirmation is by perceptual hash, not
    by name: one cheap decode of the candidate AND its parent, compared.

    A path is moved to the sidecar list only when ALL hold:
      * its filename matches ``<base>_<N>.<ext>`` (single-digit N),
      * its parent ``<base>.<ext>`` is in the SAME directory in this batch,
      * the parent is alive per ``parent_alive`` (when provided),
      * both decode to a perceptual hash, and
      * those hashes are within ``max_hamming`` bits.

    ``parent_alive`` mirrors :func:`detect_and_link_live_photo_sidecars`'s
    DB-side criteria: a parent that is soft-deleted, hidden, or itself a
    sidecar can never anchor a hidden child (the tagger skips it), so a
    candidate confirmed against it here would linger as a visible,
    score-less ghost. Real case: the user trashes the parent in a
    duplicate review and keeps the ``_1`` copy — that copy is now their
    photo and must be scored, not skipped.

    Everything else stays in the to-score list — a ``_N`` file whose hash
    diverges from its parent (a genuinely distinct photo that merely shares
    the naming convention), one we can't hash, or one whose parent isn't in
    the batch. The filename pattern alone never drops anything.

    The returned sidecar records carry exactly the columns
    ``bulk_upsert_photos`` needs to store the sidecar WITHOUT scoring it —
    including the ``phash`` / ``ahash`` we just computed. Storing them with
    hashes lets the phash backfill thread's
    ``detect_and_link_live_photo_sidecars(require_phash_match=True)`` tag +
    link them to their parents (whose hashes land in the same pass) without a
    second decode. Tagging itself is deliberately left to that one path so
    the linking logic lives in exactly one place.

    Args:
        image_paths: the scan's full file list.
        max_hamming: phash-confirmation budget (default 2 bits).
        compute_hashes: ``(filepath) -> (dhash, ahash)``; defaults to
            :func:`bpp.dedupe.phash.compute_hashes_from_file`. Injectable for
            tests so they need no real image decode.
        on_progress: optional ``(done, total)`` callback ticked per parent
            decoded, so a large pre-pass isn't a silent stall.

    Returns:
        ``(paths_to_score, sidecar_records)``.
    """
    if compute_hashes is None:
        from bpp.dedupe.phash import compute_hashes_from_file as compute_hashes

    # Map (dir, lower_filename) -> original path, so a candidate can find its
    # parent's real path within this batch (same directory only).
    by_dir: dict[tuple[str, str], str] = {}
    for p in image_paths:
        by_dir[(os.path.dirname(p), os.path.basename(p).lower())] = p

    # Collect candidate→parent pairs first (filename-only), so we can report a
    # decode total up front and decode each parent at most once.
    pairs: list[tuple[str, str]] = []  # (candidate_path, parent_path)
    for p in image_paths:
        result = is_live_photo_sidecar_filename(os.path.basename(p))
        if result is None:
            continue
        parent_filename, _ = result
        parent_path = by_dir.get((os.path.dirname(p), parent_filename.lower()))
        if parent_path is None or parent_path == p:
            continue
        if parent_alive is not None and not parent_alive(parent_path):
            continue  # dead parent can never anchor a hidden child — score it
        pairs.append((p, parent_path))

    if not pairs:
        return list(image_paths), []

    # Decode each distinct parent once; cache candidate hashes so we reuse
    # them when building the sidecar records below (no second decode).
    parent_hashes: dict[str, tuple[int | None, int | None]] = {}
    cand_hashes: dict[str, tuple[int | None, int | None]] = {}
    confirmed: set[str] = set()
    total = len({pp for _, pp in pairs})
    done = 0
    # Only arm the early-abort probe when there's meaningfully more than one
    # probe slice to save — a library at or below the probe size is decoded in
    # full regardless (aborting would save nothing).
    probe_active = len(pairs) > _SIDECAR_PROBE_SIZE
    for i, (cand_path, parent_path) in enumerate(pairs):
        # After the probe slice, bail if the confirm rate is too low to be a
        # Live-Photo library — the rest flow to scoring undecoded.
        if probe_active and i == _SIDECAR_PROBE_SIZE:
            rate = len(confirmed) / _SIDECAR_PROBE_SIZE
            if rate < _SIDECAR_MIN_CONFIRM_RATE:
                log.info(
                    "Live Photo pre-scan: only %.0f%% of the first %d '_N' "
                    "candidates confirmed (< %.0f%% threshold) — not a "
                    "Live-Photo library; aborting pre-scan and scoring the "
                    "remaining %d candidate(s) normally",
                    rate * 100,
                    _SIDECAR_PROBE_SIZE,
                    _SIDECAR_MIN_CONFIRM_RATE * 100,
                    len(pairs) - _SIDECAR_PROBE_SIZE,
                )
                break
        if parent_path not in parent_hashes:
            parent_hashes[parent_path] = compute_hashes(parent_path)
            done += 1
            if on_progress is not None:
                on_progress(done, total)
        p_dhash, p_ahash = parent_hashes[parent_path]
        # If the parent couldn't be hashed, the candidate can never be
        # confirmed (both sides must match) — skip the wasted decode and leave
        # it in the to-score list.
        if p_dhash is None or p_ahash is None:
            continue
        c_dhash, c_ahash = compute_hashes(cand_path)
        cand_hashes[cand_path] = (c_dhash, c_ahash)
        if _phash_confirms_sidecar(c_dhash, c_ahash, p_dhash, p_ahash, max_hamming):
            confirmed.add(cand_path)

    if not confirmed:
        return list(image_paths), []

    to_score = [p for p in image_paths if p not in confirmed]
    records: list[dict] = []
    for cand_path in confirmed:
        try:
            stat = os.stat(cand_path)
        except OSError:
            # Can't stat → leave it in the scoring list rather than write a
            # half-row. Better to score a sidecar than to drop a file we
            # can't even read the size of.
            to_score.append(cand_path)
            continue
        c_dhash, c_ahash = cand_hashes[cand_path]
        records.append(
            {
                "filepath": cand_path,
                "original_filename": os.path.basename(cand_path),
                "file_size": stat.st_size,
                "file_mtime": stat.st_mtime,
                "phash": c_dhash,
                "ahash": c_ahash,
            }
        )

    log.info(
        "Live Photo pre-scan: %d phash-confirmed sidecar(s) skipped from scoring "
        "(of %d '_N' candidate pairs)",
        len(records),
        len(pairs),
    )
    return to_score, records


def filter_sidecar_paths(paths: list[str]) -> tuple[list[str], list[str]]:
    """Split a list of file paths into (non_sidecars, sidecars).

    Used at import scan time when ``import_live_photo_sidecars=False``
    to drop sidecar files before they are ever copied into the library.

    Operates purely on filenames — no filesystem or DB access required.
    A path is a sidecar candidate if its filename matches the ``_N.ext``
    pattern.  Since we are filtering BEFORE import, we cannot verify
    the parent exists in the library, so we use a conservative heuristic:
    only filter if a corresponding parent path (same directory, parent
    filename) is ALSO in the input list.  This avoids silently dropping
    files when the user imports only the sidecar and not the parent.

    Returns:
        non_sidecars: paths to import normally
        sidecars: paths that were identified as sidecars and skipped
    """
    # Build a set of (dir, lower_filename) for fast parent lookup
    all_names: set[tuple[str, str]] = set()
    for p in paths:
        d = os.path.dirname(p)
        f = os.path.basename(p).lower()
        all_names.add((d, f))

    non_sidecars: list[str] = []
    sidecars: list[str] = []

    for p in paths:
        filename = os.path.basename(p)
        result = is_live_photo_sidecar_filename(filename)
        if result is None:
            non_sidecars.append(p)
            continue
        parent_filename, _ = result
        directory = os.path.dirname(p)
        # Only skip if the parent is in the same import batch
        if (directory, parent_filename.lower()) in all_names:
            sidecars.append(p)
        else:
            # Parent not present — import the sidecar as a regular photo
            # so the user doesn't lose it silently.
            non_sidecars.append(p)

    return non_sidecars, sidecars

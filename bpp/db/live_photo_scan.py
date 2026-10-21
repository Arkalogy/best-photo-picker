"""Live Photo sidecar handling at scan time (before any row exists).

Companion of :mod:`bpp.db.live_photo`, which owns the filename rule and
the DB-side tagging. These helpers decide, for a list of files found on
disk, which ones are sidecars of a parent in the same scan, confirming
the filename heuristic by perceptual hash on a probe sample.
"""

from __future__ import annotations

import os

from bpp.db.live_photo import (
    _SIDECAR_MIN_CONFIRM_RATE,
    _SIDECAR_PHASH_MAX_HAMMING,
    _SIDECAR_PROBE_SIZE,
    _phash_confirms_sidecar,
    is_live_photo_sidecar_filename,
)
from bpp.utils.logging import get_logger

log = get_logger(__name__)


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

    pairs = _sidecar_candidate_pairs(image_paths, parent_alive)
    if not pairs:
        return list(image_paths), []

    confirmed, cand_hashes = _confirm_sidecars_by_phash(
        pairs, compute_hashes, max_hamming, on_progress
    )
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


def _sidecar_candidate_pairs(image_paths: list[str], parent_alive) -> list[tuple[str, str]]:
    """``(candidate_path, parent_path)`` for every ``_N`` file whose parent
    is in the same directory of this batch (filename-only; nothing is
    decoded yet). Collected up front so the decode total can be reported
    and each parent decoded at most once."""
    # Map (dir, lower_filename) -> original path, so a candidate can find its
    # parent's real path within this batch (same directory only).
    by_dir: dict[tuple[str, str], str] = {}
    for p in image_paths:
        by_dir[(os.path.dirname(p), os.path.basename(p).lower())] = p

    pairs: list[tuple[str, str]] = []
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
    return pairs


def _confirm_sidecars_by_phash(
    pairs: list[tuple[str, str]], compute_hashes, max_hamming: int, on_progress
) -> tuple[set[str], dict[str, tuple[int | None, int | None]]]:
    """Decode each distinct parent once and every candidate once; returns
    the confirmed candidate paths and the candidates' hashes (reused for
    the sidecar records, so there is no second decode)."""
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
    return confirmed, cand_hashes


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

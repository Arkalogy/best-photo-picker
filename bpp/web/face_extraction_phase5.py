"""Phase 5 of the face-extraction pipeline — the heavy ML extractor.

Extracted from :mod:`bpp.web.face_extraction_phases` as part of the
500-LOC cap enforcement. Phase 5 is the only phase that drives a
worker pool (process or thread) and does per-photo ``INSERT ... ON
CONFLICT`` writes; it owns its own ``ExtractionOutput`` dataclass and
the ``_restore_dismissed_and_filter`` helper that closes the dismissed-
slot loop after the INSERT-OR-REPLACE.

The face_extraction_phases module re-exports
:func:`extract_new_embeddings`, :class:`ExtractionOutput`, and
``_restore_dismissed_and_filter`` so existing callers (the orchestrator
and the dozen tests that touch them) continue to work via the original
module path.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from concurrent.futures import (
    FIRST_COMPLETED,
    ProcessPoolExecutor,
    ThreadPoolExecutor,
    wait,
)
from dataclasses import dataclass
from typing import Any

import numpy as np

from bpp.constants import CLUSTER_DISMISSED
from bpp.scoring.face_embed import producing_model_id_for
from bpp.utils.logging import get_logger
from bpp.web.face_extraction_phases import ExtractionPartition, PreExtractSnapshot

log = get_logger(__name__)

# Protection E: how often to checkpoint the WAL during a long
# extraction run. Every N completed photos. Smaller values bound the
# SIGKILL-corruption window more tightly but cost more wall clock;
# 100 balances both — at ~250 photos / 20s observed during the Jun-2
# extraction, that's roughly one checkpoint per 8 seconds.
_WAL_CHECKPOINT_EVERY = 100

# Commit the face-embedding INSERTs in batches of this many photos rather
# than once per photo. At 150-200K-photo libraries a per-photo commit means
# 150-200K WAL fsyncs — minutes of pure commit overhead during extraction.
# Batching cuts the fsync count ~100x. The crash exposure stays bounded: a
# SIGKILL mid-run loses at most the current batch (<= this many photos),
# which the idempotent re-run re-extracts anyway (ON CONFLICT upsert). Kept
# equal to the WAL-checkpoint cadence so each batch commits, then truncates
# the WAL in one aligned step. Configurable: lower it to tighten crash loss
# at some throughput cost.
_FACE_COMMIT_EVERY = 100


@dataclass
class ExtractionOutput:
    """Result of :func:`extract_new_embeddings`.

    ``all_records`` is the full set (cached + newly extracted, minus
    dismissed slots that the restore re-orphaned). ``cancelled_early``
    is set when the per-photo loop saw the cancel signal — the
    orchestrator uses it to short-circuit clustering.
    """

    all_records: list[tuple[str, int, int, np.ndarray]]
    cancelled_early: bool
    extracted_count: int


def extract_new_embeddings(
    conn: sqlite3.Connection,
    partition: ExtractionPartition,
    snapshot: PreExtractSnapshot,
    *,
    max_long_side: int,
    face_confidence: float,
    config: dict[str, Any],
    extract_one_fn: Callable[..., list[dict] | None],
    validate_bbox_fn: Callable[..., tuple[int, int, int, int] | None],
    validate_embedding_fn: Callable[[np.ndarray], bool],
    progress_callback: Callable[[dict[str, Any]], None] | None,
    cancellation_check: Callable[[], bool] | None,
    progress_total: int,
    method: str | None = None,
) -> ExtractionOutput:
    """Phase 5: drive the per-photo extractor pool + INSERT/UPSERT.

    Heavy. Same pool-selection logic and per-photo committing as the
    pre-P3 monolith — the win here is the cleanly-defined boundary
    (everything before is read-only / pure; everything after sees a
    consistent post-extract DB state).

    The callbacks (``extract_one_fn`` etc.) are injected so the phase
    file doesn't import the C-extension stack at module-load time —
    keeps phase-level unit tests fast.
    """
    all_records: list[tuple[str, int, int, np.ndarray]] = list(partition.cached_records)
    cancelled_early = False
    extracted_count = 0

    need_extract = partition.need_extract
    total = progress_total
    done_count = total - len(need_extract)

    if not need_extract:
        # Still need to honour the dismissed-slot restoration even when
        # we extract nothing — the INSERTs from a prior partial run
        # could have left orphan rows otherwise.
        _restore_dismissed_and_filter(conn, all_records, snapshot.dismissed_slots)
        return ExtractionOutput(
            all_records=all_records,
            cancelled_early=False,
            extracted_count=0,
        )

    try:
        n_workers = int(config.get("_face_extract_workers") or 1)
    except (TypeError, ValueError):
        n_workers = 1
    pool_kind = (
        config.get("_face_extract_pool") or ("process" if n_workers > 1 else "thread")
    ).lower()
    embed_conf = float(config.get("face_embedding_confidence", 0.65))
    min_eq = float(config.get("min_embedding_quality", 0.25))

    Pool = ProcessPoolExecutor if pool_kind == "process" and n_workers > 1 else ThreadPoolExecutor
    log.info(
        "Face extract: %s pool, workers=%d, %d photos to extract",
        Pool.__name__,
        n_workers,
        len(need_extract),
    )
    # Bounded in-flight submission. Pre-T4 the loop submitted every
    # need_extract item up front, building a dict of N pending futures
    # and queuing every input in the pool's _work_queue. At the
    # documented 50K-photo import scale that retains 50K (idx, fp, pid)
    # tuples + their pickled extractor args in memory until
    # as_completed drains them. ``MAX_INFLIGHT = n_workers * 4`` keeps
    # the workers saturated without holding the whole queue resident.
    needs_iter = iter(need_extract)
    inflight: dict[Any, tuple[int, str, int]] = {}
    max_inflight_cap = max(1, n_workers * 4)

    with Pool(max_workers=n_workers) as pool:

        def _submit_next() -> bool:
            """Submit one more item from ``needs_iter``. Returns False
            when the iterator is exhausted (caller stops priming)."""
            try:
                idx, fp_, pid = next(needs_iter)
            except StopIteration:
                return False
            fut = pool.submit(
                extract_one_fn,
                fp_,
                max_long_side,
                face_confidence,
                embed_conf,
                min_eq,
                method,
            )
            inflight[fut] = (idx, fp_, pid)
            return True

        # Prime the in-flight set up to the cap.
        for _ in range(max_inflight_cap):
            if not _submit_next():
                break

        # Drain + refill loop. Each completion frees one slot which we
        # immediately top up so the workers stay busy.
        while inflight:
            if cancellation_check and cancellation_check():
                for f in inflight:
                    f.cancel()
                log.info("Face extraction cancelled")
                cancelled_early = True
                break

            done, _pending = wait(inflight, return_when=FIRST_COMPLETED)
            for future in done:
                _idx, fp, photo_id = inflight.pop(future)
                try:
                    faces = future.result()
                except Exception:
                    log.warning(
                        "Face extraction crashed for photo_id=%s (%s), skipping",
                        photo_id,
                        fp,
                    )
                    faces = None
                if faces:
                    for fi, face in enumerate(faces):
                        if "bbox" not in face or "embedding" not in face:
                            log.warning(
                                "Malformed face dict at face_index=%d "
                                "for photo_id=%s (%s), skipping",
                                fi,
                                photo_id,
                                fp,
                            )
                            continue
                        try:
                            bx, by, bw, bh = face["bbox"]
                        except (ValueError, TypeError):
                            log.warning(
                                "Face bbox unpack failed at face_index=%d "
                                "for photo_id=%s (%s), skipping",
                                fi,
                                photo_id,
                                fp,
                            )
                            continue
                        emb = face["embedding"]
                        bbox = validate_bbox_fn(bx, by, bw, bh)
                        if bbox is None:
                            continue
                        if not validate_embedding_fn(emb):
                            continue
                        bx, by, bw, bh = bbox
                        quality = face.get("quality")
                        # v40 (Bug #9 hardening): record the detector
                        # input size so the read path can reconstruct
                        # the exact dimensions regardless of current
                        # settings. Without this, a config change to
                        # max_long_side silently invalidates every
                        # stored bbox.
                        # Batch 7 / item 21: tag every row with the
                        # registry id of the model that produced it
                        # so the derived-data-purge flow at model-
                        # removal time can find them. Map the short
                        # ``method`` string (sface / dlib) to the
                        # canonical registry entry id; BYOM
                        # extractions can pass their byom_<hash> id
                        # through ``method`` directly.
                        producing_model_id = producing_model_id_for(method)
                        conn.execute(
                            "INSERT INTO face_embeddings "
                            "(photo_id, face_index, "
                            " bbox_x, bbox_y, bbox_w, bbox_h, embedding, quality,"
                            " extraction_max_long_side, producing_model_id) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?) "
                            "ON CONFLICT(photo_id, face_index) DO UPDATE SET "
                            " bbox_x=excluded.bbox_x, bbox_y=excluded.bbox_y,"
                            " bbox_w=excluded.bbox_w, bbox_h=excluded.bbox_h,"
                            " embedding=excluded.embedding, quality=excluded.quality,"
                            " extraction_max_long_side=excluded.extraction_max_long_side,"
                            " producing_model_id=excluded.producing_model_id",
                            (
                                photo_id,
                                fi,
                                bx,
                                by,
                                bw,
                                bh,
                                emb.tobytes(),
                                quality,
                                max_long_side,
                                producing_model_id,
                            ),
                        )
                        all_records.append((fp, photo_id, fi, emb))
                        extracted_count += 1
                done_count += 1
                # Commit in bounded batches (see _FACE_COMMIT_EVERY) instead
                # of once per photo — the WAL checkpoint just below runs on
                # the same cadence, so each batch commits then truncates the
                # WAL in one aligned step. A final commit after the loop
                # flushes the tail batch (see below).
                if done_count % _FACE_COMMIT_EVERY == 0:
                    conn.commit()
                # Protection E: periodic WAL checkpoint during long
                # extraction runs. Without this, the WAL grows for the
                # whole multi-minute run; a SIGKILL anywhere in that
                # window can leave a corrupt WAL that's hard to
                # recover (Jun-1 demo lib incident). Checkpointing
                # every 100 photos caps the worst-case loss to ~one
                # chunk and keeps the WAL file small. TRUNCATE mode
                # so the WAL doesn't bloat through the run.
                if done_count % _WAL_CHECKPOINT_EVERY == 0:
                    try:
                        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    except sqlite3.OperationalError:
                        # WAL not enabled (dialect without WAL, or
                        # checkpoint blocked by a concurrent reader)
                        # — neither case warrants aborting extraction.
                        log.debug(
                            "Periodic WAL checkpoint at %d photos failed; continuing extraction",
                            done_count,
                            exc_info=True,
                        )
                if progress_callback:
                    progress_callback(
                        {
                            "type": "face_progress",
                            "current": done_count,
                            "total": total,
                            "filepath": os.path.basename(fp),
                        }
                    )
                # Free slot → top up one more submission.
                _submit_next()

    # Flush the final partial batch — _FACE_COMMIT_EVERY only commits on the
    # boundary, so the last < _FACE_COMMIT_EVERY photos are still uncommitted
    # here. _restore_dismissed_and_filter commits only when there ARE dismissed
    # slots, so without this an entire tail batch could be lost on a clean run.
    conn.commit()

    _restore_dismissed_and_filter(conn, all_records, snapshot.dismissed_slots)
    return ExtractionOutput(
        all_records=all_records,
        cancelled_early=cancelled_early,
        extracted_count=extracted_count,
    )


def _restore_dismissed_and_filter(
    conn: sqlite3.Connection,
    all_records: list[tuple[str, int, int, np.ndarray]],
    dismissed_slots: frozenset[tuple[int, int]],
) -> None:
    """Re-stamp ``cluster_id = CLUSTER_DISMISSED`` on any slot the
    extractor's INSERT OR REPLACE just overwrote, and filter the
    restored slots out of the in-memory record list so they don't enter
    clustering. Mutates ``all_records`` in place by replacement
    assignment via slice — keeping the same list object so the caller's
    reference stays valid.
    """
    if not dismissed_slots:
        return
    cur = conn.executemany(
        "UPDATE face_embeddings SET cluster_id = ? WHERE photo_id = ? AND face_index = ?",
        [(CLUSTER_DISMISSED, pid, fi) for pid, fi in dismissed_slots],
    )
    restored = cur.rowcount
    if restored:
        conn.commit()
        log.info("Restored %d dismissed face slots after re-extraction", restored)
    # In-place filter — keep same list object.
    filtered = [r for r in all_records if (r[1], r[2]) not in dismissed_slots]
    all_records[:] = filtered

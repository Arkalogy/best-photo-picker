"""Types shared by :mod:`face_phase_pipeline` and :mod:`face_phase_classes`.

Extracted from face_phase_pipeline.py so the concrete phase classes
(which live in face_phase_classes) can import the protocol + context +
sentinel without circularly depending on the runner module that
imports the classes back to assemble :data:`FACE_PIPELINE`.

This module has no behaviour — only the type / protocol surface plus
a single constant.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from bpp.web import face_extraction_journal as journal
from bpp.web import face_extraction_phases as phases

# Sentinel for phases that always run (Phase 4.5).
_NO_JOURNAL = -1


# ──────────────────────────────────────────────────────────────────
# Context
# ──────────────────────────────────────────────────────────────────


@dataclass
class FacePhaseContext:
    """Mutable accumulator threaded through every face phase.

    Built once by :func:`bpp.web.face_phase_pipeline.run_face_pipeline`
    from the orchestrator's arguments. Each phase reads what it needs
    and writes its outputs into the dedicated fields (``snapshot``,
    ``partition``, ``extraction``, etc.). The orchestrator never
    inspects these directly — the final ``faces_found`` / ``n_clusters``
    are read off the context at the end.
    """

    # ── inputs (immutable per run) ─────────────────────────────────
    conn: sqlite3.Connection
    with_faces: list[dict[str, Any]]
    photo_map: dict[str, int]
    max_long_side: int
    face_confidence: float
    config: dict[str, Any]

    # ── journal state ──────────────────────────────────────────────
    run_id: str
    phases_done: int  # bitmask from get_phases_complete()

    # ── late-bound function refs (test monkey-patch surface) ───────
    # The test suite monkey-patches names on bpp.web.face_worker;
    # the orchestrator reads them fresh at run-start so patches take
    # effect even though the implementation lives elsewhere.
    extract_one_fn: Callable[..., Any]
    validate_bbox_fn: Callable[..., Any]
    validate_embedding_fn: Callable[..., Any]
    assign_new_faces_fn: Callable[..., Any]
    reconstruct_identities_fn: Callable[..., Any]
    remap_names_and_tags_fn: Callable[..., Any]

    # ── runtime plumbing ───────────────────────────────────────────
    progress_callback: Callable[[dict[str, Any]], None] | None = None
    cancellation_check: Callable[[], bool] | None = None
    post_cluster_dedup: bool = False

    # ── accumulating phase outputs ─────────────────────────────────
    # Resolved embedder choice for this run (sface | dlib). Phase 1
    # writes it via embedding_method(conn) honoring the user setting;
    # Phase 5 reads it to drive per-photo extraction.
    current_method: str | None = None
    stored_method: str | None = None
    method_changed: bool = False
    old_cluster_photos: dict[int, set[int]] | None = None
    cached_by_pid: dict[int, list[tuple[int, Any]]] = field(default_factory=dict)
    stale_photo_ids: frozenset[int] = field(default_factory=frozenset)
    partition: phases.ExtractionPartition | None = None
    dismissed_slots: frozenset[tuple[int, int]] = field(default_factory=frozenset)
    snapshot: phases.PreExtractSnapshot | None = None
    extraction: Any = None  # ExtractionOutput from phase5
    clustering_journal_id: int | None = None

    # ── final results read by orchestrator ─────────────────────────
    faces_found: int = 0
    n_clusters: int = 0
    cancelled_early: bool = False

    # ── helpers ────────────────────────────────────────────────────
    def is_cancelled(self) -> bool:
        return bool(self.cancellation_check and self.cancellation_check())

    def journal_complete(self, phase_bit: int) -> bool:
        return journal.is_phase_complete(self.phases_done, phase_bit)


# ──────────────────────────────────────────────────────────────────
# Protocol
# ──────────────────────────────────────────────────────────────────


class FacePhase(Protocol):
    """In-process face-pipeline phase.

    The runner asks each phase the three questions:

    1. ``should_skip(ctx)`` — already journal-complete? If so, the
       runner will call :meth:`rehydrate` instead of :meth:`run`.
    2. ``rehydrate(ctx)`` — repopulate ctx's fields from journal
       storage so downstream phases see the same state they would
       have if ``run`` had executed.
    3. ``run(ctx)`` — do the work, mutate ctx. If ``journal_bit`` is
       non-negative, the runner stamps the bit after a successful
       return.

    A phase with ``journal_bit < 0`` is intentionally never skipped
    on resume — e.g. Phase 4.5 captures the *current* dismissed-slot
    set, which must reflect the user's between-crash-and-resume
    dismissals (T1.1).
    """

    name: str
    journal_bit: int  # -1 means "always run"

    def should_skip(self, ctx: FacePhaseContext) -> bool: ...
    def rehydrate(self, ctx: FacePhaseContext) -> None: ...
    def run(self, ctx: FacePhaseContext) -> None: ...

"""Phase-protocol decomposition of the face-extraction pipeline.

Public entry point for the face-extraction pipeline. The protocol +
context type live in :mod:`face_phase_types`; the eight concrete
phase classes live in :mod:`face_phase_classes`. This module owns the
runner, the plugin extension surface (:func:`register_face_phase`),
and the canonical built-in ordering.

* :class:`FacePhase` and :class:`FacePhaseContext` — re-exported below
  for back-compat with existing callers.
* :data:`FACE_PIPELINE` — the canonical ordered tuple of built-in
  phase instances. ``run_face_pipeline(ctx)`` walks the live pipeline
  via :func:`build_pipeline`; FACE_PIPELINE remains as the built-in-
  only snapshot for callers that read it directly.
* :func:`register_face_phase` — plugin hook for splicing custom
  phases between the built-ins by priority.
* :func:`run_face_pipeline` — the runner. Each phase decides itself
  whether to run, rehydrate, or skip; the runner stamps the journal
  bit on success and checks cancellation between phases.

Cancellation: checked between phases at the safe boundary. The
extract phase can also signal mid-phase cancellation via
``ExtractionOutput.cancelled_early``, in which case the runner
returns early with ``faces_found`` set but ``n_clusters=0`` and the
journal row left pending for resume.
"""

from __future__ import annotations

import threading

from bpp.utils.logging import get_logger
from bpp.web import face_extraction_journal as journal
from bpp.web.face_phase_classes import (
    ClusterPhase,
    DismissedSlotsPhase,
    ExtractEmbeddingsPhase,
    IdentityReconstructPhase,
    MethodReconcilerPhase,
    PartitionPhase,
    PreloadCachedEmbeddingsPhase,
    StaleDeletePhase,
)
from bpp.web.face_phase_types import _NO_JOURNAL, FacePhase, FacePhaseContext

# Re-export so existing imports of FacePhase / FacePhaseContext /
# _NO_JOURNAL from this module keep working.
__all__ = [
    "FACE_PIPELINE",
    "_NO_JOURNAL",
    "FacePhase",
    "FacePhaseContext",
    "build_pipeline",
    "register_face_phase",
    "run_face_pipeline",
    "unregister_face_phase",
]

log = get_logger(__name__)


# ──────────────────────────────────────────────────────────────────
# Canonical pipeline ordering + plugin extension surface
# ──────────────────────────────────────────────────────────────────

# Built-in phase priorities. Plugins place themselves between the
# built-ins by choosing a priority — by convention "before X" uses
# `<priority of X>` and "after X" uses `<priority of X> + 1`. Wide
# integer gaps leave room for plugins to insert at the exact slot
# without reshuffling the built-ins.
_PRIORITY_METHOD_RECONCILE = 100
_PRIORITY_PRELOAD = 200
_PRIORITY_PARTITION = 300
_PRIORITY_STALE_DELETE = 400
_PRIORITY_DISMISSED_SLOTS = 450
_PRIORITY_EXTRACT = 500
_PRIORITY_CLUSTER = 600
_PRIORITY_IDENTITY = 700

#: Plugin-registered phases keyed by name → (priority, phase). Kept
#: as a dict (not a list) so re-registering the same name with
#: ``replace=True`` updates in place instead of creating a duplicate.
_extra_phases: dict[str, tuple[int, FacePhase]] = {}
#: Guards ``_extra_phases`` against a worker reading mid-registration.
#: Plugins typically register at startup (single-threaded), but a
#: late-binding plugin that registers inside ``on_library_open`` runs
#: while the previous library's workers may still be draining — the
#: lock keeps :func:`build_pipeline` from seeing a torn dict.
_extra_phases_lock = threading.Lock()


def register_face_phase(
    phase: FacePhase,
    *,
    priority: int,
    replace: bool = False,
) -> None:
    """Insert a custom :class:`FacePhase` into the pipeline.

    Plugins use this to add phases that run between the built-ins.
    Pick a priority that places the phase where it belongs:

    * Between method reconciler (100) and preload (200) — pick 150.
    * After clustering (600) but before identity reconstruct (700) —
      pick 650. A "validate the clusters before persisting album
      changes" plugin lives here.
    * Last (after identity reconstruct) — pick > 700, e.g. 800.

    A phase registered twice under the same name without
    ``replace=True`` raises. Use :func:`unregister_face_phase` from
    test code to roll back.

    Built-in phases use priorities 100, 200, 300, 400, 450, 500, 600,
    700 — wide gaps left so plugins can slot in cleanly.

    Args:
        phase: An instance satisfying the :class:`FacePhase` Protocol.
            Implementations may use ``journal_bit = -1`` (the
            :data:`_NO_JOURNAL` sentinel) to opt out of the journal-
            skip / mark-complete mechanism.
        priority: Where the phase runs. Lower numbers run earlier.
        replace: When True, overwrite any existing phase with the same
            ``name``. Default False raises on collision.
    """
    with _extra_phases_lock:
        if phase.name in _extra_phases and not replace:
            existing_priority, _existing = _extra_phases[phase.name]
            raise ValueError(
                f"Face phase {phase.name!r} already registered at priority "
                f"{existing_priority}; pass replace=True if intentional."
            )
        _extra_phases[phase.name] = (priority, phase)


def unregister_face_phase(name: str) -> bool:
    """Remove a previously-registered face phase. Returns True if
    a phase by that name was registered."""
    with _extra_phases_lock:
        return _extra_phases.pop(name, None) is not None


def _reset_face_phases_for_tests() -> None:
    """Drop every plugin-registered phase. Test-only hook."""
    with _extra_phases_lock:
        _extra_phases.clear()


_BUILTIN_PIPELINE: tuple[tuple[int, FacePhase], ...] = (
    (_PRIORITY_METHOD_RECONCILE, MethodReconcilerPhase()),
    (_PRIORITY_PRELOAD, PreloadCachedEmbeddingsPhase()),
    (_PRIORITY_PARTITION, PartitionPhase()),
    (_PRIORITY_STALE_DELETE, StaleDeletePhase()),
    (_PRIORITY_DISMISSED_SLOTS, DismissedSlotsPhase()),
    (_PRIORITY_EXTRACT, ExtractEmbeddingsPhase()),
    (_PRIORITY_CLUSTER, ClusterPhase()),
    (_PRIORITY_IDENTITY, IdentityReconstructPhase()),
)


def build_pipeline() -> tuple[FacePhase, ...]:
    """Compose built-ins + plugin phases into the run-time pipeline.

    Called by :func:`run_face_pipeline` at the start of each
    orchestration — picks up plugin registrations made AFTER module
    load, so a plugin's :func:`setup` can register a face phase
    without import-order surgery.
    """
    with _extra_phases_lock:
        extras = list(_extra_phases.values())
    merged = list(_BUILTIN_PIPELINE) + extras
    merged.sort(key=lambda item: item[0])
    return tuple(phase for _priority, phase in merged)


# Kept for back-compat with callers / tests that read the canonical
# tuple directly. It reflects the built-ins only; plugin additions
# appear at :func:`build_pipeline` time. New code should call
# ``build_pipeline()`` for the live ordering.
FACE_PIPELINE: tuple[FacePhase, ...] = tuple(phase for _priority, phase in _BUILTIN_PIPELINE)


# ──────────────────────────────────────────────────────────────────
# Runner
# ──────────────────────────────────────────────────────────────────


def run_face_pipeline(ctx: FacePhaseContext) -> None:
    """Drive every phase in :data:`FACE_PIPELINE` against ``ctx``.

    Each phase decides itself whether to run, rehydrate, or skip via
    the protocol's ``should_skip`` hook. After a successful ``run``
    the runner stamps the journal bit (if any). Cancellation is
    checked between phases.

    Phase 5's ``ExtractionOutput.cancelled_early`` flag is honoured
    by short-circuiting before clustering — the journal row stays
    pending and the recovery handler resumes at phase 5 on next start.

    Phase 7 raises on failure; the journal row stays pending and the
    bounded recovery loop retries.
    """
    pipeline = build_pipeline()
    for phase in pipeline:
        # Pre-phase cancel check — between phases is the safe boundary.
        if ctx.is_cancelled():
            log.info(
                "Face pipeline cancelled before phase %s — leaving journal pending",
                phase.name,
            )
            ctx.cancelled_early = True
            return

        if phase.should_skip(ctx):
            # H4 / review 2026-05-31: log skipped phases too so server.log
            # explains what the orchestrator did across a resume without
            # forcing on-call to query the journal table.
            log.info("Face pipeline: skipping %s (journal-complete)", phase.name)
            phase.rehydrate(ctx)
            continue

        # M1 / review 2026-05-31: PreloadCachedEmbeddingsPhase has a
        # journal bit but intentionally re-runs on resume (the output
        # isn't snapshotted; it's recomputed from current DB state).
        # Surface that in the log so a reader can tell the journal bit
        # was set in a prior run but the body still executed.
        if phase.journal_bit != _NO_JOURNAL and ctx.journal_complete(phase.journal_bit):
            log.info(
                "Face pipeline: re-running %s despite journal-complete "
                "(phase output recomputed from current DB state)",
                phase.name,
            )
        else:
            log.info("Face pipeline: starting %s", phase.name)

        phase.run(ctx)

        # Honour mid-phase cancel from extract.
        if (
            phase.name == "extract_new_embeddings"
            and ctx.extraction is not None
            and getattr(ctx.extraction, "cancelled_early", False)
        ):
            ctx.cancelled_early = True
            ctx.faces_found = len(ctx.extraction.all_records)
            log.info(
                "Face pipeline: extract cancelled (%d faces); skipping cluster + identity",
                ctx.faces_found,
            )
            return

        log.info("Face pipeline: %s completed", phase.name)

        if phase.journal_bit != _NO_JOURNAL:
            journal.mark_phase_complete(ctx.conn, ctx.run_id, phase.journal_bit)

    # Pipeline ran to completion. Stamp the run done.
    journal.complete_run(ctx.conn, ctx.run_id)

    if ctx.clustering_journal_id is not None:
        from bpp.db.journal import journal_complete

        journal_complete(ctx.conn, ctx.clustering_journal_id)

    if ctx.extraction is not None:
        ctx.faces_found = len(ctx.extraction.all_records)

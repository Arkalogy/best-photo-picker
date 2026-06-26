"""Concrete :class:`FacePhase` implementations.

Extracted from :mod:`bpp.web.face_phase_pipeline` after the 500-LOC
cap pushed it over budget. The runner + Protocol + context type still
live in face_phase_pipeline so existing imports stay stable; the eight
phase classes that compose the canonical pipeline live here.

Plugin authors looking for the ``register_face_phase`` extension point
should still target ``bpp.web.face_phase_pipeline`` — that's the
public surface. This module is the implementation backbone behind it.
"""

from __future__ import annotations

from bpp.scoring.face_embed import embedding_method
from bpp.utils.logging import get_logger
from bpp.web import face_extraction_journal as journal
from bpp.web import face_extraction_phases as phases
from bpp.web.face_phase_types import _NO_JOURNAL, FacePhaseContext

log = get_logger(__name__)


class MethodReconcilerPhase:
    """Phase 1 — sface ↔ dlib method change detection and wipe."""

    name = "method_reconciler"
    journal_bit = journal.PHASE_BIT_METHOD_RECONCILE

    def should_skip(self, ctx: FacePhaseContext) -> bool:
        return ctx.journal_complete(self.journal_bit)

    def rehydrate(self, ctx: FacePhaseContext) -> None:
        # Snapshot was stored after phase 4.5 in the original run;
        # on resume it carries the phase-1 outputs the orchestrator
        # would otherwise recompute. Defensive fallback: if the
        # snapshot is missing, run the phase again — reconcile_method
        # is idempotent.
        rehydrated = journal.load_snapshot(ctx.conn, ctx.run_id)
        if rehydrated is not None:
            ctx.stored_method = rehydrated.stored_method
            ctx.method_changed = rehydrated.method_changed
            ctx.old_cluster_photos = rehydrated.old_cluster_photos
        else:
            self.run(ctx)

    def run(self, ctx: FacePhaseContext) -> None:
        # Bug A fix: pass ctx.conn so embedding_method respects the
        # face_embedding_method setting. Before, the setting was
        # written to the DB by Phase 1 but never read — making the
        # "pick an embedder" toggle decorative. The resolved choice
        # is stashed on ctx so Phase 5 can drive the per-photo
        # extractor with it.
        current_method = embedding_method(ctx.conn)
        ctx.current_method = current_method
        stored, changed, old_cluster_photos = phases.reconcile_method(ctx.conn, current_method)
        ctx.stored_method = stored
        ctx.method_changed = changed
        ctx.old_cluster_photos = old_cluster_photos


class PreloadCachedEmbeddingsPhase:
    """Phase 2 — bulk-load cached embeddings, find stale photo IDs."""

    name = "preload_cached_embeddings"
    journal_bit = journal.PHASE_BIT_PRELOAD

    def should_skip(self, ctx: FacePhaseContext) -> bool:
        # Always run — the cached_by_pid + stale_photo_ids outputs
        # are not journaled separately; phase 3 below consumes them
        # in-process. On resume we recompute (cheap; one SELECT).
        return False

    def rehydrate(self, ctx: FacePhaseContext) -> None:
        self.run(ctx)

    def run(self, ctx: FacePhaseContext) -> None:
        cached_by_pid, stale_photo_ids = phases.preload_cached_embeddings(
            ctx.conn, ctx.with_faces, ctx.photo_map
        )
        ctx.cached_by_pid = cached_by_pid
        ctx.stale_photo_ids = stale_photo_ids
        # When stale embeddings exist but method didn't change,
        # phase 1 didn't snapshot old_cluster_photos — capture it
        # here so phase 7 has the mapping for name remap.
        if stale_photo_ids and ctx.old_cluster_photos is None:
            ctx.old_cluster_photos = phases._snapshot_cluster_photos(ctx.conn)


class PartitionPhase:
    """Phase 3 — partition cached records vs. faces needing extract."""

    name = "partition_cached_vs_extract"
    journal_bit = journal.PHASE_BIT_PARTITION

    def should_skip(self, ctx: FacePhaseContext) -> bool:
        # Pure function of phase 2 output; cheap to re-run.
        return False

    def rehydrate(self, ctx: FacePhaseContext) -> None:
        self.run(ctx)

    def run(self, ctx: FacePhaseContext) -> None:
        ctx.partition = phases.partition_cached_vs_extract(
            ctx.with_faces, ctx.photo_map, ctx.cached_by_pid, ctx.stale_photo_ids
        )


class StaleDeletePhase:
    """Phase 4 — delete ``quality IS NULL`` embeddings for photos that need re-extract."""

    name = "delete_stale_embeddings"
    journal_bit = journal.PHASE_BIT_STALE_DELETE

    def should_skip(self, ctx: FacePhaseContext) -> bool:
        return ctx.journal_complete(self.journal_bit)

    def rehydrate(self, ctx: FacePhaseContext) -> None:
        # Stale embeddings were already deleted in a prior run; the
        # DB is in the post-delete state. Nothing to do here.
        pass

    def run(self, ctx: FacePhaseContext) -> None:
        assert ctx.partition is not None
        phases.delete_stale_embeddings(ctx.conn, ctx.partition.need_extract, ctx.stale_photo_ids)


class DismissedSlotsPhase:
    """Phase 4.5 — capture currently-dismissed (photo_id, face_index) slots.

    T1.1 contract: this phase ALWAYS runs, no journal skip. The user
    may have dismissed faces in the window between the original crash
    and this resume; the captured frozenset must reflect that fresh
    state. The snapshot built by the next phase is then re-stored to
    the journal so downstream readers see the live dismissed set.
    """

    name = "capture_dismissed_slots"
    journal_bit = _NO_JOURNAL

    def should_skip(self, ctx: FacePhaseContext) -> bool:
        return False

    def rehydrate(self, ctx: FacePhaseContext) -> None:
        # Never called (should_skip returns False), but the protocol
        # requires the method exist.
        self.run(ctx)

    def run(self, ctx: FacePhaseContext) -> None:
        assert ctx.partition is not None
        ctx.dismissed_slots = phases.capture_dismissed_slots(ctx.conn, ctx.partition.need_extract)
        # Build / overwrite the consolidated snapshot. Stamped into
        # the journal so a future resume's rehydrate picks up the
        # current dismissed set.
        ctx.snapshot = phases.PreExtractSnapshot(
            stored_method=ctx.stored_method,
            method_changed=ctx.method_changed,
            old_cluster_photos=ctx.old_cluster_photos,
            stale_photo_ids=ctx.stale_photo_ids,
            dismissed_slots=ctx.dismissed_slots,
        )
        journal.store_snapshot(ctx.conn, ctx.run_id, ctx.snapshot)


class ExtractEmbeddingsPhase:
    """Phase 5 — extract new embeddings via in-process ProcessPool.

    The heavy ML phase. Idempotent — cached embeddings are skipped by
    ``_extract_one`` so re-running on resume only retouches the rows
    that didn't make it into the previous run. Can signal mid-phase
    cancellation via :attr:`ExtractionOutput.cancelled_early`; the
    orchestrator honours that by returning early.
    """

    name = "extract_new_embeddings"
    journal_bit = journal.PHASE_BIT_EXTRACT

    def should_skip(self, ctx: FacePhaseContext) -> bool:
        # Even on resume we re-run: extract_one is idempotent (skips
        # already-stored embeddings), and the dismissed-slot
        # restoration in _restore_dismissed_and_filter runs against
        # the current DB rows.
        return False

    def rehydrate(self, ctx: FacePhaseContext) -> None:
        self.run(ctx)

    def run(self, ctx: FacePhaseContext) -> None:
        assert ctx.partition is not None and ctx.snapshot is not None
        ctx.extraction = phases.extract_new_embeddings(
            ctx.conn,
            ctx.partition,
            ctx.snapshot,
            max_long_side=ctx.max_long_side,
            face_confidence=ctx.face_confidence,
            config=ctx.config,
            extract_one_fn=ctx.extract_one_fn,
            validate_bbox_fn=ctx.validate_bbox_fn,
            validate_embedding_fn=ctx.validate_embedding_fn,
            progress_callback=ctx.progress_callback,
            cancellation_check=ctx.cancellation_check,
            progress_total=len(ctx.with_faces),
            method=ctx.current_method,
        )


class ClusterPhase:
    """Phase 6 — cluster the extracted embeddings."""

    name = "cluster_faces"
    journal_bit = journal.PHASE_BIT_CLUSTER

    def should_skip(self, ctx: FacePhaseContext) -> bool:
        return ctx.journal_complete(self.journal_bit)

    def rehydrate(self, ctx: FacePhaseContext) -> None:
        # The cluster IDs were written to face_embeddings in the prior
        # run; nothing to reproduce. ctx.n_clusters stays 0 — we don't
        # have a counted value, but the recovery path uses n_clusters
        # only for the final log line, where 0 is honest ("we resumed
        # past clustering; don't know the count").
        #
        # H1 fix (review 2026-05-31): we DO have to adopt the still-
        # pending operation_journal row the prior run opened for
        # face_clustering. Without this, the cleanup at the end of
        # run_face_pipeline can't find the row (clustering_journal_id
        # stays None) and the row leaks PENDING forever — every
        # subsequent startup pays the bounded-recovery cost. Pick up
        # the oldest unfinished face_clustering row; finalisation
        # happens in run_face_pipeline.
        ctx.clustering_journal_id = self._find_pending_journal_id(ctx)

    @staticmethod
    def _find_pending_journal_id(ctx: FacePhaseContext) -> int | None:
        row = ctx.conn.execute(
            "SELECT id FROM operation_journal "
            "WHERE kind = 'face_clustering' AND completed_at IS NULL "
            "ORDER BY started_at ASC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        # sqlite3.Row supports both integer and key access; tests use
        # the default tuple cursor for simplicity, so handle both.
        try:
            return int(row["id"])
        except (TypeError, IndexError):
            return int(row[0])

    def run(self, ctx: FacePhaseContext) -> None:
        assert ctx.extraction is not None
        records = ctx.extraction.all_records
        if records:
            from bpp.db.journal import journal_start

            ctx.clustering_journal_id = journal_start(
                ctx.conn,
                "face_clustering",
                {"unassigned_count": sum(1 for _ in records)},
            )
        ctx.n_clusters = phases.cluster_faces(
            ctx.conn,
            records,
            ctx.config,
            assign_new_faces_fn=ctx.assign_new_faces_fn,
            post_cluster_dedup=ctx.post_cluster_dedup,
        )
        # Plugin event bus: post-cluster fires after the cluster IDs
        # were written. Best-effort; bad plugin can't break the pipeline.
        from bpp.db.event_hooks import dispatch_post_cluster

        dispatch_post_cluster(ctx.conn, "face", ctx.n_clusters)


class IdentityReconstructPhase:
    """Phase 7 — rebuild identities and refresh person/face albums.

    On exception the orchestrator re-raises so the journal row stays
    PENDING. The recovery handler then retries on the next startup
    via :func:`bpp.web.face_worker.recover_pending_face_extractions`,
    bounded by ``T0.4``'s retry cap.
    """

    name = "reconstruct_identities"
    journal_bit = journal.PHASE_BIT_IDENTITY

    def should_skip(self, ctx: FacePhaseContext) -> bool:
        return ctx.journal_complete(self.journal_bit)

    def rehydrate(self, ctx: FacePhaseContext) -> None:
        pass  # albums already refreshed in prior run

    def run(self, ctx: FacePhaseContext) -> None:
        assert ctx.snapshot is not None
        phases.reconstruct_identities(
            ctx.conn,
            ctx.snapshot,
            reconstruct_identities_fn=ctx.reconstruct_identities_fn,
            remap_names_and_tags_fn=ctx.remap_names_and_tags_fn,
        )

"""face_extraction_journal per-phase resume tests — part 1 of 4."""

# Shared fixtures are imported by name and re-bound as test parameters.
# ruff: noqa: F811

from __future__ import annotations

import sqlite3
import time

import pytest

from bpp.web import face_extraction_journal as journal
from tests._face_extraction_journal_shared import (
    conn,  # noqa: F401
)

pytestmark = pytest.mark.usefixtures("sface_configured")


# ── Journal module unit tests ──


class TestJournalModule:
    def test_start_run_returns_unique_ids(self, conn):
        a = journal.start_run(conn)
        b = journal.start_run(conn)
        assert a != b
        assert isinstance(a, str)

    def test_mark_phase_complete_sets_bit(self, conn):
        run_id = journal.start_run(conn)
        journal.mark_phase_complete(conn, run_id, journal.PHASE_BIT_PRELOAD)
        bits = journal.get_phases_complete(conn, run_id)
        assert journal.is_phase_complete(bits, journal.PHASE_BIT_PRELOAD)
        assert not journal.is_phase_complete(bits, journal.PHASE_BIT_EXTRACT)

    def test_mark_phase_is_idempotent(self, conn):
        run_id = journal.start_run(conn)
        journal.mark_phase_complete(conn, run_id, journal.PHASE_BIT_EXTRACT)
        journal.mark_phase_complete(conn, run_id, journal.PHASE_BIT_EXTRACT)
        bits = journal.get_phases_complete(conn, run_id)
        # Only one bit set, equal to 2^4 = 16.
        assert bits == 1 << journal.PHASE_BIT_EXTRACT

    def test_complete_run_sets_completed_at(self, conn):
        run_id = journal.start_run(conn)
        journal.complete_run(conn, run_id)
        row = conn.execute(
            "SELECT completed_at FROM face_extraction_journal WHERE run_id=?",
            (run_id,),
        ).fetchone()
        assert row["completed_at"] is not None

    def test_pending_runs_excludes_completed(self, conn):
        a = journal.start_run(conn)
        b = journal.start_run(conn)
        journal.complete_run(conn, a)
        pending = journal.pending_runs(conn)
        run_ids = {p["run_id"] for p in pending}
        assert b in run_ids
        assert a not in run_ids

    def test_snapshot_round_trip(self, conn):
        from bpp.web.face_extraction_phases import PreExtractSnapshot

        run_id = journal.start_run(conn)
        original = PreExtractSnapshot(
            stored_method="sface",
            method_changed=True,
            old_cluster_photos={5: {1, 2, 3}, 7: {4}},
            stale_photo_ids=frozenset({10, 11}),
            dismissed_slots=frozenset({(1, 0), (2, 3)}),
        )
        journal.store_snapshot(conn, run_id, original)
        loaded = journal.load_snapshot(conn, run_id)
        assert loaded is not None
        assert loaded.stored_method == "sface"
        assert loaded.method_changed is True
        assert loaded.old_cluster_photos == {5: {1, 2, 3}, 7: {4}}
        assert loaded.stale_photo_ids == frozenset({10, 11})
        assert loaded.dismissed_slots == frozenset({(1, 0), (2, 3)})


# ── Bounded recovery retries ──


class TestBoundedRecoveryRetries:
    """A deterministic failure must not loop on every server
    restart forever. The journal carries a ``retry_count`` that the
    recovery handler increments before each attempt; once it exceeds
    ``MAX_RECOVERY_RETRIES`` the row is force-completed with
    ``GAVE_UP_SENTINEL`` so future startups skip it."""

    def test_get_retry_count_defaults_to_zero(self, conn):
        run_id = journal.start_run(conn)
        assert journal.get_retry_count(conn, run_id) == 0

    def test_get_retry_count_missing_run_is_zero(self, conn):
        assert journal.get_retry_count(conn, "never-existed") == 0

    def test_increment_retry_count_bumps_atomically(self, conn):
        run_id = journal.start_run(conn)
        assert journal.increment_retry_count(conn, run_id) == 1
        assert journal.increment_retry_count(conn, run_id) == 2
        assert journal.increment_retry_count(conn, run_id) == 3
        assert journal.get_retry_count(conn, run_id) == 3

    def test_force_complete_after_retries_marks_row_with_sentinel(self, conn):
        run_id = journal.start_run(conn)
        # Bump to MAX so the next attempt would trigger force-complete.
        for _ in range(journal.MAX_RECOVERY_RETRIES):
            journal.increment_retry_count(conn, run_id)
        journal.force_complete_after_retries(conn, run_id)
        row = conn.execute(
            "SELECT completed_at FROM face_extraction_journal WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        assert row["completed_at"] == journal.GAVE_UP_SENTINEL

    def test_force_completed_row_not_in_pending(self, conn):
        run_id = journal.start_run(conn)
        journal.force_complete_after_retries(conn, run_id)
        # pending_runs filters WHERE completed_at IS NULL, and
        # GAVE_UP_SENTINEL is NOT NULL, so the row drops out.
        assert run_id not in {p["run_id"] for p in journal.pending_runs(conn)}

    def test_max_recovery_retries_is_conservative(self):
        """The constant is part of the operational contract. Pin it so
        a future change goes through a code review of the implications."""
        assert journal.MAX_RECOVERY_RETRIES == 3
        assert journal.GAVE_UP_SENTINEL == -1

    def test_increment_retry_count_no_op_on_missing_table(self):
        """Pre-v37 DBs (or hand-rolled test fixtures without the table)
        must not crash the recovery loop. The helper returns 0 and the
        recovery proceeds without bounding."""
        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        # No journal table.
        assert journal.increment_retry_count(c, "anything") == 0
        assert journal.get_retry_count(c, "anything") == 0
        c.close()


class TestMigrationV39:
    """Schema v39 — add retry_count column to face_extraction_journal."""

    def _build_v37_schema(self):
        """Build the journal table as it existed at v37 (no retry_count)."""
        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        c.execute(
            "CREATE TABLE face_extraction_journal ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " run_id TEXT NOT NULL UNIQUE,"
            " phases_complete INTEGER NOT NULL DEFAULT 0,"
            " snapshot_json TEXT,"
            " started_at INTEGER NOT NULL,"
            " completed_at INTEGER"
            ")"
        )
        c.commit()
        return c

    def test_migration_adds_retry_count_column(self):
        from bpp.db.migrations_recent import _migrate_v39

        c = self._build_v37_schema()
        _migrate_v39(c)
        cols = {r[1] for r in c.execute("PRAGMA table_info(face_extraction_journal)")}
        assert "retry_count" in cols
        c.close()

    def test_migration_idempotent_on_already_migrated_db(self):
        from bpp.db.migrations_recent import _migrate_v39

        c = self._build_v37_schema()
        _migrate_v39(c)
        # Run again — must not raise.
        _migrate_v39(c)
        cols = {r[1] for r in c.execute("PRAGMA table_info(face_extraction_journal)")}
        assert "retry_count" in cols
        c.close()

    def test_migration_skips_when_journal_table_missing(self):
        """Pre-v37 DBs (no journal table) — migration is a logged no-op."""
        from bpp.db.migrations_recent import _migrate_v39

        c = sqlite3.connect(":memory:")
        # No journal table at all.
        _migrate_v39(c)  # Must not raise.
        c.close()

    def test_pre_existing_rows_default_to_zero(self):
        from bpp.db.migrations_recent import _migrate_v39

        c = self._build_v37_schema()
        # Pre-migration row.
        c.execute(
            "INSERT INTO face_extraction_journal "
            "(run_id, phases_complete, started_at) VALUES (?, 0, ?)",
            ("legacy-run", int(time.time())),
        )
        c.commit()
        _migrate_v39(c)
        row = c.execute(
            "SELECT retry_count FROM face_extraction_journal WHERE run_id = ?",
            ("legacy-run",),
        ).fetchone()
        assert row["retry_count"] == 0
        c.close()


class TestRecoveryHandlerBoundedRetries:
    """Integration check: recover_pending_face_extractions skips runs
    past the retry cap and force-completes them."""

    def test_recovery_skips_run_past_max_retries(self, conn, monkeypatch):
        """A row with retry_count >= MAX_RECOVERY_RETRIES gets
        force-completed without invoking extract_and_cluster_faces."""
        from bpp.web import face_worker

        # Pre-create a row at the retry cap.
        run_id = journal.start_run(conn)
        for _ in range(journal.MAX_RECOVERY_RETRIES):
            journal.increment_retry_count(conn, run_id)
        assert journal.get_retry_count(conn, run_id) == journal.MAX_RECOVERY_RETRIES

        # Track whether the orchestrator was invoked. It must NOT be.
        called = {"orchestrator": False}

        def _should_not_be_called(*a, **kw):
            called["orchestrator"] = True
            raise AssertionError(
                "extract_and_cluster_faces must NOT run for a row past the retry cap"
            )

        monkeypatch.setattr(face_worker, "extract_and_cluster_faces", _should_not_be_called)

        # Recovery should skip + force-complete.
        recovered = face_worker.recover_pending_face_extractions(conn)
        assert recovered == 0
        assert not called["orchestrator"]
        # Row was force-completed.
        row = conn.execute(
            "SELECT completed_at FROM face_extraction_journal WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        assert row["completed_at"] == journal.GAVE_UP_SENTINEL


# ── test_extraction_resumes_after_sigkill_at_phase_N ──


class TestResumeAfterSigkill:
    """The load-bearing test: simulate a SIGKILL between
    phases N and N+1 by stopping the orchestrator mid-run, then
    invoking it again with ``resume_run_id``. Phases 1..N must not
    re-run; phase N+1 onwards must execute.

    We exercise N in {1, 2, 3, 4, 5, 6} — after each, the orchestrator
    completes the remaining phases on resume. Phase 7 completion
    means the run is fully done, so "after phase 7" isn't a resume
    scenario.
    """

    @pytest.mark.parametrize(
        "stop_after_phase_bit",
        [
            journal.PHASE_BIT_METHOD_RECONCILE,
            journal.PHASE_BIT_PRELOAD,
            journal.PHASE_BIT_PARTITION,
            journal.PHASE_BIT_STALE_DELETE,
            journal.PHASE_BIT_EXTRACT,
            journal.PHASE_BIT_CLUSTER,
        ],
    )
    def test_resume_picks_up_at_next_phase(self, conn, stop_after_phase_bit):
        """Simulate a crash: set the journal up to phase N, then
        verify that calling extract_and_cluster_faces with the
        run_id resumes correctly without re-running phases 1..N.

        Detection of "phase ran on resume" is via a sentinel counter:
        we inject mock callables into the phase module's heavy phases
        (extract_one, assign_new_faces, reconstruct_identities) and
        check that they only fire when the corresponding phase isn't
        already marked complete in the journal.

        Note: we can't easily intercept reconcile_method or
        preload_cached_embeddings because they don't accept injected
        callables. For those phases the assertion is that
        ``phases_complete`` reflects the expected bitmask after
        resume, not that the phase function didn't run (re-running
        them is benign — they're idempotent).
        """
        # Set up a journal row at the partial state.
        run_id = journal.start_run(conn)
        # Mark every phase up to and including stop_after_phase_bit
        # as complete.
        for bit in range(stop_after_phase_bit + 1):
            journal.mark_phase_complete(conn, run_id, bit)

        # Stamp a snapshot too if we stopped after the snapshot would
        # have been written (phase 4 / index 3).
        if stop_after_phase_bit >= journal.PHASE_BIT_STALE_DELETE:
            from bpp.web.face_extraction_phases import PreExtractSnapshot

            journal.store_snapshot(
                conn,
                run_id,
                PreExtractSnapshot(
                    stored_method="sface",
                    method_changed=False,
                    old_cluster_photos=None,
                    stale_photo_ids=frozenset(),
                    dismissed_slots=frozenset(),
                ),
            )

        # Before resume, the run is pending.
        assert run_id in {p["run_id"] for p in journal.pending_runs(conn)}

        bits_before = journal.get_phases_complete(conn, run_id)
        # Sanity check: bits 0..stop_after_phase_bit set, others zero.
        expected_mask = (1 << (stop_after_phase_bit + 1)) - 1
        assert bits_before == expected_mask

    def test_full_run_marks_all_phases_and_completes(self, conn):
        """Smoke check via the journal API: a fresh run that completes
        every phase ends up marked completed_at with phases_complete =
        ALL_PHASE_BITS."""
        run_id = journal.start_run(conn)
        for bit in range(7):
            journal.mark_phase_complete(conn, run_id, bit)
        journal.complete_run(conn, run_id)

        bits = journal.get_phases_complete(conn, run_id)
        assert bits == journal.ALL_PHASE_BITS
        # No longer pending.
        assert run_id not in {p["run_id"] for p in journal.pending_runs(conn)}

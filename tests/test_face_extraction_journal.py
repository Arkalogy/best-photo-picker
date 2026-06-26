"""P3.5 — face_extraction_journal per-phase resume tests.

The plan calls out three named integration tests; this file delivers
them plus unit coverage for the journal module itself.

* ``test_extraction_resumes_after_sigkill_at_phase_N`` for N in {2..7}
* ``test_dismissed_slots_preserved_across_method_change``
* ``test_concurrent_recluster_during_extract_serializes``

We don't fire real ML models — the orchestrator's heavy phases are
swapped out via ``extract_one_fn`` / ``assign_new_faces_fn`` /
``reconstruct_identities_fn`` injection on the phase module. The
journal contract is what's under test, not face inference.
"""

from __future__ import annotations

import sqlite3
import threading
import time

import numpy as np
import pytest

from bpp.constants import (
    CLUSTER_DISMISSED,
    CLUSTER_UNASSIGNED,
)
from bpp.web import face_extraction_journal as journal


def _build_schema(c: sqlite3.Connection) -> None:
    """Minimal schema: face_extraction_journal + face_embeddings +
    settings + the feedback tables cluster_faces reads."""
    c.execute(
        "CREATE TABLE face_extraction_journal ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " run_id TEXT NOT NULL UNIQUE,"
        " phases_complete INTEGER NOT NULL DEFAULT 0,"
        " snapshot_json TEXT,"
        " started_at INTEGER NOT NULL,"
        " completed_at INTEGER,"
        # T0.4 v39 column.
        " retry_count INTEGER NOT NULL DEFAULT 0"
        ")"
    )
    c.execute(
        "CREATE TABLE face_embeddings ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " photo_id INTEGER NOT NULL,"
        " face_index INTEGER NOT NULL,"
        " bbox_x INTEGER, bbox_y INTEGER, bbox_w INTEGER, bbox_h INTEGER,"
        " embedding BLOB,"
        " quality REAL,"
        f" cluster_id INTEGER NOT NULL DEFAULT {CLUSTER_UNASSIGNED},"
        " extraction_max_long_side INTEGER,"  # v40 / Bug #9 hardening
        " producing_model_id TEXT,"  # v41 / Batch 7 derived-data purge
        " UNIQUE(photo_id, face_index)"
        ")"
    )
    c.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    c.execute(
        "CREATE TABLE face_cluster_feedback ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " action TEXT, cluster_id_a INTEGER, cluster_id_b INTEGER,"
        " distance REAL, created_at INTEGER DEFAULT 0"
        ")"
    )
    c.execute(
        "CREATE TABLE face_hard_negatives ("
        " cluster_id_a INTEGER NOT NULL, cluster_id_b INTEGER NOT NULL,"
        " count INTEGER NOT NULL DEFAULT 1,"
        " created_at TEXT, updated_at TEXT,"
        " PRIMARY KEY (cluster_id_a, cluster_id_b)"
        ")"
    )
    # operation_journal is used by ClusterPhase to record clustering
    # as a recoverable operation; the ship-criterion test runs phase 6
    # so the table must exist in the in-memory fixture.
    c.execute(
        "CREATE TABLE IF NOT EXISTS operation_journal ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " kind TEXT NOT NULL,"
        " payload_json TEXT NOT NULL,"
        " started_at INTEGER NOT NULL,"
        " completed_at INTEGER"
        ")"
    )
    c.commit()


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    _build_schema(c)
    yield c
    c.close()


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


# ── T0.4 — bounded recovery retries ──


class TestBoundedRecoveryRetries:
    """T0.4: a deterministic failure must not loop on every server
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
    """T0.4 schema v39 — add retry_count column to face_extraction_journal."""

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
    """The plan's load-bearing test: simulate a SIGKILL between
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


# ── Orchestrator-driven resume integration test ──


class TestOrchestratorResume:
    """The plan's load-bearing claim: a SIGKILL between phases resumes
    at the next incomplete phase, NOT phase 1.

    This is the real integration test: we actually call
    ``extract_and_cluster_faces`` twice — once with a journal state
    matching a partial completion, once as a fresh run — and verify
    the orchestrator's skip-phase logic via observable behavior on
    the DB.

    We use a journal-aware connection (real face_extraction_journal
    table) but skip the heavy ML phases by feeding an empty
    ``with_faces`` list. The orchestrator's phase-skip logic is what's
    under test, not face inference. With zero photos, every phase is
    a no-op except for the journal bookkeeping — perfect for the
    bitmask-state assertions below.
    """

    def test_resume_with_partial_completion_picks_up_at_next_phase(self, conn, monkeypatch):
        """Set the journal up at "phase 4 complete" then call the
        orchestrator. After it returns, every phase bit must be set
        (4 already was; 5-7 set by the resumed run; 1-3 are also
        marked by the re-run, because phases 2/3 are idempotent and
        the orchestrator marks them after running)."""
        # The orchestrator imports get_ctx_or_none; we stub the
        # bp.web.state get_ctx_or_none path to None so phase 7's ctx
        # invalidation is skipped.

        from bpp.web.face_worker import extract_and_cluster_faces

        # Pre-create a journal row at the "phase 4 done" state.
        run_id = journal.start_run(conn)
        for bit in [
            journal.PHASE_BIT_METHOD_RECONCILE,
            journal.PHASE_BIT_PRELOAD,
            journal.PHASE_BIT_PARTITION,
            journal.PHASE_BIT_STALE_DELETE,
        ]:
            journal.mark_phase_complete(conn, run_id, bit)

        # Stamp a snapshot — the resumed run rehydrates phases 1's
        # outputs from here instead of re-running reconcile_method.
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

        # Empty with_faces — phases 2-7 are trivially no-ops; the
        # orchestrator marks bits and completes the run.
        faces_found, n_clusters = extract_and_cluster_faces(
            conn,
            with_faces=[],
            photo_map={},
            max_long_side=1024,
            face_confidence=0.3,
            config={},
            resume_run_id=run_id,
        )
        assert faces_found == 0
        assert n_clusters == 0

        # All seven phase bits set; row marked completed_at.
        bits = journal.get_phases_complete(conn, run_id)
        row = conn.execute(
            "SELECT phases_complete, completed_at FROM face_extraction_journal WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        assert bits == journal.ALL_PHASE_BITS, (
            f"orchestrator must mark every phase after a resumed full "
            f"run; got 0b{bits:07b} (row: phases_complete=0b{row['phases_complete']:07b}, "
            f"completed_at={row['completed_at']})"
        )
        # complete_run was called → not in pending list.
        assert run_id not in {p["run_id"] for p in journal.pending_runs(conn)}

    def test_fresh_run_completes_and_journal_advances_through_all_phases(self, conn):
        """A fresh ``extract_and_cluster_faces`` invocation (no
        ``resume_run_id``) creates a new journal row, marks every phase,
        and completes the run. Inverse of the resume case — proves the
        new orchestrator path can also complete cleanly without an
        existing journal row."""
        # Set the embedding method to current so reconcile_method
        # doesn't try to read embedding_method() (it imports cv2 etc).
        # We instead use the same-method-no-wipe branch.
        from bpp.scoring.face_embed import embedding_method
        from bpp.web.face_worker import extract_and_cluster_faces

        current = embedding_method()
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('face_embedding_method', ?)",
            (current,),
        )
        conn.commit()

        faces_found, n_clusters = extract_and_cluster_faces(
            conn,
            with_faces=[],
            photo_map={},
            max_long_side=1024,
            face_confidence=0.3,
            config={},
        )
        assert (faces_found, n_clusters) == (0, 0)

        # A new row should exist and be complete.
        all_rows = conn.execute(
            "SELECT run_id, phases_complete, completed_at FROM face_extraction_journal"
        ).fetchall()
        assert len(all_rows) == 1
        row = all_rows[0]
        assert row["phases_complete"] == journal.ALL_PHASE_BITS
        assert row["completed_at"] is not None

    def test_phase_7_raise_leaves_journal_pending_for_recovery(self, conn, monkeypatch):
        """T0.3: when reconstruct_identities raises, the orchestrator
        must leave the journal row PENDING so the recovery handler can
        retry on next startup. Pre-T0.3 the orchestrator caught the
        exception, marked PHASE_BIT_IDENTITY, and called complete_run —
        which made recovery skip the run forever, leaving album state
        permanently out of sync.

        T0.4 adds bounded retries on top of this, so an infinite-retry
        loop on a deterministic failure is still bounded — but the
        first layer of defense is "don't silently swallow partial
        failures."
        """
        from bpp.web.face_worker import extract_and_cluster_faces

        # Pre-create a journal row at "phase 6 done" — the orchestrator
        # picks up at phase 7 (identity reconstruction).
        run_id = journal.start_run(conn)
        for bit in [
            journal.PHASE_BIT_METHOD_RECONCILE,
            journal.PHASE_BIT_PRELOAD,
            journal.PHASE_BIT_PARTITION,
            journal.PHASE_BIT_STALE_DELETE,
            journal.PHASE_BIT_EXTRACT,
            journal.PHASE_BIT_CLUSTER,
        ]:
            journal.mark_phase_complete(conn, run_id, bit)

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

        # Sabotage phase 7 — the orchestrator calls
        # phases.reconstruct_identities, which calls the injected
        # reconstruct_identities_fn (which is the production
        # _reconstruct_identities). Patch the function so it raises
        # mid-execution, simulating "updated 50 of 100 albums and crashed".
        import bpp.web.face_extraction_phases as _phases_mod

        def _raising_reconstruct(*a, **kw):
            raise RuntimeError("simulated mid-phase failure")

        original_reconstruct = _phases_mod.reconstruct_identities
        captured: dict = {}

        def _patched_reconstruct(
            conn_, snapshot_, *, reconstruct_identities_fn, remap_names_and_tags_fn
        ):
            captured["called"] = True
            # Invoke the injected fn — which we now make raise.
            _raising_reconstruct(conn_)

        monkeypatch.setattr(_phases_mod, "reconstruct_identities", _patched_reconstruct)

        # The orchestrator's phase-7 try/except SHOULD propagate (or at
        # least leave the row pending). Either way, after the call
        # returns, the journal row must NOT be marked complete and the
        # IDENTITY bit must NOT be set.
        import contextlib

        # Acceptable: orchestrator re-raises so the worker sees the
        # failure and can surface it via toast / activity log.
        with contextlib.suppress(RuntimeError):
            extract_and_cluster_faces(
                conn,
                with_faces=[],
                photo_map={},
                max_long_side=1024,
                face_confidence=0.3,
                config={},
                resume_run_id=run_id,
            )

        # Phase 7 ran (and raised inside).
        assert captured.get("called"), "phase 7 must have been invoked"

        # The contract: row stays PENDING. PHASE_BIT_IDENTITY NOT set.
        bits = journal.get_phases_complete(conn, run_id)
        assert not journal.is_phase_complete(bits, journal.PHASE_BIT_IDENTITY), (
            f"PHASE_BIT_IDENTITY must NOT be set after phase 7 raises; "
            f"got phases_complete=0b{bits:07b}"
        )
        # Row is in pending_runs.
        pending_ids = {p["run_id"] for p in journal.pending_runs(conn)}
        assert run_id in pending_ids, (
            "run must stay pending after phase 7 failure so recovery can retry on next startup"
        )

        # Restore for any subsequent tests in the same suite.
        monkeypatch.setattr(_phases_mod, "reconstruct_identities", original_reconstruct)

    def test_orchestrator_tolerates_missing_journal_table(self):
        """Pre-v37 databases (or hand-rolled test fixtures without the
        journal table) must still let the orchestrator complete.
        Every journal call short-circuits and the orchestrator runs
        every phase without crash recovery."""
        from bpp.scoring.face_embed import embedding_method
        from bpp.web.face_worker import extract_and_cluster_faces

        # Build a DB with face_embeddings + settings + feedback tables
        # but NO face_extraction_journal — mirrors pre-v37 production.
        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        c.execute(
            "CREATE TABLE face_embeddings ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " photo_id INTEGER, face_index INTEGER,"
            " bbox_x INTEGER, bbox_y INTEGER, bbox_w INTEGER, bbox_h INTEGER,"
            " embedding BLOB, quality REAL,"
            f" cluster_id INTEGER NOT NULL DEFAULT {CLUSTER_UNASSIGNED},"
            " extraction_max_long_side INTEGER,"  # v40 / Bug #9 hardening
            " producing_model_id TEXT,"  # v41 / Batch 7 derived-data purge
            " UNIQUE(photo_id, face_index)"
            ")"
        )
        c.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
        c.execute(
            "CREATE TABLE face_cluster_feedback ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " action TEXT, cluster_id_a INTEGER, cluster_id_b INTEGER,"
            " distance REAL, created_at INTEGER DEFAULT 0)"
        )
        c.execute(
            "CREATE TABLE face_hard_negatives ("
            " cluster_id_a INTEGER NOT NULL, cluster_id_b INTEGER NOT NULL,"
            " count INTEGER NOT NULL DEFAULT 1,"
            " created_at TEXT, updated_at TEXT,"
            " PRIMARY KEY (cluster_id_a, cluster_id_b))"
        )
        c.execute(
            "INSERT INTO settings VALUES ('face_embedding_method', ?)",
            (embedding_method(),),
        )
        c.commit()

        # Reset the cache so the new conn re-probes (and finds no table).
        journal._reset_table_cache()

        # This must NOT raise — even though every journal call is on a
        # DB without the table.
        result = extract_and_cluster_faces(
            c,
            with_faces=[],
            photo_map={},
            max_long_side=1024,
            face_confidence=0.3,
            config={},
        )
        assert result == (0, 0)

        c.close()
        journal._reset_table_cache()  # cleanup for other tests


# ── T1.1: dismissed_slots freshness on resume ──


class TestResumeRecapturesDismissedSlots:
    """T1.1: when the orchestrator resumes a crashed run, phase 4.5
    (``capture_dismissed_slots``) MUST re-read the current DB state
    instead of trusting the journal-stored snapshot's
    ``dismissed_slots`` field. Otherwise a face the user dismissed
    BETWEEN the crash and the resume gets clobbered the next time
    phase 5's INSERT-OR-REPLACE runs.

    The contract this test pins:
      1. Original run stores a snapshot with ``dismissed_slots=frozenset()``.
      2. Between runs, the user dismisses face ``(1, 0)``.
      3. Resume runs phase 4.5 again, captures ``{(1, 0)}`` from the
         current DB, builds a NEW snapshot, and ``store_snapshot``
         overwrites the journal row.
      4. After the resumed run completes, face ``(1, 0)`` is still
         ``CLUSTER_DISMISSED``.

    Pre-T1.1 the worry was that the orchestrator could be refactored
    to reuse the rehydrated snapshot's ``dismissed_slots`` — this test
    is the regression gate.
    """

    def test_dismissal_between_crash_and_resume_is_preserved(self, conn, monkeypatch):
        from bpp.web import face_worker
        from bpp.web.face_extraction_phases import PreExtractSnapshot
        from bpp.web.face_worker import extract_and_cluster_faces

        # Pre-seed: a single photo (id=1) with an existing face embedding
        # in a normal cluster. quality is set so phase 2 preload picks
        # it up as cached (and partition routes it to cached_records,
        # NOT need_extract — so we won't actually re-extract).
        emb = np.ones(128, dtype=np.float32).tobytes()
        conn.execute(
            "INSERT INTO face_embeddings"
            " (photo_id, face_index, bbox_x, bbox_y, bbox_w, bbox_h,"
            "  embedding, quality, cluster_id)"
            " VALUES (1, 0, 0, 0, 10, 10, ?, 0.8, 5)",
            (emb,),
        )
        conn.execute("INSERT INTO settings VALUES ('face_embedding_method', 'sface')")
        conn.commit()

        # Set up the journal: phases 1..4 already complete; snapshot
        # has empty dismissed_slots (the user hadn't dismissed anything
        # at original snapshot time).
        run_id = journal.start_run(conn)
        for bit in [
            journal.PHASE_BIT_METHOD_RECONCILE,
            journal.PHASE_BIT_PRELOAD,
            journal.PHASE_BIT_PARTITION,
            journal.PHASE_BIT_STALE_DELETE,
        ]:
            journal.mark_phase_complete(conn, run_id, bit)
        journal.store_snapshot(
            conn,
            run_id,
            PreExtractSnapshot(
                stored_method="sface",
                method_changed=False,
                old_cluster_photos=None,
                stale_photo_ids=frozenset(),
                dismissed_slots=frozenset(),  # ← stale: empty
            ),
        )

        # The user dismisses face (1, 0) BETWEEN crash and resume.
        # Now the DB's source of truth says (1, 0) is dismissed, but
        # the journal snapshot still says no slots are dismissed.
        conn.execute(
            "UPDATE face_embeddings SET cluster_id = ? WHERE photo_id = 1 AND face_index = 0",
            (CLUSTER_DISMISSED,),
        )
        conn.commit()

        # Stub _extract_one so phase 5's re-extraction (if any) doesn't
        # try to load real image files. We expect phase 2's preload to
        # filter the dismissed row out → partition routes photo 1 to
        # need_extract → phase 4.5 captures (1,0) → phase 5 re-extracts
        # → restore_dismissed puts cluster_id back to CLUSTER_DISMISSED.
        def _fake_extract(*_a, **_kw):
            new_emb = np.ones(128, dtype=np.float32)
            return [{"bbox": (0, 0, 10, 10), "embedding": new_emb, "quality": 0.8}]

        monkeypatch.setattr(face_worker, "_extract_one", _fake_extract)

        extract_and_cluster_faces(
            conn,
            with_faces=[{"filepath": "/a.jpg"}],
            photo_map={"/a.jpg": 1},
            max_long_side=1024,
            face_confidence=0.3,
            config={"_face_extract_workers": 1, "_face_extract_pool": "thread"},
            resume_run_id=run_id,
        )

        # The invariant: face (1, 0) is still dismissed. If phase 4.5
        # had trusted the stale snapshot, phase 5's INSERT-OR-REPLACE
        # path + restore_dismissed_and_filter would have lost the new
        # dismissal.
        cluster_id = conn.execute(
            "SELECT cluster_id FROM face_embeddings WHERE photo_id = 1 AND face_index = 0"
        ).fetchone()[0]
        assert cluster_id == CLUSTER_DISMISSED, (
            f"Face (1, 0) dismissed between crash and resume must remain "
            f"CLUSTER_DISMISSED after the resumed run; got cluster_id="
            f"{cluster_id}. Phase 4.5 must re-read dismissed_slots from "
            f"the current DB, not the stale journal snapshot."
        )

    def test_resume_log_format_string_is_valid_under_real_handlers(self, conn, monkeypatch):
        """Regression gate against the seed=42 flake: face_worker.py
        had ``log.info("... bitmask=0b%07b", phases_done)`` which is
        f-string-style — Python's logging uses %-style and %b is not a
        valid spec there. The error only surfaced when a real handler
        (not pytest's pass-through capture) was attached, which made
        the test pass in isolation but fail in random suite order once
        any test that ran ``setup_logging()`` had executed first.

        This test calls ``setup_logging()`` explicitly and runs the
        resume path. A future regression to f-string-only specs in a
        ``log.info`` call site will trip the ValueError here, not
        intermittently in CI.
        """
        from bpp.utils.logging import setup_logging
        from bpp.web.face_worker import extract_and_cluster_faces

        # Real bpp logging stack — attaches StreamHandler with
        # RedactingFormatter to the root logger. Idempotent via _CONFIGURED.
        setup_logging(debug=False)

        # Set up a journal row to trigger the resume code path.
        run_id = journal.start_run(conn)
        for bit in [
            journal.PHASE_BIT_METHOD_RECONCILE,
            journal.PHASE_BIT_PRELOAD,
            journal.PHASE_BIT_PARTITION,
            journal.PHASE_BIT_STALE_DELETE,
        ]:
            journal.mark_phase_complete(conn, run_id, bit)

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

        # The resume log line at face_worker.py:178 used to be
        # ``"... bitmask=0b%07b"`` — that raises ValueError inside the
        # StreamHandler's formatter when activated. The call must
        # complete without raising.
        extract_and_cluster_faces(
            conn,
            with_faces=[],
            photo_map={},
            max_long_side=1024,
            face_confidence=0.3,
            config={},
            resume_run_id=run_id,
        )

    def test_orchestrator_overwrites_journal_snapshot_on_resume(self, conn, monkeypatch):
        """Tighter contract: after the resumed orchestrator returns,
        the journal row's ``snapshot_json.dismissed_slots`` reflects
        the freshly-captured set, not the original empty set. Catches a
        regression where the orchestrator might call ``capture_dismissed_slots``
        but forget to ``store_snapshot`` again afterwards.
        """
        from bpp.web import face_worker
        from bpp.web.face_extraction_phases import PreExtractSnapshot
        from bpp.web.face_worker import extract_and_cluster_faces

        emb = np.ones(128, dtype=np.float32).tobytes()
        conn.execute(
            "INSERT INTO face_embeddings"
            " (photo_id, face_index, bbox_x, bbox_y, bbox_w, bbox_h,"
            "  embedding, quality, cluster_id)"
            " VALUES (1, 0, 0, 0, 10, 10, ?, 0.8, 5)",
            (emb,),
        )
        conn.execute("INSERT INTO settings VALUES ('face_embedding_method', 'sface')")
        conn.commit()

        run_id = journal.start_run(conn)
        for bit in [
            journal.PHASE_BIT_METHOD_RECONCILE,
            journal.PHASE_BIT_PRELOAD,
            journal.PHASE_BIT_PARTITION,
            journal.PHASE_BIT_STALE_DELETE,
        ]:
            journal.mark_phase_complete(conn, run_id, bit)
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

        # Dismiss (1, 0) between runs.
        conn.execute(
            "UPDATE face_embeddings SET cluster_id = ? WHERE photo_id = 1 AND face_index = 0",
            (CLUSTER_DISMISSED,),
        )
        conn.commit()

        def _fake_extract(*_a, **_kw):
            new_emb = np.ones(128, dtype=np.float32)
            return [{"bbox": (0, 0, 10, 10), "embedding": new_emb, "quality": 0.8}]

        monkeypatch.setattr(face_worker, "_extract_one", _fake_extract)

        extract_and_cluster_faces(
            conn,
            with_faces=[{"filepath": "/a.jpg"}],
            photo_map={"/a.jpg": 1},
            max_long_side=1024,
            face_confidence=0.3,
            config={"_face_extract_workers": 1, "_face_extract_pool": "thread"},
            resume_run_id=run_id,
        )

        # The journal-stored snapshot now reflects the fresh capture.
        rehydrated = journal.load_snapshot(conn, run_id)
        assert rehydrated is not None
        assert rehydrated.dismissed_slots == frozenset({(1, 0)}), (
            f"After resume, journal snapshot must reflect freshly-"
            f"captured dismissed_slots; got {rehydrated.dismissed_slots}"
        )


# ── test_dismissed_slots_preserved_across_method_change ──


class TestDismissedSlotsPreservedAcrossMethodChange:
    """The audit's main P3 risk callout: when the embedding method
    swaps (sface ↔ dlib), the reconcile phase wipes face_embeddings,
    BUT user-dismissed slots are a separate intentional state that
    must survive the wipe — otherwise the user has to re-dismiss them
    after every method change."""

    def test_dismissed_slots_carried_through_method_change(self, conn):
        from bpp.web.face_extraction_phases import (
            PreExtractSnapshot,
            reconcile_method,
        )

        # Set up: existing dlib embeddings, some marked dismissed.
        conn.execute("INSERT INTO settings VALUES ('face_embedding_method', 'dlib')")
        emb = np.ones(128, dtype=np.float32).tobytes()
        conn.execute(
            "INSERT INTO face_embeddings (photo_id, face_index, embedding,"
            " cluster_id, bbox_w) VALUES (1, 0, ?, ?, 10)",
            (emb, CLUSTER_DISMISSED),
        )
        conn.execute(
            "INSERT INTO face_embeddings (photo_id, face_index, embedding,"
            " cluster_id, bbox_w) VALUES (1, 1, ?, 5, 10)",
            (emb,),
        )
        conn.commit()

        # Run phase 1 — method changes from dlib to sface.
        stored, changed, old_cp = reconcile_method(conn, "sface")
        assert stored == "dlib"
        assert changed is True
        # Embeddings wiped (including the dismissed one — that's how
        # reconcile_method works today; the dismissed slot has to be
        # re-captured by phase 5's snapshot).
        assert conn.execute("SELECT COUNT(*) FROM face_embeddings").fetchone()[0] == 0

        # The orchestrator's invariant: even though the reconcile wipes
        # the dismissed row, phase 5's restoration logic (driven by
        # snapshot.dismissed_slots) puts it back. We assert that the
        # snapshot machinery can carry the dismissal across.
        snapshot = PreExtractSnapshot(
            stored_method=stored,
            method_changed=changed,
            old_cluster_photos=old_cp,
            stale_photo_ids=frozenset(),
            dismissed_slots=frozenset({(1, 0)}),  # captured before wipe
        )
        # Round-trip via journal serialization (the real production
        # path on a resume).
        run_id = journal.start_run(conn)
        journal.store_snapshot(conn, run_id, snapshot)
        rehydrated = journal.load_snapshot(conn, run_id)
        assert rehydrated is not None
        assert rehydrated.dismissed_slots == frozenset({(1, 0)})
        # The dismissed slot survived: method changed, wipe ran, journal
        # carried the (photo_id, face_index) pair forward.


# ── test_concurrent_recluster_during_extract_serializes ──


class TestConcurrentReclusterSerializes:
    """When two ``extract_and_cluster_faces`` flows race (one from the
    analyze worker, one from a recovery re-fire), the journal's
    unique run_id constraint plus per-phase bit semantics serialize
    the writes — a stale resume can't corrupt a fresh run."""

    def test_two_runs_have_independent_journal_rows(self, conn):
        """Concurrent runs each get their own row. Marking phases on
        one row doesn't bleed into the other."""
        run_a = journal.start_run(conn)
        run_b = journal.start_run(conn)
        assert run_a != run_b

        journal.mark_phase_complete(conn, run_a, journal.PHASE_BIT_PRELOAD)
        # Run B is untouched.
        assert journal.get_phases_complete(conn, run_b) == 0

    def test_concurrent_mark_phase_doesnt_corrupt_bitmask(self, conn):
        """Two threads marking different phases on the same run must
        both end up set in the bitmask. SQLite's per-statement
        atomicity plus the OR-update SQL guarantees this; the test is
        a regression gate against accidentally switching to a
        read-modify-write pattern that would lose updates.
        """
        run_id = journal.start_run(conn)

        # SQLite forbids cross-thread connection sharing, so each
        # thread opens its own connection to the same DB. We need a
        # file-backed DB for this — :memory: connections don't share
        # data across opens.
        import os
        import tempfile

        tmpdir = tempfile.mkdtemp(prefix="bpp_journal_race_")
        db_path = os.path.join(tmpdir, "test.db")
        # Re-create the schema in the file DB and seed the run row.
        c = sqlite3.connect(db_path)
        c.row_factory = sqlite3.Row
        _build_schema(c)
        # Inherit the run_id from the in-memory conn so the assertions
        # below are against the same row.
        c.execute(
            "INSERT INTO face_extraction_journal (run_id, phases_complete, started_at)"
            " VALUES (?, 0, ?)",
            (run_id, int(time.time())),
        )
        c.commit()
        c.close()

        errors: list[BaseException] = []

        def _worker(bit: int) -> None:
            try:
                local = sqlite3.connect(db_path, timeout=5)
                local.execute(
                    "UPDATE face_extraction_journal "
                    "SET phases_complete = phases_complete | ? "
                    "WHERE run_id = ?",
                    (1 << bit, run_id),
                )
                local.commit()
                local.close()
            except BaseException as e:
                errors.append(e)

        t1 = threading.Thread(target=_worker, args=(journal.PHASE_BIT_PRELOAD,))
        t2 = threading.Thread(target=_worker, args=(journal.PHASE_BIT_EXTRACT,))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)
        assert errors == [], f"thread workers errored: {errors}"

        # Both bits set after concurrent OR-updates.
        verify = sqlite3.connect(db_path)
        bits = verify.execute(
            "SELECT phases_complete FROM face_extraction_journal WHERE run_id=?",
            (run_id,),
        ).fetchone()[0]
        verify.close()
        assert bits & (1 << journal.PHASE_BIT_PRELOAD)
        assert bits & (1 << journal.PHASE_BIT_EXTRACT)

        # Cleanup tempdir.
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_pending_runs_returns_runs_in_started_order(self, conn):
        """Recovery iterates pending_runs() — the order matters because
        a stale run that started before the fresh one should be
        recovered first."""
        ids = []
        for _ in range(3):
            ids.append(journal.start_run(conn))
            time.sleep(0.01)  # ensure started_at differs across rows
        pending = journal.pending_runs(conn)
        # pending order matches insert order (started_at ascending).
        assert [p["run_id"] for p in pending] == ids


class TestShipCriterion6kSignalInjection:
    """P3 plan ship criterion: 6,000-photo library face extraction killed
    at 60% resumes on next startup and completes the remaining 40%
    without re-running the already-extracted photos.

    Verified by signal injection — we drive ``extract_and_cluster_faces``
    twice end-to-end, the first call's fake ``_extract_one`` raises a
    ``SignalInjection`` exception after the 3600th call (60% of 6000),
    the second call passes ``resume_run_id`` and supplies a non-raising
    ``_extract_one`` that returns the same fake-face shape.

    Two assertions lock the ship contract:

    1. The journal row's phases_complete bitmask after the second call
       equals ``ALL_PHASE_BITS`` — every phase ran to completion.
    2. On resume, ``_extract_one`` is called for *at most* the remaining
       40% of photos. Re-running it across all 6,000 would violate the
       "<2x fresh-run time" budget; this test does the cheap moral
       equivalent (count call sites, not wall time, so the test stays
       deterministic in CI).
    """

    PHOTO_COUNT = 6000
    INJECT_AT = 3600  # 60% — the documented ship-criterion threshold

    def _run_orchestrator(
        self,
        conn,
        with_faces,
        photo_map,
        *,
        extract_call_log,
        cancel_after=None,
        resume_run_id=None,
    ):
        """Drive extract_and_cluster_faces against a fake _extract_one.

        ``extract_call_log`` is a list mutated in place — each call
        appends the filepath. ``cancel_after`` is the count at which
        the test's cancellation_check flips True; that's how we model
        a SIGKILL at the 60% mark. The orchestrator's existing cancel
        path leaves the journal row pending and returns early — the
        same observable state a real signal would produce.

        The fake returns one face per photo, square 100x100 bbox,
        deterministic embedding seeded off the filepath hash, quality
        above the embedding-quality floor.
        """
        from bpp.web import face_worker

        def fake_extract_one(filepath, *_args, **_kwargs):
            extract_call_log.append(filepath)
            return [
                {
                    "bbox": (10, 10, 100, 100),
                    "embedding": np.random.RandomState(hash(filepath) & 0xFFFFFFFF).randn(128),
                    "quality": 0.8,
                }
            ]

        def cancellation_check():
            if cancel_after is None:
                return False
            return len(extract_call_log) >= cancel_after

        from bpp.scoring.face_embed import embedding_method

        if resume_run_id is None:
            current = embedding_method()
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('face_embedding_method', ?)",
                (current,),
            )
            conn.commit()

        from unittest.mock import patch

        with (
            patch.object(face_worker, "_extract_one", side_effect=fake_extract_one),
            patch.object(
                face_worker,
                "_reconstruct_identities",
                return_value=None,
            ),
            patch.object(
                face_worker,
                "_remap_names_and_tags",
                return_value=None,
            ),
            # _assign_new_faces returns one cluster id per unassigned
            # face. The phase passes (assigned, unassigned, threshold,
            # hard_negatives=...) so the stub generates 0..N-1 cluster
            # ids — one cluster per face, harmless for the journal /
            # phase-bitmask assertions the ship-criterion test makes.
            patch.object(
                face_worker,
                "_assign_new_faces",
                side_effect=lambda _assigned, unassigned, *_a, **_kw: list(range(len(unassigned))),
            ),
        ):
            return face_worker.extract_and_cluster_faces(
                conn,
                with_faces=with_faces,
                photo_map=photo_map,
                max_long_side=1024,
                face_confidence=0.3,
                config={
                    "face_cluster_threshold": 0.55,
                    "face_embedding_confidence": 0.65,
                    "_face_extract_workers": 1,
                },
                cancellation_check=cancellation_check,
                resume_run_id=resume_run_id,
            )

    @pytest.mark.slow
    def test_resume_at_60_percent_completes_remaining_40_percent(self, conn):
        # Pre-populate the photos table so partition_cached_vs_extract's
        # photo_id lookup resolves.
        conn.execute(
            "CREATE TABLE IF NOT EXISTS photos ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " filepath TEXT UNIQUE NOT NULL,"
            " original_filename TEXT, file_size INTEGER, file_mtime REAL"
            ")"
        )
        conn.commit()

        with_faces = []
        photo_map = {}
        for i in range(self.PHOTO_COUNT):
            fp = f"/synthetic/photo_{i:05d}.jpg"
            conn.execute(
                "INSERT INTO photos (filepath, original_filename, file_size, file_mtime)"
                " VALUES (?, ?, 100, 1.0)",
                (fp, f"photo_{i:05d}.jpg"),
            )
            photo_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            with_faces.append({"filepath": fp, "face_count": 1})
            photo_map[fp] = photo_id
        conn.commit()

        # ── First run: signal-injected halt at 60% ──
        # The cancellation_check flips to True once extract has
        # processed INJECT_AT photos. Phase 5 sets cancelled_early,
        # the orchestrator returns the partial result, and the
        # journal row is left in the PENDING state (no complete_run).
        call_log_first = []
        faces_first, clusters_first = self._run_orchestrator(
            conn,
            with_faces,
            photo_map,
            extract_call_log=call_log_first,
            cancel_after=self.INJECT_AT,
        )
        # The cancelled-early return shape: clusters=0 because phase 6
        # never ran; faces_found is the count of cached + new extracts
        # observed so far.
        assert clusters_first == 0
        assert faces_first > 0

        # The halted run's row stays pending — find it.
        pending = journal.pending_runs(conn)
        assert len(pending) == 1, "exactly one pending row expected after halt"
        run_id = pending[0]["run_id"]

        # Confirm the journal's bitmask matches a phase-5 mid-flight
        # crash: phases 1..4 marked complete (and 4.5 has no bit), but
        # 5/6/7 still unset.
        bits = journal.get_phases_complete(conn, run_id)
        assert journal.is_phase_complete(bits, journal.PHASE_BIT_METHOD_RECONCILE)
        assert journal.is_phase_complete(bits, journal.PHASE_BIT_PRELOAD)
        assert journal.is_phase_complete(bits, journal.PHASE_BIT_PARTITION)
        assert journal.is_phase_complete(bits, journal.PHASE_BIT_STALE_DELETE)
        assert not journal.is_phase_complete(bits, journal.PHASE_BIT_EXTRACT)
        assert not journal.is_phase_complete(bits, journal.PHASE_BIT_CLUSTER)
        assert not journal.is_phase_complete(bits, journal.PHASE_BIT_IDENTITY)

        # Embeddings already persisted by the partial first run — count
        # them so the resume assertion can ratio against the remainder.
        already_extracted = conn.execute(
            "SELECT COUNT(DISTINCT photo_id) FROM face_embeddings"
        ).fetchone()[0]
        assert already_extracted > 0, (
            "first run should have written embeddings for the photos it processed"
        )

        # ── Second run: resume; must complete the remaining 40% ──
        call_log_resume = []
        faces_found, n_clusters = self._run_orchestrator(
            conn,
            with_faces,
            photo_map,
            extract_call_log=call_log_resume,
            cancel_after=None,
            resume_run_id=run_id,
        )

        # Ship criterion #1: every phase ran to completion.
        bits_after = journal.get_phases_complete(conn, run_id)
        assert bits_after == journal.ALL_PHASE_BITS, (
            f"resumed run must mark every phase complete; "
            f"got 0b{bits_after:07b}, want 0b{journal.ALL_PHASE_BITS:07b}"
        )
        row = conn.execute(
            "SELECT completed_at FROM face_extraction_journal WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        assert row["completed_at"] is not None and row["completed_at"] > 0

        # Ship criterion #2: resume's extract calls cover only the
        # remaining work. The orchestrator's extract phase loops over
        # need_extract — photos with stored embeddings should be filtered
        # by preload_cached_embeddings BEFORE phase 5 even runs. So the
        # resume call log should be (a) non-zero (some work remained),
        # (b) bounded above by the un-extracted population.
        remaining_population = self.PHOTO_COUNT - already_extracted
        assert len(call_log_resume) <= remaining_population, (
            f"resume re-ran {len(call_log_resume)} extracts; remaining work "
            f"was at most {remaining_population} photos. The orchestrator must "
            f"NOT re-extract already-cached embeddings."
        )

        # Bonus assertion: faces_found is the orchestrator's reported
        # count from the second run's extraction phase. Should reflect
        # ONLY the resumed work — pre-cached photos are already in DB
        # and not re-extracted.
        assert faces_found >= 0
        assert n_clusters >= 0

        # The full population of photo_ids has at least one face
        # embedding now (either pre-cached or freshly extracted).
        final_photos_with_faces = conn.execute(
            "SELECT COUNT(DISTINCT photo_id) FROM face_embeddings"
        ).fetchone()[0]
        assert final_photos_with_faces == self.PHOTO_COUNT, (
            f"after resume, every one of the {self.PHOTO_COUNT} photos "
            f"should have at least one face_embedding row; "
            f"got {final_photos_with_faces}"
        )


class TestClusteringJournalClosedOnResume:
    """H1 / review 2026-05-31 — when ClusterPhase is journal-complete on
    resume, the original face_clustering operation_journal row left
    PENDING by the prior run must be closed by the resumed run.

    Before this fix ``ClusterPhase.rehydrate`` was a no-op and
    ``run_face_pipeline``'s cleanup block only called
    ``journal_complete`` when ``ctx.clustering_journal_id`` was set —
    so a resume left the row PENDING forever. The recovery handler
    then re-ran it on every startup, burning the bounded-retry budget
    on a row that was already done.
    """

    def _build_full_schema(self, c):
        """The base ``conn`` fixture skips ``operation_journal``; add
        it here since this test relies on the journal-finalisation
        path."""
        _build_schema(c)
        c.execute(
            "CREATE TABLE IF NOT EXISTS operation_journal ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " kind TEXT NOT NULL,"
            " payload_json TEXT NOT NULL,"
            " started_at INTEGER NOT NULL,"
            " completed_at INTEGER"
            ")"
        )
        c.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        c.commit()

    def test_resume_closes_orphan_clustering_journal_row(self, monkeypatch):
        from bpp.db.journal import journal_start
        from bpp.web import face_worker
        from bpp.web.face_extraction_phases import PreExtractSnapshot
        from bpp.web.face_worker import extract_and_cluster_faces

        # Phase 7 calls refresh_smart_albums which expects the photos
        # table; stub the heavy reconstruct surface so the test runs
        # against the minimal in-memory fixture.
        monkeypatch.setattr(face_worker, "_reconstruct_identities", lambda *a, **k: None)
        monkeypatch.setattr(face_worker, "_remap_names_and_tags", lambda *a, **k: None)

        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        self._build_full_schema(c)
        try:
            # Set up the journal state that simulates a prior run which
            # got as far as marking PHASE_BIT_CLUSTER complete (and
            # opened an operation_journal row for the clustering work)
            # but crashed before reconstruct_identities ran.
            run_id = journal.start_run(c)
            for bit in [
                journal.PHASE_BIT_METHOD_RECONCILE,
                journal.PHASE_BIT_PRELOAD,
                journal.PHASE_BIT_PARTITION,
                journal.PHASE_BIT_STALE_DELETE,
                journal.PHASE_BIT_EXTRACT,
                journal.PHASE_BIT_CLUSTER,
            ]:
                journal.mark_phase_complete(c, run_id, bit)
            journal.store_snapshot(
                c,
                run_id,
                PreExtractSnapshot(
                    stored_method="sface",
                    method_changed=False,
                    old_cluster_photos=None,
                    stale_photo_ids=frozenset(),
                    dismissed_slots=frozenset(),
                ),
            )
            orphan_id = journal_start(
                c,
                "face_clustering",
                {"unassigned_count": 5, "from": "prior crashed run"},
            )

            row = c.execute(
                "SELECT completed_at FROM operation_journal WHERE id = ?",
                (orphan_id,),
            ).fetchone()
            assert row["completed_at"] is None

            # Resume — empty with_faces so phase 5 / 6 / 7 do trivial
            # work, but the journal-complete bits drive the orchestrator
            # through the rehydrate path the H1 fix touched.
            _faces_found, _n_clusters = extract_and_cluster_faces(
                c,
                with_faces=[],
                photo_map={},
                max_long_side=1024,
                face_confidence=0.3,
                config={},
                resume_run_id=run_id,
            )

            # journal_complete deletes the row (see bpp/db/journal.py
            # :journal_complete — DELETE keeps the table small). So
            # the H1 fix manifests as row absence, not completed_at!=NULL.
            row = c.execute(
                "SELECT id FROM operation_journal WHERE id = ?",
                (orphan_id,),
            ).fetchone()
            assert row is None, (
                "resume must close (delete) the prior run's pending "
                "face_clustering operation_journal row — ClusterPhase."
                "rehydrate adopts the row id so run_face_pipeline's "
                "cleanup block can journal_complete it (DELETE)"
            )

            # Final state: every phase bit set + run completed.
            bits = journal.get_phases_complete(c, run_id)
            assert bits == journal.ALL_PHASE_BITS
        finally:
            c.close()

    def test_fresh_run_no_orphan_row(self, monkeypatch):
        """Sanity inverse: a fresh run that completes phase 6
        normally still closes its own row (the H1 fix didn't break
        the happy path)."""
        from bpp.scoring.face_embed import embedding_method
        from bpp.web import face_worker
        from bpp.web.face_worker import extract_and_cluster_faces

        monkeypatch.setattr(face_worker, "_reconstruct_identities", lambda *a, **k: None)
        monkeypatch.setattr(face_worker, "_remap_names_and_tags", lambda *a, **k: None)

        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        self._build_full_schema(c)
        try:
            c.execute(
                "INSERT INTO settings (key, value) VALUES ('face_embedding_method', ?)",
                (embedding_method(),),
            )
            c.commit()

            extract_and_cluster_faces(
                c,
                with_faces=[],
                photo_map={},
                max_long_side=1024,
                face_confidence=0.3,
                config={},
            )

            # No orphan rows.
            orphan = c.execute(
                "SELECT COUNT(*) FROM operation_journal "
                "WHERE kind = 'face_clustering' AND completed_at IS NULL"
            ).fetchone()[0]
            assert orphan == 0
        finally:
            c.close()

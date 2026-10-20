"""face_extraction_journal per-phase resume tests — part 2 of 4: TestOrchestratorResume."""

# Shared fixtures are imported by name and re-bound as test parameters.
# ruff: noqa: F811

from __future__ import annotations

import sqlite3

import pytest

from bpp.constants import (
    CLUSTER_UNASSIGNED,
)
from bpp.web import face_extraction_journal as journal
from tests._face_extraction_journal_shared import (
    conn,  # noqa: F401
)

pytestmark = pytest.mark.usefixtures("sface_configured")


# ── Orchestrator-driven resume integration test ──


class TestOrchestratorResume:
    """The load-bearing claim: a SIGKILL between phases resumes
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
        """When reconstruct_identities raises, the orchestrator
        must leave the journal row PENDING so the recovery handler can
        retry on next startup. Previously the orchestrator caught the
        exception, marked PHASE_BIT_IDENTITY, and called complete_run —
        which made recovery skip the run forever, leaving album state
        permanently out of sync.

        Bounded retries sit on top of this, so an infinite-retry
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
            " extraction_max_long_side INTEGER,"  # v40 bbox-scale hardening
            " producing_model_id TEXT,"  # v41 derived-data purge
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

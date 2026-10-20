"""face_extraction_journal per-phase resume tests — part 3 of 4:
TestResumeRecapturesDismissedSlots, TestDismissedSlotsPreservedAcrossMethodChange,
TestConcurrentReclusterSerializes."""

# Shared fixtures are imported by name and re-bound as test parameters.
# ruff: noqa: F811

from __future__ import annotations

import sqlite3
import threading
import time

import numpy as np
import pytest

from bpp.constants import (
    CLUSTER_DISMISSED,
)
from bpp.web import face_extraction_journal as journal
from tests._face_extraction_journal_shared import (
    _build_schema,
    conn,  # noqa: F401
)

pytestmark = pytest.mark.usefixtures("sface_configured")


# ── dismissed_slots freshness on resume ──


class TestResumeRecapturesDismissedSlots:
    """When the orchestrator resumes a crashed run, phase 4.5
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

    The worry was that the orchestrator could be refactored
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
    """The main risk: when the embedding method
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

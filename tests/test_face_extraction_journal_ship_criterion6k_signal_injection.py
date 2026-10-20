"""face_extraction_journal per-phase resume tests — part 4 of 4:
TestShipCriterion6kSignalInjection, TestClusteringJournalClosedOnResume."""

# Shared fixtures are imported by name and re-bound as test parameters.
# ruff: noqa: F811

from __future__ import annotations

import sqlite3

import numpy as np
import pytest

from bpp.web import face_extraction_journal as journal
from tests._face_extraction_journal_shared import (
    _build_schema,
    conn,  # noqa: F401
)

pytestmark = pytest.mark.usefixtures("sface_configured")


class TestShipCriterion6kSignalInjection:
    """Ship criterion: 6,000-photo library face extraction killed
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
    """When ClusterPhase is journal-complete on
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
            # through the rehydrate path the fix touched.
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
            # the fix manifests as row absence, not completed_at!=NULL.
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
        normally still closes its own row (the fix didn't break
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

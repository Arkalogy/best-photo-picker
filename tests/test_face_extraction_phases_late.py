"""Per-phase unit tests for face_extraction_phases, phases 5-7
(extract, cluster, reconstruct identities). Phases 1-4 and the shared
in-memory schema live in test_face_extraction_phases.py; split for the
500-LOC cap.
"""

from __future__ import annotations

import sqlite3

import numpy as np
import pytest

from bpp.constants import (
    CLUSTER_DISMISSED,
    CLUSTER_UNASSIGNED,
)
from bpp.web import face_extraction_phases as phases
from tests.test_face_extraction_phases import _build_schema, _insert_face


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    _build_schema(c)
    yield c
    c.close()


# ── Phase 5: extract_new_embeddings (mocked extractor) ──


class TestExtractNewEmbeddings:
    """Heavy phase — test via injected callbacks. No real model load."""

    def test_no_need_extract_returns_cached_only(self, conn):
        snapshot = phases.PreExtractSnapshot(
            stored_method="sface",
            method_changed=False,
            old_cluster_photos=None,
            stale_photo_ids=frozenset(),
            dismissed_slots=frozenset(),
        )
        cached_emb = np.ones(128, dtype=np.float32)
        partition = phases.ExtractionPartition(
            cached_records=[("/a.jpg", 1, 0, cached_emb)],
            need_extract=[],
        )
        out = phases.extract_new_embeddings(
            conn,
            partition,
            snapshot,
            max_long_side=1024,
            face_confidence=0.3,
            config={},
            extract_one_fn=lambda *a, **kw: None,  # never called
            validate_bbox_fn=lambda *a, **kw: None,
            validate_embedding_fn=lambda emb, **_: True,
            progress_callback=None,
            cancellation_check=None,
            progress_total=1,
        )
        assert out.cancelled_early is False
        assert out.extracted_count == 0
        assert len(out.all_records) == 1
        assert out.all_records[0][0] == "/a.jpg"

    def test_extracted_face_appended_and_committed(self, conn):
        snapshot = phases.PreExtractSnapshot(
            stored_method=None,
            method_changed=False,
            old_cluster_photos=None,
            stale_photo_ids=frozenset(),
            dismissed_slots=frozenset(),
        )
        partition = phases.ExtractionPartition(
            cached_records=[],
            need_extract=[(0, "/a.jpg", 1)],
        )
        # Mock extractor returns one face with valid bbox + embedding.
        emb = np.ones(128, dtype=np.float32)

        def _fake_extract(filepath, *_args, **_kw):
            return [{"bbox": (5, 5, 20, 20), "embedding": emb, "quality": 0.8}]

        out = phases.extract_new_embeddings(
            conn,
            partition,
            snapshot,
            max_long_side=1024,
            face_confidence=0.3,
            config={"_face_extract_workers": 1, "_face_extract_pool": "thread"},
            extract_one_fn=_fake_extract,
            validate_bbox_fn=lambda bx, by, bw, bh: (bx, by, bw, bh),
            validate_embedding_fn=lambda e, **_: True,
            progress_callback=None,
            cancellation_check=None,
            progress_total=1,
        )
        assert out.cancelled_early is False
        assert out.extracted_count == 1
        # DB row was written.
        row = conn.execute("SELECT photo_id, face_index, quality FROM face_embeddings").fetchone()
        assert (row[0], row[1], row[2]) == (1, 0, 0.8)

    def test_dismissed_slot_restored_and_filtered_from_records(self, conn):
        # Pre-existing dismissed slot at (1, 0). Extractor produces a
        # new face at the same slot — INSERT OR REPLACE would orphan
        # the dismiss. Phase 5 must restore + filter from the records
        # list so it doesn't enter clustering.
        _insert_face(conn, photo_id=1, face_index=0, cluster_id=CLUSTER_DISMISSED)
        conn.commit()
        snapshot = phases.PreExtractSnapshot(
            stored_method=None,
            method_changed=False,
            old_cluster_photos=None,
            stale_photo_ids=frozenset(),
            dismissed_slots=frozenset({(1, 0)}),
        )
        partition = phases.ExtractionPartition(
            cached_records=[],
            need_extract=[(0, "/a.jpg", 1)],
        )
        emb = np.ones(128, dtype=np.float32)

        def _fake_extract(*_a, **_kw):
            return [{"bbox": (5, 5, 20, 20), "embedding": emb, "quality": 0.8}]

        out = phases.extract_new_embeddings(
            conn,
            partition,
            snapshot,
            max_long_side=1024,
            face_confidence=0.3,
            config={"_face_extract_workers": 1, "_face_extract_pool": "thread"},
            extract_one_fn=_fake_extract,
            validate_bbox_fn=lambda bx, by, bw, bh: (bx, by, bw, bh),
            validate_embedding_fn=lambda e, **_: True,
            progress_callback=None,
            cancellation_check=None,
            progress_total=1,
        )
        # In-memory records list: extracted face filtered out (it's dismissed).
        assert out.all_records == []
        # DB: row at (1, 0) still marked dismissed.
        cid = conn.execute(
            "SELECT cluster_id FROM face_embeddings WHERE photo_id=1 AND face_index=0"
        ).fetchone()[0]
        assert cid == CLUSTER_DISMISSED

    def test_bounded_inflight_futures_at_large_scale(self, conn):
        """The orchestrator's submit loop used to ``pool.submit(...)`` once
        per ``need_extract`` item up front, building a dict of N futures
        held simultaneously. At the documented 50K-photo import scale
        that's 50K futures + their pickled results in memory until
        ``as_completed`` drains them. The fix submits in waves bounded
        by ``n_workers * 4`` so the futures dict size stays small.

        Detection: a ThreadPoolExecutor subclass spies on ``submit()``
        and records the size of the orchestrator's futures dict at each
        submission point. The dict must never grow beyond the cap.
        """
        snapshot = phases.PreExtractSnapshot(
            stored_method=None,
            method_changed=False,
            old_cluster_photos=None,
            stale_photo_ids=frozenset(),
            dismissed_slots=frozenset(),
        )
        # 100 photos, 2 workers → cap should be 8.
        n_photos = 100
        partition = phases.ExtractionPartition(
            cached_records=[],
            need_extract=[(i, f"/p{i}.jpg", i + 1) for i in range(n_photos)],
        )

        import time
        from concurrent.futures import ThreadPoolExecutor

        emb = np.ones(128, dtype=np.float32)

        # Track the pool's pending-work queue depth at each submit().
        # ThreadPoolExecutor exposes a ``_work_queue`` we can observe.
        max_queue_depth = 0

        original_submit = ThreadPoolExecutor.submit

        def _spying_submit(self, *a, **kw):
            nonlocal max_queue_depth
            depth = self._work_queue.qsize() + 1  # +1 for this submission
            if depth > max_queue_depth:
                max_queue_depth = depth
            return original_submit(self, *a, **kw)

        def _slow_extract(*_a, **_kw):
            time.sleep(0.001)
            return [{"bbox": (5, 5, 20, 20), "embedding": emb, "quality": 0.8}]

        n_workers = 2
        import unittest.mock as _mock

        with _mock.patch.object(ThreadPoolExecutor, "submit", _spying_submit):
            phases.extract_new_embeddings(
                conn,
                partition,
                snapshot,
                max_long_side=1024,
                face_confidence=0.3,
                config={
                    "_face_extract_workers": n_workers,
                    "_face_extract_pool": "thread",
                },
                extract_one_fn=_slow_extract,
                validate_bbox_fn=lambda bx, by, bw, bh: (bx, by, bw, bh),
                validate_embedding_fn=lambda e, **_: True,
                progress_callback=None,
                cancellation_check=None,
                progress_total=n_photos,
            )

        cap = n_workers * 4
        assert max_queue_depth <= cap, (
            f"pool _work_queue depth must be bounded by ~{cap} "
            f"(n_workers={n_workers} * 4); observed peak={max_queue_depth}. "
            "Unbounded queue holds N futures' inputs in memory at 50K-photo scale."
        )
        # Every photo must still be processed.
        assert conn.execute("SELECT COUNT(*) FROM face_embeddings").fetchone()[0] == n_photos

    def test_cancellation_check_aborts_early(self, conn):
        snapshot = phases.PreExtractSnapshot(
            stored_method=None,
            method_changed=False,
            old_cluster_photos=None,
            stale_photo_ids=frozenset(),
            dismissed_slots=frozenset(),
        )
        partition = phases.ExtractionPartition(
            cached_records=[],
            need_extract=[(0, "/a.jpg", 1), (1, "/b.jpg", 2)],
        )
        # Pre-fire cancellation so as_completed returns at least one
        # done future and the check trips on the first iteration.
        out = phases.extract_new_embeddings(
            conn,
            partition,
            snapshot,
            max_long_side=1024,
            face_confidence=0.3,
            config={"_face_extract_workers": 1, "_face_extract_pool": "thread"},
            extract_one_fn=lambda *a, **kw: None,
            validate_bbox_fn=lambda *a, **kw: None,
            validate_embedding_fn=lambda e, **_: True,
            progress_callback=None,
            cancellation_check=lambda: True,  # always cancel
            progress_total=2,
        )
        assert out.cancelled_early is True


# ── Phase 6: cluster_faces (mocked assigner) ──


class TestClusterFaces:
    def test_no_records_zero_clusters(self, conn):
        n = phases.cluster_faces(
            conn,
            all_records=[],
            config={},
            assign_new_faces_fn=lambda *a, **kw: [],
            post_cluster_dedup=False,
        )
        assert n == 0

    def test_unassigned_face_gets_cluster_label_via_assigner(self, conn):
        # Pre-existing face row in DB with cluster_id = UNASSIGNED.
        emb = np.ones(128, dtype=np.float32)
        _insert_face(conn, photo_id=1, face_index=0, cluster_id=CLUSTER_UNASSIGNED)
        conn.commit()

        # Mock assigner labels every unassigned face with cluster 42.
        def _fake_assigner(_assigned, unassigned, _thresh, **_kw):
            return [42] * len(unassigned)

        n = phases.cluster_faces(
            conn,
            all_records=[("/a.jpg", 1, 0, emb)],
            config={},
            assign_new_faces_fn=_fake_assigner,
            post_cluster_dedup=False,
        )
        assert n == 1
        cid = conn.execute(
            "SELECT cluster_id FROM face_embeddings WHERE photo_id=1 AND face_index=0"
        ).fetchone()[0]
        assert cid == 42

    def test_post_cluster_dedup_removes_duplicate_cluster_rows(self, conn):
        # Photo 1 has two embeddings both assigned to cluster 5 —
        # dedup must keep one and delete the other.
        _insert_face(conn, photo_id=1, face_index=0, cluster_id=5)
        _insert_face(conn, photo_id=1, face_index=1, cluster_id=5)
        _insert_face(conn, photo_id=2, face_index=0, cluster_id=6)
        conn.commit()
        emb = np.ones(128, dtype=np.float32)
        # All records already assigned (assigner won't run).
        records = [
            ("/a.jpg", 1, 0, emb),
            ("/a.jpg", 1, 1, emb),
            ("/b.jpg", 2, 0, emb),
        ]
        n = phases.cluster_faces(
            conn,
            all_records=records,
            config={},
            assign_new_faces_fn=lambda *a, **kw: [],
            post_cluster_dedup=True,
        )
        # 2 distinct cluster_ids: 5, 6 (after dedup)
        assert n == 2
        rows = conn.execute(
            "SELECT photo_id, face_index FROM face_embeddings ORDER BY photo_id, face_index"
        ).fetchall()
        # Photo 1 has just one face left (dedup removed the duplicate).
        photo_1_rows = [r for r in rows if r[0] == 1]
        assert len(photo_1_rows) == 1


# ── Phase 7: reconstruct_identities ──


class TestReconstructIdentities:
    def test_calls_reconstruct_and_skips_remap_when_no_snapshot(self, conn):
        snapshot = phases.PreExtractSnapshot(
            stored_method=None,
            method_changed=False,
            old_cluster_photos=None,
            stale_photo_ids=frozenset(),
            dismissed_slots=frozenset(),
        )
        called = {"reconstruct": False, "remap": False}

        def _reconstruct(_conn):
            called["reconstruct"] = True

        def _remap(_conn, _old, _new):
            called["remap"] = True

        phases.reconstruct_identities(
            conn,
            snapshot,
            reconstruct_identities_fn=_reconstruct,
            remap_names_and_tags_fn=_remap,
        )
        assert called["reconstruct"] is True
        assert called["remap"] is False, (
            "remap must NOT run when no pre-extract snapshot exists; "
            "the names already correspond to the current cluster IDs"
        )

    def test_calls_remap_when_snapshot_present(self, conn):
        """Stale photos re-extracted without a model switch: the legacy
        overlap remap runs with the snapshot's old map."""
        snapshot = phases.PreExtractSnapshot(
            stored_method="sface",
            method_changed=False,
            old_cluster_photos={5: {1, 2}},
            stale_photo_ids=frozenset(),
            dismissed_slots=frozenset(),
        )
        # Put a face in the DB so _snapshot_cluster_photos has something
        # to return for the "new" side of the remap.
        _insert_face(conn, photo_id=1, face_index=0, cluster_id=10)
        conn.commit()

        observed_old = {}
        observed_new = {}

        def _reconstruct(_conn):
            pass

        def _remap(_conn, old, new):
            observed_old.update(old)
            observed_new.update(new)

        phases.reconstruct_identities(
            conn,
            snapshot,
            reconstruct_identities_fn=_reconstruct,
            remap_names_and_tags_fn=_remap,
        )
        assert observed_old == {5: {1, 2}}
        assert observed_new == {10: {1}}

    # ── Phase 7 SAVEPOINT rollback ──

    def test_no_legacy_remap_after_a_model_switch(self, conn):
        """After a switch the pending record owns the names; once it is gone
        the placement happened, and a replay of this phase (crash recovery)
        must not remap the already-moved albums again."""
        snapshot = phases.PreExtractSnapshot(
            stored_method="dlib",
            method_changed=True,
            old_cluster_photos={5: {1, 2}},
            stale_photo_ids=frozenset(),
            dismissed_slots=frozenset(),
        )
        _insert_face(conn, photo_id=1, face_index=0, cluster_id=10)
        conn.commit()
        calls: list[object] = []
        phases.reconstruct_identities(
            conn,
            snapshot,
            reconstruct_identities_fn=lambda _c: None,
            remap_names_and_tags_fn=lambda *_a: calls.append(_a),
        )
        assert calls == []


class TestReconstructIdentitiesSavepointRollback:
    """When ``reconstruct_identities_fn`` raises mid-run, any
    partial UPDATE it issued must be rolled back so the next phase-7
    invocation (recovery retry) starts from a clean state.

    Previously the partial UPDATEs sat in the implicit transaction —
    correct in practice because the subprocess teardown discards them,
    but fragile: a future refactor that runs phase 7 inline (e.g. test
    code, future plugin hook) would inherit the half-committed state.
    The explicit SAVEPOINT/ROLLBACK makes the contract local to the
    phase function and independent of caller-side cleanup.
    """

    def test_partial_failure_rolls_back_via_savepoint(self, conn):
        """Inject a reconstruct_identities_fn that issues an UPDATE
        and then raises. The SAVEPOINT must roll back the UPDATE.
        """
        # Seed an albums table — fewer columns than production, but
        # enough for an UPDATE/SELECT round-trip.
        conn.execute("CREATE TABLE albums (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO albums (id, name) VALUES (1, 'original')")
        conn.commit()

        snapshot = phases.PreExtractSnapshot(
            stored_method=None,
            method_changed=False,
            old_cluster_photos=None,
            stale_photo_ids=frozenset(),
            dismissed_slots=frozenset(),
        )

        def _partial_then_raise(c):
            c.execute("UPDATE albums SET name = 'partial' WHERE id = 1")
            raise RuntimeError("simulated mid-phase failure")

        with pytest.raises(RuntimeError, match="simulated mid-phase failure"):
            phases.reconstruct_identities(
                conn,
                snapshot,
                reconstruct_identities_fn=_partial_then_raise,
                remap_names_and_tags_fn=lambda *a, **kw: None,
            )

        # The partial UPDATE must have been rolled back.
        name = conn.execute("SELECT name FROM albums WHERE id = 1").fetchone()[0]
        assert name == "original", (
            f"phase 7 partial UPDATE must be rolled back via SAVEPOINT "
            f"after reconstruct_identities_fn raises; got name={name!r}"
        )

    def test_successful_run_commits_normally(self, conn):
        """The SAVEPOINT must NOT swallow committed UPDATEs on the
        success path — same UPDATE, no raise, change must persist.
        """
        conn.execute("CREATE TABLE albums (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO albums (id, name) VALUES (1, 'original')")
        conn.commit()

        snapshot = phases.PreExtractSnapshot(
            stored_method=None,
            method_changed=False,
            old_cluster_photos=None,
            stale_photo_ids=frozenset(),
            dismissed_slots=frozenset(),
        )

        def _commit_an_update(c):
            c.execute("UPDATE albums SET name = 'reconstructed' WHERE id = 1")
            c.commit()

        phases.reconstruct_identities(
            conn,
            snapshot,
            reconstruct_identities_fn=_commit_an_update,
            remap_names_and_tags_fn=lambda *a, **kw: None,
        )

        name = conn.execute("SELECT name FROM albums WHERE id = 1").fetchone()[0]
        assert name == "reconstructed"

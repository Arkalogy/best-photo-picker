"""P3 — per-phase unit tests for face_extraction_phases.

The orchestrator in face_worker.extract_and_cluster_faces is now a
thin sequence over 7 typed phases; this file tests each phase in
isolation against an in-memory SQLite DB. Heavy ML phases
(``extract_new_embeddings``) get mock callbacks so the test stays
synchronous and free of model loads.
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

# ── Schema helper ──


def _build_schema(conn: sqlite3.Connection) -> None:
    """Minimal schema for the phases under test.

    The real DB has migrations; for unit tests we only need the
    tables the phase functions actually touch.
    """
    conn.execute(
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
    conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    # Adaptive-threshold + hard-negative tables. cluster_faces() reads
    # both via compute_adaptive_face_threshold + get_hard_negatives;
    # empty tables → fall back to the config-provided threshold +
    # no hard negatives, which is what these unit tests want.
    conn.execute(
        "CREATE TABLE face_cluster_feedback ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " action TEXT,"
        " cluster_id_a INTEGER,"
        " cluster_id_b INTEGER,"
        " distance REAL,"
        " created_at INTEGER DEFAULT (strftime('%s','now'))"
        ")"
    )
    conn.execute(
        "CREATE TABLE face_hard_negatives ("
        " cluster_id_a INTEGER NOT NULL,"
        " cluster_id_b INTEGER NOT NULL,"
        " count INTEGER NOT NULL DEFAULT 1,"
        " created_at TEXT DEFAULT (datetime('now')),"
        " updated_at TEXT DEFAULT (datetime('now')),"
        " PRIMARY KEY (cluster_id_a, cluster_id_b)"
        ")"
    )
    conn.commit()


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    _build_schema(c)
    yield c
    c.close()


def _insert_face(
    conn: sqlite3.Connection,
    photo_id: int,
    face_index: int,
    cluster_id: int = CLUSTER_UNASSIGNED,
    quality: float | None = 0.5,
    embedding: bytes | None = None,
) -> None:
    if embedding is None:
        embedding = np.ones(128, dtype=np.float32).tobytes()
    conn.execute(
        "INSERT INTO face_embeddings"
        " (photo_id, face_index, bbox_x, bbox_y, bbox_w, bbox_h, embedding, quality, cluster_id)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (photo_id, face_index, 0, 0, 10, 10, embedding, quality, cluster_id),
    )


# ── Phase 1: reconcile_method ──


class TestReconcileMethod:
    def test_no_stored_method_writes_current_no_wipe(self, conn):
        stored, changed, snapshot = phases.reconcile_method(conn, "sface")
        assert stored is None
        assert changed is False
        assert snapshot is None
        # current method is now stored
        row = conn.execute(
            "SELECT value FROM settings WHERE key='face_embedding_method'"
        ).fetchone()
        assert row[0] == "sface"

    def test_same_method_no_wipe_no_snapshot(self, conn):
        conn.execute("INSERT INTO settings VALUES ('face_embedding_method', 'sface')")
        conn.commit()
        _insert_face(conn, photo_id=1, face_index=0, cluster_id=5)
        conn.commit()

        stored, changed, snapshot = phases.reconcile_method(conn, "sface")
        assert stored == "sface"
        assert changed is False
        assert snapshot is None
        # face survives
        assert conn.execute("SELECT COUNT(*) FROM face_embeddings").fetchone()[0] == 1

    def test_method_change_wipes_and_snapshots(self, conn):
        conn.execute("INSERT INTO settings VALUES ('face_embedding_method', 'dlib')")
        conn.commit()
        _insert_face(conn, photo_id=1, face_index=0, cluster_id=5)
        _insert_face(conn, photo_id=2, face_index=0, cluster_id=5)
        _insert_face(conn, photo_id=3, face_index=0, cluster_id=7)
        conn.commit()

        stored, changed, snapshot = phases.reconcile_method(conn, "sface")
        assert stored == "dlib"
        assert changed is True
        assert snapshot is not None
        assert snapshot[5] == {1, 2}
        assert snapshot[7] == {3}
        # all rows wiped
        assert conn.execute("SELECT COUNT(*) FROM face_embeddings").fetchone()[0] == 0
        # stored method updated
        row = conn.execute(
            "SELECT value FROM settings WHERE key='face_embedding_method'"
        ).fetchone()
        assert row[0] == "sface"


# ── Phase 2: preload_cached_embeddings ──


class TestPreloadCachedEmbeddings:
    def test_empty_with_faces_returns_empty_dicts(self, conn):
        cached, stale = phases.preload_cached_embeddings(conn, [], {})
        assert cached == {}
        assert stale == frozenset()

    def test_dismissed_rows_excluded(self, conn):
        _insert_face(conn, photo_id=1, face_index=0, cluster_id=CLUSTER_DISMISSED)
        _insert_face(conn, photo_id=1, face_index=1, cluster_id=5)
        conn.commit()
        with_faces = [{"filepath": "/a.jpg"}]
        photo_map = {"/a.jpg": 1}
        cached, _ = phases.preload_cached_embeddings(conn, with_faces, photo_map)
        # Only the non-dismissed face is in the cache.
        assert 1 in cached
        face_indices = {fi for fi, _ in cached[1]}
        assert face_indices == {1}

    def test_null_quality_rows_marked_stale(self, conn):
        _insert_face(conn, photo_id=1, face_index=0, cluster_id=5, quality=None)
        _insert_face(conn, photo_id=2, face_index=0, cluster_id=5, quality=0.5)
        conn.commit()
        with_faces = [{"filepath": "/a.jpg"}, {"filepath": "/b.jpg"}]
        photo_map = {"/a.jpg": 1, "/b.jpg": 2}
        _, stale = phases.preload_cached_embeddings(conn, with_faces, photo_map)
        assert stale == frozenset({1})

    def test_dedup_pids_in_with_faces(self, conn):
        """Same photo appearing twice in with_faces must not blow up the IN-clause."""
        _insert_face(conn, photo_id=1, face_index=0, cluster_id=5)
        conn.commit()
        with_faces = [{"filepath": "/a.jpg"}, {"filepath": "/a.jpg"}]
        photo_map = {"/a.jpg": 1}
        cached, _ = phases.preload_cached_embeddings(conn, with_faces, photo_map)
        # Single entry, not duplicated.
        assert 1 in cached
        assert len(cached[1]) == 1


# ── Phase 3: partition_cached_vs_extract ──


class TestPartitionCachedVsExtract:
    def test_cached_photo_goes_to_records(self):
        emb = np.ones(128, dtype=np.float32).tobytes()
        cached = {1: [(0, emb)]}
        partition = phases.partition_cached_vs_extract(
            with_faces=[{"filepath": "/a.jpg"}],
            photo_map={"/a.jpg": 1},
            cached_by_pid=cached,
            stale_photo_ids=frozenset(),
        )
        assert len(partition.cached_records) == 1
        fp, pid, fi, emb_arr = partition.cached_records[0]
        assert (fp, pid, fi) == ("/a.jpg", 1, 0)
        assert emb_arr.shape == (128,)
        assert partition.need_extract == []

    def test_stale_photo_goes_to_need_extract(self):
        emb = np.ones(128, dtype=np.float32).tobytes()
        cached = {1: [(0, emb)]}
        # photo 1 is also stale → must re-extract, even though cached
        partition = phases.partition_cached_vs_extract(
            with_faces=[{"filepath": "/a.jpg"}],
            photo_map={"/a.jpg": 1},
            cached_by_pid=cached,
            stale_photo_ids=frozenset({1}),
        )
        assert partition.cached_records == []
        assert partition.need_extract == [(0, "/a.jpg", 1)]

    def test_unmapped_photo_dropped(self):
        partition = phases.partition_cached_vs_extract(
            with_faces=[{"filepath": "/a.jpg"}],
            photo_map={},  # not in map
            cached_by_pid={},
            stale_photo_ids=frozenset(),
        )
        assert partition.cached_records == []
        assert partition.need_extract == []


# ── Phase 4: delete_stale_embeddings ──


class TestDeleteStaleEmbeddings:
    def test_only_stale_rows_deleted_dismissed_preserved(self, conn):
        # Photo 1: stale with one regular face + one dismissed face.
        # The DELETE must clear the regular face but preserve the
        # dismissed one (the user's intent must survive a wipe).
        _insert_face(conn, photo_id=1, face_index=0, cluster_id=5, quality=None)
        _insert_face(conn, photo_id=1, face_index=1, cluster_id=CLUSTER_DISMISSED)
        # Photo 2: not stale, untouched.
        _insert_face(conn, photo_id=2, face_index=0, cluster_id=5, quality=0.5)
        conn.commit()

        n = phases.delete_stale_embeddings(
            conn,
            need_extract=[(0, "/a.jpg", 1), (1, "/b.jpg", 2)],
            stale_photo_ids=frozenset({1}),
        )
        assert n == 1
        # Photo 1 face 0 gone; dismissed face 1 still there; photo 2 untouched.
        rows = conn.execute(
            "SELECT photo_id, face_index, cluster_id FROM face_embeddings"
            " ORDER BY photo_id, face_index"
        ).fetchall()
        assert len(rows) == 2
        assert (rows[0][0], rows[0][1], rows[0][2]) == (1, 1, CLUSTER_DISMISSED)
        assert (rows[1][0], rows[1][1]) == (2, 0)

    def test_empty_need_extract_noop(self, conn):
        _insert_face(conn, photo_id=1, face_index=0, cluster_id=5)
        conn.commit()
        n = phases.delete_stale_embeddings(conn, need_extract=[], stale_photo_ids=frozenset({1}))
        assert n == 0
        assert conn.execute("SELECT COUNT(*) FROM face_embeddings").fetchone()[0] == 1


# ── Phase 4.5: capture_dismissed_slots ──


class TestCaptureDismissedSlots:
    def test_captures_only_dismissed_slots_for_targets(self, conn):
        _insert_face(conn, photo_id=1, face_index=0, cluster_id=CLUSTER_DISMISSED)
        _insert_face(conn, photo_id=1, face_index=1, cluster_id=5)
        _insert_face(conn, photo_id=2, face_index=0, cluster_id=CLUSTER_DISMISSED)
        _insert_face(conn, photo_id=3, face_index=0, cluster_id=CLUSTER_DISMISSED)
        conn.commit()

        # Only photos 1 and 2 in the need_extract list — photo 3's
        # dismissed slot is irrelevant to this extraction run.
        slots = phases.capture_dismissed_slots(
            conn, need_extract=[(0, "/a.jpg", 1), (1, "/b.jpg", 2)]
        )
        assert slots == frozenset({(1, 0), (2, 0)})

    def test_empty_need_extract_returns_empty(self, conn):
        slots = phases.capture_dismissed_slots(conn, need_extract=[])
        assert slots == frozenset()


# ── T0.2: Phase 5 dismissed-slot crash window ──


class TestPhase5DismissedAtomicity:
    """The review's Critical R1: pre-T0.2, ``_restore_dismissed_and_filter``
    ran AFTER the per-photo commit loop. A SIGKILL between the per-photo
    INSERT-OR-REPLACE commit and the post-loop restore left the dismissed
    cluster_id silently overwritten to CLUSTER_UNASSIGNED. The fix
    restores within the same transaction as the INSERT so any commit
    that touches a photo also restores that photo's dismissed slots.

    These tests pin the per-commit invariant: every conn.commit() during
    phase 5 must leave dismissed slots in cluster_id=CLUSTER_DISMISSED.
    """

    def test_committed_photo_with_dismissed_slot_restored_atomically(self):
        """Strongest contract: every observation point at conn.commit()
        sees the dismissed slot already restored. Verified by a
        Connection subclass spy that snapshots the DB after every commit.

        Pre-T0.2 there was a commit observation where the dismissed
        slot's cluster_id was CLUSTER_UNASSIGNED — a SIGKILL there
        would lose dismissed state forever.
        """
        from bpp.web.face_extraction_phases import (
            ExtractionPartition,
            PreExtractSnapshot,
            extract_new_embeddings,
        )

        observed_post_commit_states: list[list[tuple]] = []

        class _SpyConn(sqlite3.Connection):
            def commit(self):
                super().commit()
                rows = self.execute(
                    "SELECT photo_id, face_index, cluster_id FROM face_embeddings"
                ).fetchall()
                observed_post_commit_states.append([tuple(r) for r in rows])

        conn = sqlite3.connect(":memory:", factory=_SpyConn)
        conn.row_factory = sqlite3.Row
        _build_schema(conn)

        # Pre-seed a dismissed face at (photo_id=1, face_index=0).
        _insert_face(conn, photo_id=1, face_index=0, cluster_id=CLUSTER_DISMISSED)
        conn.commit()
        # Discard the seed commit's observation — we only care about
        # commits inside extract_new_embeddings.
        observed_post_commit_states.clear()

        snapshot = PreExtractSnapshot(
            stored_method=None,
            method_changed=False,
            old_cluster_photos=None,
            stale_photo_ids=frozenset(),
            dismissed_slots=frozenset({(1, 0)}),
        )
        partition = ExtractionPartition(
            cached_records=[],
            need_extract=[(0, "/a.jpg", 1)],
        )

        emb = np.ones(128, dtype=np.float32)

        def _fake_extract(*_a, **_kw):
            return [{"bbox": (5, 5, 20, 20), "embedding": emb, "quality": 0.8}]

        extract_new_embeddings(
            conn,
            partition,
            snapshot,
            max_long_side=1024,
            face_confidence=0.3,
            config={"_face_extract_workers": 1, "_face_extract_pool": "thread"},
            extract_one_fn=_fake_extract,
            validate_bbox_fn=lambda bx, by, bw, bh: (bx, by, bw, bh),
            validate_embedding_fn=lambda e: True,
            progress_callback=None,
            cancellation_check=None,
            progress_total=1,
        )

        conn.close()

        # The invariant: at EVERY commit observation point, the dismissed
        # slot (1, 0) is in CLUSTER_DISMISSED.
        assert observed_post_commit_states, (
            "extract_new_embeddings must produce at least one commit"
        )
        for i, state in enumerate(observed_post_commit_states):
            for row in state:
                photo_id, face_index, cluster_id = row
                if (photo_id, face_index) in snapshot.dismissed_slots:
                    assert cluster_id == CLUSTER_DISMISSED, (
                        f"commit observation #{i}: dismissed slot "
                        f"({photo_id}, {face_index}) had "
                        f"cluster_id={cluster_id}, not CLUSTER_DISMISSED. "
                        f"A SIGKILL at this point would lose dismissed state."
                    )

    def test_final_state_restored_when_multiple_photos_extracted(self, conn):
        """Belt check: contract holds for multi-photo runs.

        Two photos, both dismissed before. Both re-extracted. Final state
        has both restored to CLUSTER_DISMISSED."""
        from bpp.web.face_extraction_phases import (
            ExtractionPartition,
            PreExtractSnapshot,
            extract_new_embeddings,
        )

        _insert_face(conn, photo_id=1, face_index=0, cluster_id=CLUSTER_DISMISSED)
        _insert_face(conn, photo_id=2, face_index=0, cluster_id=CLUSTER_DISMISSED)
        conn.commit()

        snapshot = PreExtractSnapshot(
            stored_method=None,
            method_changed=False,
            old_cluster_photos=None,
            stale_photo_ids=frozenset(),
            dismissed_slots=frozenset({(1, 0), (2, 0)}),
        )
        partition = ExtractionPartition(
            cached_records=[],
            need_extract=[(0, "/a.jpg", 1), (1, "/b.jpg", 2)],
        )
        emb = np.ones(128, dtype=np.float32)

        def _fake_extract(*_a, **_kw):
            return [{"bbox": (5, 5, 20, 20), "embedding": emb, "quality": 0.8}]

        extract_new_embeddings(
            conn,
            partition,
            snapshot,
            max_long_side=1024,
            face_confidence=0.3,
            config={"_face_extract_workers": 1, "_face_extract_pool": "thread"},
            extract_one_fn=_fake_extract,
            validate_bbox_fn=lambda bx, by, bw, bh: (bx, by, bw, bh),
            validate_embedding_fn=lambda e: True,
            progress_callback=None,
            cancellation_check=None,
            progress_total=2,
        )

        rows = {
            (r[0], r[1]): r[2]
            for r in conn.execute(
                "SELECT photo_id, face_index, cluster_id FROM face_embeddings"
            ).fetchall()
        }
        assert rows[(1, 0)] == CLUSTER_DISMISSED
        assert rows[(2, 0)] == CLUSTER_DISMISSED


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
            validate_embedding_fn=lambda emb: True,
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
            validate_embedding_fn=lambda e: True,
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
            validate_embedding_fn=lambda e: True,
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
                validate_embedding_fn=lambda e: True,
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
            validate_embedding_fn=lambda e: True,
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
        snapshot = phases.PreExtractSnapshot(
            stored_method="dlib",
            method_changed=True,
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


# ── T1.4: Phase 7 SAVEPOINT rollback ──


class TestReconstructIdentitiesSavepointRollback:
    """T1.4: when ``reconstruct_identities_fn`` raises mid-run, any
    partial UPDATE it issued must be rolled back so the next phase-7
    invocation (recovery retry) starts from a clean state.

    Pre-T1.4 the partial UPDATEs sat in the implicit transaction —
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

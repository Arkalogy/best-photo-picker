"""Per-phase unit tests for face_extraction_phases.

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
        " extraction_max_long_side INTEGER,"  # v40 bbox-scale hardening
        " producing_model_id TEXT,"  # v41 derived-data purge
        " user_confirmed INTEGER DEFAULT 0,"
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


# ── Phase 5 dismissed-slot crash window ──


class TestPhase5DismissedAtomicity:
    """Previously, ``_restore_dismissed_and_filter``
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

        Previously there was a commit observation where the dismissed
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
            validate_embedding_fn=lambda e, **_: True,
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
            validate_embedding_fn=lambda e, **_: True,
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

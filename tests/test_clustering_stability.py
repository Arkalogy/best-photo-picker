"""Comprehensive tests for face clustering stability, determinism, and resilience.

These tests verify that:
1. cluster_faces() is deterministic (same input -> same output)
2. Cluster GROUPINGS are stable regardless of input order
3. Re-clustering preserves logical groupings
4. The full extract_and_cluster_faces() pipeline produces stable results
5. Named clusters (person tags) survive re-clustering
6. Embedding serialization round-trips are lossless
"""

from __future__ import annotations

import os
import re
import sqlite3
import time
from unittest.mock import patch

import numpy as np

from bpp.constants import CLUSTER_DISMISSED, CLUSTER_UNASSIGNED
from bpp.db.connection import init_db
from bpp.scoring.face_cluster import cluster_faces, pick_representative

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_person_embeddings(person_seed: int, count: int, noise: float = 0.02) -> list[np.ndarray]:
    """Generate embeddings for one person, tight around a centroid.

    Returns float32 arrays — matches production write dtype (schema v35+)
    so round-tripping through tobytes() + frombuffer(dtype=np.float32)
    preserves shape (128,) instead of reinterpreting 1024 bytes as 256
    garbage floats.
    """
    rng = np.random.RandomState(person_seed)
    centroid = rng.randn(128)
    centroid = centroid / np.linalg.norm(centroid)
    return [(centroid + rng.randn(128) * noise).astype(np.float32) for _ in range(count)]


def _make_multi_person(
    n_people: int = 3,
    faces_per: int = 5,
    noise: float = 0.02,
    seed_base: int = 100,
) -> list[np.ndarray]:
    """Generate embeddings for multiple people. Returns flat list."""
    embeddings: list[np.ndarray] = []
    for p in range(n_people):
        embeddings.extend(_make_person_embeddings(seed_base + p, faces_per, noise))
    return embeddings


def _make_multi_person_with_truth(
    n_people: int = 3,
    faces_per: int = 5,
    noise: float = 0.02,
    seed_base: int = 100,
) -> tuple[list[np.ndarray], list[int]]:
    """Generate embeddings + ground truth labels."""
    embeddings: list[np.ndarray] = []
    labels: list[int] = []
    for p in range(n_people):
        embeddings.extend(_make_person_embeddings(seed_base + p, faces_per, noise))
        labels.extend([p] * faces_per)
    return embeddings, labels


def _groupings(labels: list[int]) -> set[frozenset[int]]:
    """Convert labels to grouping structure (set of frozensets of indices)."""
    groups: dict[int, set[int]] = {}
    for idx, label in enumerate(labels):
        groups.setdefault(label, set()).add(idx)
    return {frozenset(v) for v in groups.values()}


def _setup_face_db(tmp_path, n_photos: int = 10) -> tuple[sqlite3.Connection, str]:
    """Create a test DB with photo records."""
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    for i in range(n_photos):
        conn.execute(
            "INSERT INTO photos "
            "(filepath, original_filename, sha256, file_size, file_mtime) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                f"/test/photo_{i}.jpg",
                f"photo_{i}.jpg",
                f"sha_{i}",
                1000,
                1704067200.0,
            ),
        )
    conn.commit()
    return conn, db_path


def _insert_embeddings(
    conn: sqlite3.Connection,
    embs: list[np.ndarray],
    cluster_id: int = CLUSTER_UNASSIGNED,
) -> list[int]:
    """Insert embeddings into face_embeddings, return photo_ids used."""
    photo_ids = [r[0] for r in conn.execute("SELECT id FROM photos ORDER BY id").fetchall()]
    for i, emb in enumerate(embs):
        pid = photo_ids[i % len(photo_ids)]
        conn.execute(
            "INSERT OR REPLACE INTO face_embeddings "
            "(photo_id, face_index, bbox_x, bbox_y, bbox_w, bbox_h, "
            "embedding, cluster_id, quality) VALUES (?,?,?,?,?,?,?,?,?)",
            (pid, i // len(photo_ids), 0, 0, 50, 50, emb.tobytes(), cluster_id, 0.5),
        )
    conn.commit()
    return photo_ids


def _cluster_db_embeddings(conn: sqlite3.Connection, threshold: float = 0.55) -> list[int]:
    """Load embeddings from DB, cluster, and update. Return labels."""
    rows = conn.execute(
        f"SELECT id, embedding FROM face_embeddings WHERE cluster_id != {CLUSTER_DISMISSED}"
    ).fetchall()
    fe_ids = [r[0] for r in rows]
    db_embs = [np.frombuffer(r[1], dtype=np.float32) for r in rows]
    labels = cluster_faces(db_embs, threshold=threshold)
    conn.executemany(
        "UPDATE face_embeddings SET cluster_id=? WHERE id=?",
        zip(labels, fe_ids, strict=True),
    )
    conn.commit()
    return labels


_FETCH_ASSIGNMENTS = (
    "SELECT photo_id, face_index, cluster_id FROM face_embeddings ORDER BY photo_id, face_index"
)


def _sandbox_extract_one(
    filepath,
    max_long_side,
    min_confidence=0.2,
    embedding_confidence=0.65,
    min_embedding_quality=0.25,
    method=None,
):
    """Thread-safe extraction: deterministic embeddings per filepath + random delay.

    Designed for real ThreadPoolExecutor tests. The random delay forces
    as_completed to return futures in non-deterministic order, exposing
    the ordering bug in extract_and_cluster_faces.

    Photos are grouped into "people" (5 photos per person) so clustering
    produces meaningful multi-person groupings.
    """
    time.sleep(os.urandom(1)[0] / 1000.0)  # 0-255ms random delay
    m = re.search(r"photo_(\d+)", filepath)
    if not m:
        return []
    idx = int(m.group(1))
    person_id = idx // 5
    rng_person = np.random.RandomState(1000 + person_id)
    centroid = rng_person.randn(128)
    centroid = centroid / np.linalg.norm(centroid)
    rng_photo = np.random.RandomState(2000 + idx)
    emb = (centroid + rng_photo.randn(128) * 0.02).astype(np.float32)
    return [{"bbox": (10, 10, 50, 50), "embedding": emb}]


# ===========================================================================
# 1. DETERMINISM — same input, same output every time
# ===========================================================================


class TestClusterDeterminism:
    """Verify cluster_faces() is perfectly deterministic."""

    def test_repeated_calls_identical_output(self):
        rng = np.random.RandomState(42)
        embeddings = [rng.randn(128) for _ in range(30)]
        reference = cluster_faces(embeddings, threshold=0.6)
        for _ in range(10):
            assert cluster_faces(embeddings, threshold=0.6) == reference

    def test_determinism_with_tight_clusters(self):
        embs = _make_multi_person(n_people=5, faces_per=8, noise=0.01)
        reference = cluster_faces(embs, threshold=0.55)
        for _ in range(10):
            assert cluster_faces(embs, threshold=0.55) == reference

    def test_determinism_across_thresholds(self):
        embs = _make_multi_person(n_people=3, faces_per=5)
        for threshold in [0.3, 0.45, 0.55, 0.6, 0.8, 1.0]:
            ref = cluster_faces(embs, threshold=threshold)
            for _ in range(5):
                assert cluster_faces(embs, threshold=threshold) == ref

    def test_determinism_with_duplicates(self):
        rng = np.random.RandomState(99)
        base = rng.randn(128)
        embs = [base.copy() for _ in range(10)]
        ref = cluster_faces(embs, threshold=0.6)
        for _ in range(10):
            assert cluster_faces(embs, threshold=0.6) == ref


# ===========================================================================
# 2. ORDER INDEPENDENCE — different orders -> same groupings
# ===========================================================================


class TestClusterOrderIndependence:
    """Verify grouping structure is independent of input order."""

    def test_shuffle_preserves_groupings(self):
        embs = _make_multi_person(n_people=4, faces_per=6, noise=0.01)
        ref_groups = _groupings(cluster_faces(embs, threshold=0.55))

        rng = np.random.RandomState(7)
        for _ in range(20):
            perm = rng.permutation(len(embs))
            shuffled = [embs[i] for i in perm]
            shuf_labels = cluster_faces(shuffled, threshold=0.55)
            original_labels = [0] * len(embs)
            for new_idx, orig_idx in enumerate(perm):
                original_labels[orig_idx] = shuf_labels[new_idx]
            assert _groupings(original_labels) == ref_groups

    def test_reverse_order_preserves_groupings(self):
        embs = _make_multi_person(n_people=3, faces_per=5, noise=0.01)
        fwd = cluster_faces(embs, threshold=0.55)
        rev_labels = cluster_faces(list(reversed(embs)), threshold=0.55)
        n = len(embs)
        original_labels = [rev_labels[n - 1 - i] for i in range(n)]
        assert _groupings(fwd) == _groupings(original_labels)

    def test_interleaved_order_preserves_groupings(self):
        n_people, faces_per = 3, 6
        embs = _make_multi_person(n_people, faces_per, noise=0.01)
        interleaved_indices = []
        for fi in range(faces_per):
            for pi in range(n_people):
                interleaved_indices.append(pi * faces_per + fi)

        int_labels = cluster_faces([embs[i] for i in interleaved_indices], threshold=0.55)
        original_labels = [0] * len(embs)
        for new_idx, orig_idx in enumerate(interleaved_indices):
            original_labels[orig_idx] = int_labels[new_idx]

        ref_labels = cluster_faces(embs, threshold=0.55)
        assert _groupings(original_labels) == _groupings(ref_labels)


# ===========================================================================
# 3. GROUPING CORRECTNESS
# ===========================================================================


class TestClusterCorrectness:
    """Verify clusters match expected groupings for synthetic data."""

    def test_well_separated_people(self):
        embs, truth = _make_multi_person_with_truth(n_people=5, faces_per=10, noise=0.01)
        assert _groupings(cluster_faces(embs, threshold=0.55)) == _groupings(truth)

    def test_single_person(self):
        embs = _make_person_embeddings(42, count=20, noise=0.01)
        assert len(set(cluster_faces(embs, threshold=0.55))) == 1

    def test_many_people_small_groups(self):
        embs, truth = _make_multi_person_with_truth(n_people=20, faces_per=3, noise=0.01)
        assert _groupings(cluster_faces(embs, threshold=0.55)) == _groupings(truth)

    def test_threshold_controls_granularity(self):
        embs = _make_multi_person(n_people=3, faces_per=10, noise=0.03)
        tight = cluster_faces(embs, threshold=0.3)
        loose = cluster_faces(embs, threshold=0.8)
        assert len(set(tight)) >= len(set(loose))


# ===========================================================================
# 4. EMBEDDING ROUND-TRIP
# ===========================================================================


class TestEmbeddingRoundTrip:
    """Verify embeddings survive DB storage/retrieval without drift."""

    def test_tobytes_frombuffer_lossless(self):
        rng = np.random.RandomState(0)
        for _ in range(100):
            emb = rng.randn(128).astype(np.float32)
            recovered = np.frombuffer(emb.tobytes(), dtype=np.float32)
            np.testing.assert_array_equal(emb, recovered)

    def test_round_trip_through_sqlite(self, tmp_path):
        conn, _ = _setup_face_db(tmp_path, n_photos=1)
        rng = np.random.RandomState(1)
        emb = rng.randn(128).astype(np.float32)
        photo_id = conn.execute("SELECT id FROM photos LIMIT 1").fetchone()[0]
        conn.execute(
            "INSERT INTO face_embeddings (photo_id, face_index, "
            "bbox_x, bbox_y, bbox_w, bbox_h, embedding) "
            "VALUES (?,?,?,?,?,?,?)",
            (photo_id, 0, 10, 20, 50, 50, emb.tobytes()),
        )
        conn.commit()
        row = conn.execute(
            "SELECT embedding FROM face_embeddings WHERE photo_id=?",
            (photo_id,),
        ).fetchone()
        np.testing.assert_array_equal(emb, np.frombuffer(row[0], dtype=np.float32))
        conn.close()

    def test_clustering_identical_after_round_trip(self, tmp_path):
        conn, _ = _setup_face_db(tmp_path, n_photos=20)
        embs = _make_multi_person(n_people=4, faces_per=5, noise=0.01)
        photo_ids = [r[0] for r in conn.execute("SELECT id FROM photos ORDER BY id").fetchall()]
        for i, emb in enumerate(embs):
            pid = photo_ids[i % len(photo_ids)]
            conn.execute(
                "INSERT OR REPLACE INTO face_embeddings "
                "(photo_id, face_index, bbox_x, bbox_y, bbox_w, bbox_h, "
                "embedding) VALUES (?,?,?,?,?,?,?)",
                (pid, i // len(photo_ids), 0, 0, 50, 50, emb.tobytes()),
            )
        conn.commit()
        rows = conn.execute("SELECT embedding FROM face_embeddings ORDER BY id").fetchall()
        db_embs = [np.frombuffer(r[0], dtype=np.float32) for r in rows]
        assert cluster_faces(db_embs, threshold=0.55) == cluster_faces(embs, threshold=0.55)
        conn.close()


# ===========================================================================
# 5. RECLUSTER STABILITY
# ===========================================================================


class TestReclusterStability:
    """Simulate what happens when the recluster endpoint is called."""

    def test_recluster_same_threshold_same_result(self, tmp_path):
        conn, _ = _setup_face_db(tmp_path, n_photos=20)
        embs = _make_multi_person(n_people=5, faces_per=8, noise=0.01)
        _insert_embeddings(conn, embs)
        labels1 = _cluster_db_embeddings(conn, threshold=0.55)
        labels2 = _cluster_db_embeddings(conn, threshold=0.55)
        assert labels1 == labels2
        conn.close()

    def test_recluster_preserves_groupings_after_dismiss(self, tmp_path):
        conn, _ = _setup_face_db(tmp_path, n_photos=20)
        embs = _make_multi_person(n_people=4, faces_per=5, noise=0.01)
        _insert_embeddings(conn, embs)
        labels = _cluster_db_embeddings(conn, threshold=0.55)

        # Dismiss one cluster
        cid_to_dismiss = labels[0]
        conn.execute(
            "UPDATE face_embeddings SET cluster_id=? WHERE cluster_id=?",
            (CLUSTER_DISMISSED, cid_to_dismiss),
        )
        conn.commit()

        remaining = conn.execute(
            f"SELECT id, cluster_id FROM face_embeddings WHERE cluster_id != {CLUSTER_DISMISSED}"
        ).fetchall()
        old_groupings = _groupings([r[1] for r in remaining])

        # Re-cluster remaining
        _cluster_db_embeddings(conn, threshold=0.55)

        new_remaining = conn.execute(
            f"SELECT id, cluster_id FROM face_embeddings WHERE cluster_id != {CLUSTER_DISMISSED}"
        ).fetchall()
        assert _groupings([r[1] for r in new_remaining]) == old_groupings
        conn.close()


# ===========================================================================
# 6. PIPELINE INTEGRATION
# ===========================================================================


class TestExtractAndClusterPipeline:
    """Integration tests for extract_and_cluster_faces()."""

    @staticmethod
    def _make_pipeline_fixture(tmp_path, n_people=3, faces_per=4):
        """Set up DB + analysis + mocked embeddings."""
        conn, _db_path = _setup_face_db(tmp_path, n_photos=n_people * faces_per)
        photos = conn.execute("SELECT id, filepath FROM photos ORDER BY id").fetchall()
        embs = _make_multi_person(n_people, faces_per, noise=0.01)
        photo_map = {fp: pid for pid, fp in photos}
        analysis = [{"filepath": fp, "face_count": 1, "id": pid} for pid, fp in photos]
        embs_by_fp = {}
        for i, (_pid, fp) in enumerate(photos):
            if i < len(embs):
                embs_by_fp[fp] = [embs[i]]

        def mock_extract(
            filepath,
            max_long_side,
            min_confidence=0.2,
            embedding_confidence=0.65,
            min_embedding_quality=0.25,
        ):
            es = embs_by_fp.get(filepath, [])
            return [{"bbox": (10, 10, 50, 50), "embedding": e, "quality": 0.8} for e in es]

        return conn, analysis, photo_map, embs_by_fp, mock_extract

    def test_pipeline_determinism(self, tmp_path):
        from bpp.web.face_worker import extract_and_cluster_faces

        conn, analysis, photo_map, _, mock_extract = self._make_pipeline_fixture(tmp_path)
        config = {"face_cluster_threshold": 0.55}

        with patch("bpp.web.face_worker._extract_one", side_effect=mock_extract):
            f1, nc1 = extract_and_cluster_faces(conn, analysis, photo_map, 1024, 0.2, config)
        labels1 = conn.execute(_FETCH_ASSIGNMENTS).fetchall()

        # Reset cluster IDs, keep cached embeddings
        conn.execute(f"UPDATE face_embeddings SET cluster_id = {CLUSTER_UNASSIGNED}")
        conn.commit()
        f2, nc2 = extract_and_cluster_faces(conn, analysis, photo_map, 1024, 0.2, config)
        labels2 = conn.execute(_FETCH_ASSIGNMENTS).fetchall()

        assert (f1, nc1) == (f2, nc2)
        assert labels1 == labels2
        conn.close()

    def test_pipeline_different_as_completed_order(self, tmp_path):
        """Simulate the worker pool returning futures in different orders.

        T4: the orchestrator switched from ``as_completed`` to
        ``wait(FIRST_COMPLETED)`` for bounded in-flight submission.
        We patch ``wait`` instead and shuffle the completion order in
        each return wave to prove cluster assignments are still stable.
        """
        from concurrent.futures import wait as real_wait

        from bpp.web.face_worker import extract_and_cluster_faces

        n_people, faces_per = 3, 4
        ref_assignments = None

        for run, should_reverse in enumerate([False, True, False]):
            run_dir = tmp_path / f"order_run_{run}"
            run_dir.mkdir()
            conn, analysis, photo_map, _, mock_extract = self._make_pipeline_fixture(
                run_dir, n_people, faces_per
            )

            _should_rev = should_reverse  # bind for closure

            def patched_wait(futures, _rev=_should_rev, **kw):
                done, not_done = real_wait(futures, **kw)
                if _rev:
                    # ``wait`` returns sets; convert to a list whose
                    # iteration order is reversed so the orchestrator's
                    # ``for future in done`` loop processes in the
                    # opposite order each odd run.
                    done = list(done)
                    done.reverse()
                return done, not_done

            config = {"face_cluster_threshold": 0.55}
            with (
                patch(
                    "bpp.web.face_worker._extract_one",
                    side_effect=mock_extract,
                ),
                patch(
                    # T4: orchestrator now uses wait + FIRST_COMPLETED
                    # for bounded in-flight submission. Phase 5 was
                    # extracted into its own module during the 500-LOC
                    # split — the wait import lives in face_extraction_phase5.
                    "bpp.web.face_extraction_phase5.wait",
                    side_effect=patched_wait,
                ),
            ):
                extract_and_cluster_faces(conn, analysis, photo_map, 1024, 0.2, config)

            assignments = conn.execute(_FETCH_ASSIGNMENTS).fetchall()
            if ref_assignments is None:
                ref_assignments = assignments
            else:
                assert [r[2] for r in assignments] == [r[2] for r in ref_assignments], (
                    f"Run {run} (reverse={should_reverse}): cluster assignments differ!"
                )
            conn.close()

    def test_pipeline_cached_vs_fresh_same_groupings(self, tmp_path):
        """Half cached + half fresh must produce same groupings as all-fresh."""
        from bpp.web.face_worker import extract_and_cluster_faces

        conn, analysis, photo_map, _, mock_extract = self._make_pipeline_fixture(tmp_path)
        config = {"face_cluster_threshold": 0.55}

        with patch("bpp.web.face_worker._extract_one", side_effect=mock_extract):
            extract_and_cluster_faces(conn, analysis, photo_map, 1024, 0.2, config)
        run1 = conn.execute(_FETCH_ASSIGNMENTS).fetchall()
        run1_groupings = _groupings([r[2] for r in run1])

        # Delete half to force partial re-extraction
        half = len(analysis) // 2
        for a in analysis[:half]:
            pid = photo_map[a["filepath"]]
            conn.execute("DELETE FROM face_embeddings WHERE photo_id=?", (pid,))
        conn.commit()

        with patch("bpp.web.face_worker._extract_one", side_effect=mock_extract):
            extract_and_cluster_faces(conn, analysis, photo_map, 1024, 0.2, config)
        run2 = conn.execute(_FETCH_ASSIGNMENTS).fetchall()
        assert _groupings([r[2] for r in run2]) == run1_groupings
        conn.close()


# ===========================================================================
# 7. PERSON TAG SURVIVAL
# ===========================================================================


class TestPersonTagSurvival:
    """Verify photo_person_tags remain valid after re-clustering."""

    def test_tags_valid_after_same_threshold_recluster(self, tmp_path):
        conn, _ = _setup_face_db(tmp_path, n_photos=20)
        embs = _make_multi_person(n_people=4, faces_per=5, noise=0.01)
        photo_ids = _insert_embeddings(conn, embs)
        labels = _cluster_db_embeddings(conn, threshold=0.55)

        # Name two clusters
        for cid in list(set(labels))[:2]:
            conn.execute(
                "INSERT OR IGNORE INTO photo_person_tags "
                "(photo_id, cluster_id, created_at) "
                "VALUES (?, ?, datetime('now'))",
                (photo_ids[0], cid),
            )
        conn.commit()

        # Recluster + cleanup
        _cluster_db_embeddings(conn, threshold=0.55)
        conn.execute(
            "DELETE FROM photo_person_tags WHERE cluster_id NOT IN "
            "(SELECT DISTINCT cluster_id FROM face_embeddings "
            "WHERE cluster_id >= 0)"
        )
        conn.commit()

        orphans = conn.execute(
            "SELECT cluster_id FROM photo_person_tags "
            "WHERE cluster_id NOT IN "
            "(SELECT DISTINCT cluster_id FROM face_embeddings "
            "WHERE cluster_id >= 0)"
        ).fetchall()
        assert orphans == []
        conn.close()

    def test_threshold_change_cleans_orphaned_tags(self, tmp_path):
        conn, _ = _setup_face_db(tmp_path, n_photos=20)
        embs = _make_multi_person(n_people=4, faces_per=5, noise=0.01)
        photo_ids = _insert_embeddings(conn, embs)
        labels = _cluster_db_embeddings(conn, threshold=0.55)

        # Tag all clusters
        for cid in set(labels):
            conn.execute(
                "INSERT OR IGNORE INTO photo_person_tags "
                "(photo_id, cluster_id, created_at) "
                "VALUES (?, ?, datetime('now'))",
                (photo_ids[0], cid),
            )
        conn.commit()
        tags_before = conn.execute("SELECT COUNT(*) FROM photo_person_tags").fetchone()[0]

        # Merge all into 1 cluster via high threshold
        rows = conn.execute("SELECT id, embedding FROM face_embeddings").fetchall()
        fe_ids = [r[0] for r in rows]
        db_embs = [np.frombuffer(r[1], dtype=np.float32) for r in rows]
        labels2 = cluster_faces(db_embs, threshold=100.0)
        conn.executemany(
            "UPDATE face_embeddings SET cluster_id=? WHERE id=?",
            zip(labels2, fe_ids, strict=True),
        )
        conn.execute(
            "DELETE FROM photo_person_tags WHERE cluster_id NOT IN "
            "(SELECT DISTINCT cluster_id FROM face_embeddings "
            "WHERE cluster_id >= 0)"
        )
        conn.commit()

        tags_after = conn.execute("SELECT COUNT(*) FROM photo_person_tags").fetchone()[0]
        assert tags_after <= tags_before
        assert tags_after <= 1
        conn.close()


# ===========================================================================
# 8. EDGE CASES
# ===========================================================================


class TestClusterEdgeCases:
    def test_two_embeddings(self):
        rng = np.random.RandomState(0)
        assert len(cluster_faces([rng.randn(128) for _ in range(2)], threshold=0.6)) == 2

    def test_all_identical_many(self):
        embs = [np.ones(128) * 0.5 for _ in range(100)]
        labels = cluster_faces(embs, threshold=0.6)
        assert len(set(labels)) == 1

    def test_very_noisy_embeddings(self):
        rng = np.random.RandomState(123)
        assert len(cluster_faces([rng.randn(128) * 10 for _ in range(50)], threshold=0.6)) == 50

    def test_near_threshold_boundary(self):
        e1, e2 = np.zeros(128), np.zeros(128)
        e2[0] = 0.55
        labels = cluster_faces([e1, e2], threshold=0.55)
        assert labels[0] == labels[1]

    def test_slightly_above_threshold(self):
        e1, e2 = np.zeros(128), np.zeros(128)
        e2[0] = 0.56
        labels = cluster_faces([e1, e2], threshold=0.55)
        assert labels[0] != labels[1]

    def test_zero_embeddings(self):
        labels = cluster_faces([np.zeros(128) for _ in range(5)], threshold=0.6)
        assert len(set(labels)) == 1

    def test_very_large_embeddings(self):
        rng = np.random.RandomState(0)
        assert len(cluster_faces([rng.randn(128) * 1e6 for _ in range(10)], threshold=1e7)) == 10

    def test_pick_representative_stable(self):
        rng = np.random.RandomState(42)
        embs = [rng.randn(128) for _ in range(20)]
        ref = pick_representative(embs)
        for _ in range(10):
            assert pick_representative(embs) == ref


# ===========================================================================
# 9. as_completed ORDER BUG — the actual root cause
# ===========================================================================


class TestAsCompletedOrderBug:
    """Demonstrate and fix the as_completed ordering vulnerability.

    ROOT CAUSE: fcluster assigns integer labels based on the order items
    appear in the linkage tree, which depends on input array order.
    Same groupings, but different label integers — breaking person tags
    that reference cluster_id.
    """

    def test_input_order_changes_label_integers(self):
        """SMOKING GUN: same embeddings in different order → different cluster IDs.

        This proves that without sorting, as_completed() ordering changes
        cluster_id values between runs, breaking person tags.

        Uses noise=0.05 which produces realistic intra-person distances
        (~0.55 L2) comparable to the clustering threshold.
        """
        embs = _make_multi_person(n_people=3, faces_per=5, noise=0.05)
        rng = np.random.RandomState(42)

        label_sequences = set()
        for _ in range(30):
            perm = rng.permutation(len(embs))
            shuffled = [embs[i] for i in perm]
            labels = cluster_faces(shuffled, threshold=0.55)
            # Map back to original indices
            mapped = tuple(labels[list(perm).index(i)] for i in range(len(embs)))
            label_sequences.add(mapped)

        # Multiple distinct label sequences proves label instability
        assert len(label_sequences) > 1, (
            "Expected multiple label sequences from shuffled input, "
            "but fcluster was unexpectedly order-invariant for labels"
        )

    def test_groupings_always_stable_despite_label_instability(self):
        """Groupings (which faces are together) are stable regardless of order."""
        embs = _make_multi_person(n_people=3, faces_per=5, noise=0.05)
        ref_groups = _groupings(cluster_faces(embs, threshold=0.55))

        rng = np.random.RandomState(42)
        for _ in range(30):
            perm = rng.permutation(len(embs))
            shuffled = [embs[i] for i in perm]
            labels = cluster_faces(shuffled, threshold=0.55)
            mapped = [0] * len(embs)
            for new_idx, orig_idx in enumerate(perm):
                mapped[orig_idx] = labels[new_idx]
            assert _groupings(mapped) == ref_groups

    def test_sorted_input_makes_ids_stable(self):
        """THE FIX: sorting by (photo_id, face_index) before clustering
        makes label integers deterministic regardless of as_completed order.
        """
        embs = _make_multi_person(n_people=3, faces_per=5, noise=0.01)
        records = [(i, 0, embs[i]) for i in range(len(embs))]

        rng = np.random.RandomState(42)
        ref_map = None
        for _ in range(20):
            shuffled = list(records)
            rng.shuffle(shuffled)
            sorted_recs = sorted(shuffled, key=lambda r: (r[0], r[1]))
            labels = cluster_faces([r[2] for r in sorted_recs], threshold=0.55)
            label_map = {rec[0]: lbl for rec, lbl in zip(sorted_recs, labels, strict=True)}
            if ref_map is None:
                ref_map = label_map
            else:
                assert label_map == ref_map

    def test_shuffled_input_preserves_clustering(self):
        """Verify scipy agglomerative clustering is order-invariant.

        For well-separated clusters, both IDs and groupings should be
        identical regardless of input order when mapped back to original indices.
        """
        embs = _make_multi_person(n_people=5, faces_per=6, noise=0.01)
        ref_labels = cluster_faces(embs, threshold=0.55)
        ref_groups = _groupings(ref_labels)

        rng = np.random.RandomState(99)
        for _ in range(20):
            perm = rng.permutation(len(embs))
            shuffled = [embs[i] for i in perm]
            shuf_labels = cluster_faces(shuffled, threshold=0.55)
            # Map back to original indices
            mapped = [0] * len(embs)
            for new_idx, orig_idx in enumerate(perm):
                mapped[orig_idx] = shuf_labels[new_idx]
            assert mapped == ref_labels
            assert _groupings(mapped) == ref_groups


# ===========================================================================
# 10. RECLUSTER ENDPOINT — ORDER BY fix
# ===========================================================================


class TestReclusterOrderBy:
    """Verify recluster produces stable IDs regardless of DB row order."""

    def test_recluster_stable_across_insertion_orders(self, tmp_path):
        """Simulate recluster with different DB insertion orders.

        Without ORDER BY in the SELECT, SQLite returns rows in rowid order.
        Different insertion orders → different rowid orders → different labels.
        The ORDER BY photo_id, face_index fix makes it deterministic.

        Uses noise=0.05 for realistic intra-person distances where label
        instability would occur without the ORDER BY fix.
        """
        embs = _make_multi_person(n_people=3, faces_per=5, noise=0.05)
        n = len(embs)
        ref_assignment = None

        rng = np.random.RandomState(42)
        for run in range(5):
            run_dir = tmp_path / f"recluster_{run}"
            run_dir.mkdir()
            conn, _ = _setup_face_db(run_dir, n_photos=n)
            photo_ids = [r[0] for r in conn.execute("SELECT id FROM photos ORDER BY id").fetchall()]

            # Insert in random order (simulating as_completed ordering)
            perm = rng.permutation(n)
            for new_idx in perm:
                pid = photo_ids[new_idx]
                conn.execute(
                    "INSERT INTO face_embeddings "
                    "(photo_id, face_index, bbox_x, bbox_y, bbox_w, bbox_h, "
                    "embedding, cluster_id) VALUES (?,?,?,?,?,?,?,?)",
                    (pid, 0, 0, 0, 50, 50, embs[new_idx].tobytes(), CLUSTER_UNASSIGNED),
                )
            conn.commit()

            # Recluster with ORDER BY (simulating fixed recluster endpoint)
            rows = conn.execute(
                "SELECT id, photo_id, face_index, embedding "
                f"FROM face_embeddings WHERE cluster_id != {CLUSTER_DISMISSED} "
                "ORDER BY photo_id, face_index"
            ).fetchall()
            fe_ids = [r[0] for r in rows]
            db_embs = [np.frombuffer(r[3], dtype=np.float32) for r in rows]
            labels = cluster_faces(db_embs, threshold=0.55)
            conn.executemany(
                "UPDATE face_embeddings SET cluster_id=? WHERE id=?",
                zip(labels, fe_ids, strict=True),
            )
            conn.commit()

            # Check assignments in canonical order
            assignments = conn.execute(_FETCH_ASSIGNMENTS).fetchall()
            if ref_assignment is None:
                ref_assignment = [r[2] for r in assignments]
            else:
                assert [r[2] for r in assignments] == ref_assignment, (
                    f"Run {run}: recluster labels differ despite ORDER BY fix"
                )
            conn.close()

    def test_recluster_without_order_by_is_unstable(self, tmp_path):
        """Prove that WITHOUT ORDER BY, recluster labels are unstable.

        Uses noise=0.05 for realistic intra-person distances that trigger
        label instability when input order changes.
        """
        embs = _make_multi_person(n_people=3, faces_per=5, noise=0.05)
        n = len(embs)
        all_assignments = []

        rng = np.random.RandomState(42)
        for run in range(10):
            run_dir = tmp_path / f"no_order_{run}"
            run_dir.mkdir()
            conn, _ = _setup_face_db(run_dir, n_photos=n)
            photo_ids = [r[0] for r in conn.execute("SELECT id FROM photos ORDER BY id").fetchall()]

            # Insert in random order (simulating as_completed ordering)
            perm = rng.permutation(n)
            for new_idx in perm:
                pid = photo_ids[new_idx]
                conn.execute(
                    "INSERT INTO face_embeddings "
                    "(photo_id, face_index, bbox_x, bbox_y, bbox_w, bbox_h, "
                    "embedding, cluster_id) VALUES (?,?,?,?,?,?,?,?)",
                    (pid, 0, 0, 0, 50, 50, embs[new_idx].tobytes(), CLUSTER_UNASSIGNED),
                )
            conn.commit()

            # Recluster WITHOUT ORDER BY (old buggy behavior)
            rows = conn.execute(
                f"SELECT id, embedding FROM face_embeddings WHERE cluster_id != {CLUSTER_DISMISSED}"
            ).fetchall()
            fe_ids = [r[0] for r in rows]
            db_embs = [np.frombuffer(r[1], dtype=np.float32) for r in rows]
            labels = cluster_faces(db_embs, threshold=0.55)
            conn.executemany(
                "UPDATE face_embeddings SET cluster_id=? WHERE id=?",
                zip(labels, fe_ids, strict=True),
            )
            conn.commit()

            assignments = conn.execute(_FETCH_ASSIGNMENTS).fetchall()
            all_assignments.append(tuple(r[2] for r in assignments))
            conn.close()

        # Without ORDER BY, some runs should produce different labels
        unique = len(set(all_assignments))
        assert unique > 1, "Expected label instability without ORDER BY, but all runs matched"


# ===========================================================================
# 11. REGRESSION — sort fix verification
# ===========================================================================


class TestSortFixRegression:
    """Verify sort fix holds in extract_and_cluster_faces."""

    def test_stable_across_fresh_dbs(self, tmp_path):
        from bpp.web.face_worker import extract_and_cluster_faces

        embs = _make_multi_person(n_people=3, faces_per=4, noise=0.01)
        ref_data = None

        for run in range(3):
            run_dir = tmp_path / f"run_{run}"
            run_dir.mkdir()
            conn, _ = _setup_face_db(run_dir, n_photos=12)
            photos = conn.execute("SELECT id, filepath FROM photos ORDER BY id").fetchall()
            photo_map = {fp: pid for pid, fp in photos}
            analysis = [{"filepath": fp, "face_count": 1, "id": pid} for pid, fp in photos]
            embs_by_fp = {fp: [embs[i]] for i, (_pid, fp) in enumerate(photos)}

            # Bind embs_by_fp for this iteration
            _ebf = embs_by_fp

            def mock_extract(
                filepath,
                max_long_side,
                min_confidence=0.2,
                embedding_confidence=0.65,
                min_embedding_quality=0.25,
                _m=_ebf,
            ):
                es = _m.get(filepath, [])
                return [{"bbox": (10, 10, 50, 50), "embedding": e} for e in es]

            config = {"face_cluster_threshold": 0.55}
            with patch(
                "bpp.web.face_worker._extract_one",
                side_effect=mock_extract,
            ):
                extract_and_cluster_faces(conn, analysis, photo_map, 1024, 0.2, config)

            data = conn.execute(_FETCH_ASSIGNMENTS).fetchall()
            if ref_data is None:
                ref_data = data
            else:
                assert [r[2] for r in data] == [r[2] for r in ref_data], (
                    f"Run {run}: cluster assignments differ!"
                )
            conn.close()


# ===========================================================================
# 11. SANDBOX — real ThreadPoolExecutor with artificial delays
# ===========================================================================


class TestSandboxRealThreadPool:
    """True sandbox: real ThreadPoolExecutor, random delays, no mocks.

    This test catches the as_completed ordering bug because:
    - Each run starts with an empty DB (no cached embeddings)
    - _sandbox_extract_one runs in real threads with random delays
    - as_completed returns futures in completion order (affected by delays)
    - Without the sort fix, different runs produce different all_records order
    - The sort fix normalizes order before clustering, making results stable
    """

    def test_real_concurrent_extraction_stable_clusters(self, tmp_path, monkeypatch):
        """Multiple runs with real concurrency must produce identical cluster IDs."""
        import bpp.web.face_worker as fw
        from bpp.web.face_worker import extract_and_cluster_faces

        monkeypatch.setattr(fw, "_extract_one", _sandbox_extract_one)

        n_photos = 15  # 3 people x 5 photos each
        n_runs = 5
        ref_assignments = None

        for run in range(n_runs):
            run_dir = tmp_path / f"sandbox_{run}"
            run_dir.mkdir()
            conn, _ = _setup_face_db(run_dir, n_photos=n_photos)
            photos = conn.execute("SELECT id, filepath FROM photos ORDER BY id").fetchall()
            photo_map = {fp: pid for pid, fp in photos}
            analysis = [{"filepath": fp, "face_count": 1, "id": pid} for pid, fp in photos]
            config = {"face_cluster_threshold": 0.55}

            extract_and_cluster_faces(conn, analysis, photo_map, 1024, 0.2, config)

            assignments = conn.execute(_FETCH_ASSIGNMENTS).fetchall()
            if ref_assignments is None:
                ref_assignments = assignments
                assert len(assignments) == n_photos, (
                    f"Expected {n_photos} assignments, got {len(assignments)}"
                )
            else:
                assert [r[2] for r in assignments] == [r[2] for r in ref_assignments], (
                    f"Run {run}: cluster IDs differ from run 0"
                )
            conn.close()

    def test_concurrent_groupings_match_sequential(self, tmp_path, monkeypatch):
        """Concurrent extraction must produce same groupings as sequential."""
        import bpp.web.face_worker as fw
        from bpp.web.face_worker import extract_and_cluster_faces

        monkeypatch.setattr(fw, "_extract_one", _sandbox_extract_one)

        n_photos = 15
        conn, _ = _setup_face_db(tmp_path, n_photos=n_photos)
        photos = conn.execute("SELECT id, filepath FROM photos ORDER BY id").fetchall()
        photo_map = {fp: pid for pid, fp in photos}
        analysis = [{"filepath": fp, "face_count": 1, "id": pid} for pid, fp in photos]
        config = {"face_cluster_threshold": 0.55}

        extract_and_cluster_faces(conn, analysis, photo_map, 1024, 0.2, config)
        concurrent_labels = [r[2] for r in conn.execute(_FETCH_ASSIGNMENTS).fetchall()]

        # Compute expected labels sequentially (deterministic order)
        ordered_embs = []
        for _pid, fp in photos:
            result = _sandbox_extract_one(fp, 1024)
            if result:
                ordered_embs.append(result[0]["embedding"])
        expected_labels = cluster_faces(ordered_embs, threshold=0.55)

        assert _groupings(concurrent_labels) == _groupings(expected_labels)
        conn.close()


# ===========================================================================
# 12. RE-ANALYSIS — cluster ID preservation
# ===========================================================================

_FETCH_CLUSTER_IDS = (
    "SELECT fe.photo_id, fe.face_index, fe.cluster_id "
    "FROM face_embeddings fe ORDER BY fe.photo_id, fe.face_index"
)


class TestReanalysisPreservesClusterIds:
    """Verify that re-running extract_and_cluster_faces preserves cluster IDs.

    When all embeddings are already cached, reclustering should either be
    skipped entirely or remap new labels back to old cluster IDs.

    When new photos are added, existing cluster IDs must be preserved and
    only genuinely new clusters get new IDs.

    These tests pre-populate the DB with embeddings and cluster them directly
    (bypassing ThreadPoolExecutor) to set up the "before" state, then call
    extract_and_cluster_faces for re-analysis where all embeddings are cached.
    """

    @staticmethod
    def _initial_cluster(conn, photos, embs, threshold=0.55):
        """Insert embeddings and cluster them. Returns cluster assignments."""
        from bpp.scoring.face_cluster import cluster_faces as cf

        for i, (_pid, _fp) in enumerate(photos):
            if i >= len(embs):
                break
            pid = photos[i][0]
            conn.execute(
                "INSERT OR REPLACE INTO face_embeddings "
                "(photo_id, face_index, bbox_x, bbox_y, bbox_w, bbox_h, "
                "embedding, cluster_id, quality) VALUES (?,?,?,?,?,?,?,?,?)",
                (pid, 0, 10, 10, 50, 50, embs[i].tobytes(), CLUSTER_UNASSIGNED, 0.5),
            )
        conn.commit()

        # Cluster in deterministic order
        rows = conn.execute(
            "SELECT id, photo_id, face_index, embedding FROM face_embeddings "
            f"WHERE cluster_id != {CLUSTER_DISMISSED} "
            "ORDER BY photo_id, face_index"
        ).fetchall()
        db_embs = [np.frombuffer(r[3], dtype=np.float32) for r in rows]
        labels = cf(db_embs, threshold=threshold)
        conn.executemany(
            "UPDATE face_embeddings SET cluster_id=? WHERE id=?",
            ((cid, r[0]) for r, cid in zip(rows, labels, strict=True)),
        )
        conn.commit()

    def test_reanalysis_no_new_photos_preserves_ids(self, tmp_path):
        """Re-analysis with no new photos must keep exact same cluster IDs."""
        from bpp.web.face_worker import extract_and_cluster_faces

        embs = _make_multi_person(n_people=3, faces_per=4, noise=0.01)
        n = len(embs)
        conn, _ = _setup_face_db(tmp_path, n_photos=n)
        photos = conn.execute("SELECT id, filepath FROM photos ORDER BY id").fetchall()
        photo_map = {fp: pid for pid, fp in photos}
        analysis = [{"filepath": fp, "face_count": 1, "id": pid} for pid, fp in photos]
        config = {"face_cluster_threshold": 0.55}

        # Pre-populate DB with embeddings and cluster them
        self._initial_cluster(conn, photos, embs)

        first_ids = conn.execute(_FETCH_CLUSTER_IDS).fetchall()
        assert len(first_ids) == n
        assert all(r[2] >= 0 for r in first_ids), "All faces should be clustered"

        # Re-analysis — all cached, no extraction needed
        extract_and_cluster_faces(conn, analysis, photo_map, 1024, 0.2, config)

        second_ids = conn.execute(_FETCH_CLUSTER_IDS).fetchall()
        assert [r[2] for r in second_ids] == [r[2] for r in first_ids], (
            "Re-analysis without new photos must preserve exact cluster IDs"
        )

    def test_reanalysis_with_new_photos_preserves_existing_ids(self, tmp_path):
        """Adding new photos must not change cluster IDs for existing faces.

        Simulates: user imports more photos, re-analyzes. New unclustered
        embeddings get added. Reclustering must not change existing IDs.

        Uses 5 people with noise=0.05 and adds 5 more faces to trigger
        label shifts in agglomerative clustering.
        """
        from bpp.web.face_worker import extract_and_cluster_faces

        # 5 people, 4 faces each = 20 initial faces
        embs = _make_multi_person(n_people=5, faces_per=4, noise=0.05, seed_base=200)
        # 5 more faces for person 0 (new photos added to library)
        extra = _make_person_embeddings(200, 5, noise=0.05)

        n_initial = len(embs)
        n_total = n_initial + len(extra)
        conn, _ = _setup_face_db(tmp_path, n_photos=n_total)
        photos = conn.execute("SELECT id, filepath FROM photos ORDER BY id").fetchall()
        photo_map = {fp: pid for pid, fp in photos}
        config = {"face_cluster_threshold": 0.55}

        # Pre-populate DB with initial embeddings and cluster them
        self._initial_cluster(conn, photos[:n_initial], embs)

        first_ids = conn.execute(_FETCH_CLUSTER_IDS).fetchall()
        old_id_map = {(r[0], r[1]): r[2] for r in first_ids}

        # Now simulate new photos being extracted: insert their embeddings
        # as unclustered (cluster_id = CLUSTER_UNASSIGNED = -1)
        for i, emb in enumerate(extra):
            pid = photos[n_initial + i][0]
            conn.execute(
                "INSERT INTO face_embeddings "
                "(photo_id, face_index, bbox_x, bbox_y, bbox_w, bbox_h, "
                "embedding, cluster_id) VALUES (?,?,?,?,?,?,?,?)",
                (pid, 0, 10, 10, 50, 50, emb.tobytes(), CLUSTER_UNASSIGNED),
            )
        conn.commit()

        # Re-analysis: all photos — old ones are cached, new ones are
        # already in DB with cluster_id=-1 (so they count as cached too,
        # since -1 != CLUSTER_DISMISSED)
        all_analysis = [{"filepath": fp, "face_count": 1, "id": pid} for pid, fp in photos]
        extract_and_cluster_faces(conn, all_analysis, photo_map, 1024, 0.2, config)

        second_ids = conn.execute(_FETCH_CLUSTER_IDS).fetchall()
        new_id_map = {(r[0], r[1]): r[2] for r in second_ids}

        # Every face that existed before must keep the same cluster ID
        for key, old_cid in old_id_map.items():
            assert key in new_id_map, f"Face {key} disappeared after re-analysis"
            assert new_id_map[key] == old_cid, (
                f"Face {key} cluster changed from {old_cid} to {new_id_map[key]}"
            )

    def test_named_person_album_survives_reanalysis(self, tmp_path):
        """Smart person albums with cluster_id references must survive re-analysis."""
        import json

        from bpp.web.face_worker import extract_and_cluster_faces

        embs = _make_multi_person(n_people=3, faces_per=4, noise=0.01)
        n = len(embs)
        conn, _ = _setup_face_db(tmp_path, n_photos=n)
        photos = conn.execute("SELECT id, filepath FROM photos ORDER BY id").fetchall()
        photo_map = {fp: pid for pid, fp in photos}
        analysis = [{"filepath": fp, "face_count": 1, "id": pid} for pid, fp in photos]
        config = {"face_cluster_threshold": 0.55}

        # Pre-populate and cluster
        self._initial_cluster(conn, photos, embs)

        # Simulate naming a person: create smart_person album for cluster 0
        cluster_ids = sorted(set(r[2] for r in conn.execute(_FETCH_CLUSTER_IDS).fetchall()))
        target_cid = cluster_ids[0]
        conn.execute(
            "INSERT INTO albums (name, album_type, rule_json) VALUES (?, ?, ?)",
            ("Alex", "smart_person", json.dumps({"cluster_id": target_cid})),
        )
        conn.commit()

        # Faces in this cluster before re-analysis
        faces_before = conn.execute(
            "SELECT photo_id, face_index FROM face_embeddings "
            "WHERE cluster_id=? ORDER BY photo_id, face_index",
            (target_cid,),
        ).fetchall()

        # Re-analysis (all cached)
        extract_and_cluster_faces(conn, analysis, photo_map, 1024, 0.2, config)

        # Album still references the same cluster ID
        album = conn.execute(
            "SELECT rule_json FROM albums WHERE name='Alex' AND album_type='smart_person'"
        ).fetchone()
        assert album is not None
        assert json.loads(album[0])["cluster_id"] == target_cid

        # Same faces are in that cluster
        faces_after = conn.execute(
            "SELECT photo_id, face_index FROM face_embeddings "
            "WHERE cluster_id=? ORDER BY photo_id, face_index",
            (target_cid,),
        ).fetchall()
        assert faces_after == faces_before, f"Cluster {target_cid} faces changed after re-analysis"


class TestAssignNewFacesQuality:
    """Tests for quality-weighted centroids and small-face gating."""

    @staticmethod
    def _make_assigned(
        embeddings: list[np.ndarray],
        cid: int,
        bbox_w: int = 150,
        quality: float | None = None,
    ) -> list[tuple[str, int, int, np.ndarray, int, int, float | None]]:
        return [(f"p{i}.jpg", i, 0, emb, cid, bbox_w, quality) for i, emb in enumerate(embeddings)]

    def test_small_face_not_assigned_to_existing_cluster(self):
        """Faces below _MIN_ASSIGN_PX should NOT be assigned to existing clusters."""
        from bpp.web.face_worker import _MIN_ASSIGN_PX, _assign_new_faces

        # Build a cluster of clear adult faces
        adult_embs = _make_person_embeddings(42, count=10, noise=0.01)
        assigned = self._make_assigned(adult_embs, cid=0, bbox_w=150)

        # A small face near the adult centroid
        centroid = np.mean(np.stack(adult_embs), axis=0)
        small_face_emb = centroid + np.random.RandomState(99).randn(128) * 0.02
        unassigned = [("tiny.jpg", 100, 0, small_face_emb, _MIN_ASSIGN_PX - 1, None)]

        labels = _assign_new_faces(assigned, unassigned, threshold=0.55)
        # Should NOT get cluster 0; should get a new cluster ID
        assert labels[0] != 0

    def test_large_face_assigned_to_existing_cluster(self):
        """Faces at or above _MIN_ASSIGN_PX with close embedding should be assigned."""
        from bpp.web.face_worker import _MIN_ASSIGN_PX, _assign_new_faces

        adult_embs = _make_person_embeddings(42, count=10, noise=0.01)
        assigned = self._make_assigned(adult_embs, cid=0, bbox_w=150)

        centroid = np.mean(np.stack(adult_embs), axis=0)
        new_emb = centroid + np.random.RandomState(99).randn(128) * 0.02
        unassigned = [("big.jpg", 100, 0, new_emb, _MIN_ASSIGN_PX + 50, None)]

        labels = _assign_new_faces(assigned, unassigned, threshold=0.55)
        assert labels[0] == 0

    def test_quality_weighted_centroids_resist_contamination(self):
        """Quality-weighted centroids should be dominated by large faces."""
        from bpp.web.face_worker import _assign_new_faces

        rng = np.random.RandomState(42)
        # 10 clear adult embeddings (bbox 150) all near the same centroid
        adult_centroid = rng.randn(128)
        adult_centroid /= np.linalg.norm(adult_centroid)
        adult_embs = [adult_centroid + rng.randn(128) * 0.01 for _ in range(10)]

        # 3 "contaminating" tiny-face embeddings pulled toward a different region
        baby_centroid = rng.randn(128)
        baby_centroid /= np.linalg.norm(baby_centroid)
        contaminants = [baby_centroid + rng.randn(128) * 0.01 for _ in range(3)]

        # Build assigned: adults at 150px, contaminants at 45px (above min, but low quality)
        assigned: list[tuple[str, int, int, np.ndarray, int, int, float | None]] = []
        for i, emb in enumerate(adult_embs):
            assigned.append((f"adult{i}.jpg", i, 0, emb, 0, 150, None))
        for i, emb in enumerate(contaminants):
            assigned.append((f"noise{i}.jpg", 100 + i, 0, emb, 0, 45, None))

        # New adult face — should still match cluster 0
        new_adult = adult_centroid + rng.randn(128) * 0.02
        unassigned = [("new_adult.jpg", 200, 0, new_adult, 140, None)]

        labels = _assign_new_faces(assigned, unassigned, threshold=0.55)
        assert labels[0] == 0, "Quality-weighted centroid should keep adult faces matching"


class TestReanalyzePreservesClusterColumns:
    """Re-analyze must not wipe derived clustering columns.

    Regression (2026-06-12): bulk_upsert_photos updated EVERY column with
    `excluded.<col>`, and analyze/import callers never carry cluster data,
    so the 0/1 defaults zeroed dup_cluster_id + moment_* on each
    re-analyze — Moments collapsed 765→17 and Duplicates to 5 groups on
    the real library until recomputed. The owners of those columns are
    assign_near_duplicate_clusters / assign_moment_clusters; bulk_upsert
    must preserve them on conflict (phash/ahash wiping stays deliberate).
    """

    def test_bulk_upsert_update_preserves_cluster_assignments(self, tmp_path):
        from bpp.db.connection import init_db
        from bpp.db.photos import bulk_upsert_photos

        conn = init_db(str(tmp_path / "t.db"))
        photo = {"filepath": "/lib/a.jpg", "date": "2024-06-01T10:00:00"}
        bulk_upsert_photos(conn, [photo])
        conn.execute(
            "UPDATE photos SET dup_cluster_id=7, cluster_size=3, "
            "moment_cluster_id=9, moment_size=4 WHERE filepath='/lib/a.jpg'"
        )
        conn.commit()

        # Re-analyze: same photo, fresh scores, no cluster columns.
        bulk_upsert_photos(conn, [{**photo, "aggregate_score": 0.5}])

        row = conn.execute(
            "SELECT dup_cluster_id, cluster_size, moment_cluster_id, moment_size, "
            "aggregate_score FROM photos WHERE filepath='/lib/a.jpg'"
        ).fetchone()
        got = tuple(row[:4])
        assert got == (7, 3, 9, 4), f"re-analyze wiped cluster columns: {got}"
        assert row[4] == 0.5  # the analyze payload itself did land

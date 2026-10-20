"""Comprehensive tests for face clustering stability, determinism, and resilience — part 1 of 3."""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pytest

from bpp.constants import CLUSTER_DISMISSED, CLUSTER_UNASSIGNED
from bpp.scoring.face_cluster import cluster_faces, pick_representative
from tests._clustering_stability_shared import (
    _FETCH_ASSIGNMENTS,
    _cluster_db_embeddings,
    _groupings,
    _insert_embeddings,
    _make_multi_person,
    _make_multi_person_with_truth,
    _make_person_embeddings,
    _setup_face_db,
)

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


@pytest.mark.usefixtures("real_face_pipeline")
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

        The orchestrator switched from ``as_completed`` to
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
                    # The orchestrator now uses wait + FIRST_COMPLETED
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

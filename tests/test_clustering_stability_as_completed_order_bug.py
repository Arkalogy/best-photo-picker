"""Comprehensive tests for face clustering stability, determinism, and resilience — part 2 of 3:
TestAsCompletedOrderBug, TestReclusterOrderBy, TestSortFixRegression."""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pytest

from bpp.constants import CLUSTER_DISMISSED, CLUSTER_UNASSIGNED
from bpp.scoring.face_cluster import cluster_faces
from tests._clustering_stability_shared import (
    _FETCH_ASSIGNMENTS,
    _groupings,
    _make_multi_person,
    _sandbox_extract_one,
    _setup_face_db,
)

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


@pytest.mark.usefixtures("real_face_pipeline")
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


@pytest.mark.usefixtures("real_face_pipeline")
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

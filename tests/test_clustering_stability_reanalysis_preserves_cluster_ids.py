"""Comprehensive tests for face clustering stability, determinism, and resilience — part 3 of 3:
TestReanalysisPreservesClusterIds, TestAssignNewFacesQuality,
TestReanalyzePreservesClusterColumns."""

from __future__ import annotations

import numpy as np
import pytest

from bpp.constants import CLUSTER_DISMISSED, CLUSTER_UNASSIGNED
from bpp.db.connection import init_db
from tests._clustering_stability_shared import (
    _FETCH_CLUSTER_IDS,
    _make_multi_person,
    _make_person_embeddings,
    _setup_face_db,
)


@pytest.mark.usefixtures("real_face_pipeline")
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

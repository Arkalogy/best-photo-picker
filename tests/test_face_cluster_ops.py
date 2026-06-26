"""Direct unit tests for face_cluster_ops.py pure-DB helpers.

These functions were extracted from bp_faces_manage.py in commit 3d9b877
(M11.b refactor). Tests for the blueprint exercise them indirectly via
HTTP, but the direct unit coverage was thin. This file backfills it.

Each helper is pure DB (no Flask), so the tests just need a seeded SQLite
schema.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from bpp.constants import CLUSTER_DISMISSED, CLUSTER_UNASSIGNED
from bpp.db.connection import get_db, init_db
from bpp.db.face_cluster_ops import (
    cleanup_person_albums,
    propagate_cluster,
    propagate_identity_on_merge,
    sync_face_count,
    sync_face_counts_for_clusters,
    sync_face_counts_for_photos,
)
from bpp.db.photos import upsert_photo


@pytest.fixture
def conn(tmp_path):
    db_path = str(tmp_path / "cluster_ops.db")
    init_db(db_path)
    return get_db(db_path)


def _add_photo(conn, tmp_path, name, **extra):
    f = tmp_path / name
    f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 32)
    photo = {"filepath": str(f)}
    photo.update(extra)
    return upsert_photo(conn, photo)


def _add_face(conn, photo_id, cluster_id, embedding=None, identity=None):
    """Insert a face_embeddings row. Returns the new row's id.

    Auto-increments face_index per photo so callers can add multiple
    faces to the same photo without colliding on the (photo_id,
    face_index) unique constraint.
    """
    if embedding is None:
        embedding = np.zeros(128, dtype=np.float32)
    # Defensive: a test that builds an embedding by adding/subtracting
    # arrays will silently promote to float64 unless explicitly cast.
    # Force float32 here so the DB blob is always 512 bytes (post-v35
    # schema), matching what production face_embed.py writes.
    embedding = np.asarray(embedding, dtype=np.float32)
    next_idx_row = conn.execute(
        "SELECT COALESCE(MAX(face_index), -1) + 1 FROM face_embeddings WHERE photo_id=?",
        (photo_id,),
    ).fetchone()
    face_index = next_idx_row[0]
    cols = ["photo_id", "face_index", "embedding", "cluster_id"]
    vals = [photo_id, face_index, embedding.tobytes(), cluster_id]
    # identity column may not exist on older schemas; check first
    from bpp.db.dialect import dialect

    if identity is not None and "identity" in dialect.column_names(conn, "face_embeddings"):
        cols.append("identity")
        vals.append(identity)
    placeholders = ", ".join(["?"] * len(cols))
    cur = conn.execute(
        f"INSERT INTO face_embeddings ({', '.join(cols)}) VALUES ({placeholders})", vals
    )
    conn.commit()
    return cur.lastrowid


# ── sync_face_counts_for_photos ─────────────────────────────────────────


class TestSyncFaceCountsForPhotos:
    def test_updates_count_to_match_embeddings(self, conn, tmp_path):
        p = _add_photo(conn, tmp_path, "p.jpg")
        _add_face(conn, p, cluster_id=0)
        _add_face(conn, p, cluster_id=0)
        _add_face(conn, p, cluster_id=1)
        sync_face_counts_for_photos(conn, [p])
        row = conn.execute("SELECT face_count FROM photos WHERE id=?", (p,)).fetchone()
        assert row[0] == 3

    def test_excludes_dismissed_faces(self, conn, tmp_path):
        p = _add_photo(conn, tmp_path, "p.jpg")
        _add_face(conn, p, cluster_id=0)
        _add_face(conn, p, cluster_id=CLUSTER_DISMISSED)
        sync_face_counts_for_photos(conn, [p])
        row = conn.execute("SELECT face_count FROM photos WHERE id=?", (p,)).fetchone()
        # Dismissed cluster_id is negative, so only the active face counts
        assert row[0] == 1

    def test_empty_list_is_noop(self, conn, tmp_path):
        # Should not raise
        sync_face_counts_for_photos(conn, [])

    def test_batch_update(self, conn, tmp_path):
        a = _add_photo(conn, tmp_path, "a.jpg")
        b = _add_photo(conn, tmp_path, "b.jpg")
        _add_face(conn, a, cluster_id=0)
        _add_face(conn, b, cluster_id=1)
        _add_face(conn, b, cluster_id=2)
        sync_face_counts_for_photos(conn, [a, b])
        rows = dict(
            conn.execute("SELECT id, face_count FROM photos WHERE id IN (?, ?)", (a, b)).fetchall()
        )
        assert rows[a] == 1
        assert rows[b] == 2


# ── sync_face_count (single-face wrapper) ───────────────────────────────


class TestSyncFaceCount:
    def test_resolves_face_to_photo_and_syncs(self, conn, tmp_path):
        p = _add_photo(conn, tmp_path, "p.jpg")
        fid = _add_face(conn, p, cluster_id=0)
        _add_face(conn, p, cluster_id=0)
        sync_face_count(conn, fid)
        row = conn.execute("SELECT face_count FROM photos WHERE id=?", (p,)).fetchone()
        assert row[0] == 2

    def test_unknown_face_id_is_noop(self, conn, tmp_path):
        # Should not raise
        sync_face_count(conn, 999999)


# ── sync_face_counts_for_clusters ───────────────────────────────────────


class TestSyncFaceCountsForClusters:
    def test_finds_affected_photos_and_syncs(self, conn, tmp_path):
        a = _add_photo(conn, tmp_path, "a.jpg")
        b = _add_photo(conn, tmp_path, "b.jpg")
        c = _add_photo(conn, tmp_path, "c.jpg")
        _add_face(conn, a, cluster_id=1)
        _add_face(conn, b, cluster_id=2)
        _add_face(conn, c, cluster_id=3)
        # Sync only clusters 1 and 2 — c (cluster 3) should be untouched
        sync_face_counts_for_clusters(conn, [1, 2])
        rows = dict(
            conn.execute(
                "SELECT id, face_count FROM photos WHERE id IN (?, ?, ?)", (a, b, c)
            ).fetchall()
        )
        assert rows[a] == 1
        assert rows[b] == 1
        # c (cluster 3) was not synced so its face_count remains at the
        # upsert default (NULL). We only care that a/b were updated.
        assert rows[c] is None or rows[c] == 0

    def test_empty_cluster_list_is_noop(self, conn):
        sync_face_counts_for_clusters(conn, [])


# ── cleanup_person_albums ───────────────────────────────────────────────


class TestCleanupPersonAlbums:
    def test_removes_albums_for_given_clusters(self, conn, tmp_path):
        # Create person albums for clusters 1, 2, 3
        for cid in (1, 2, 3):
            conn.execute(
                "INSERT INTO albums (name, album_type, rule_json) VALUES (?, 'smart_person', ?)",
                (f"Person {cid}", json.dumps({"cluster_id": cid}, sort_keys=True)),
            )
        conn.commit()
        cleanup_person_albums(conn, [1, 3])
        remaining = conn.execute(
            "SELECT rule_json FROM albums WHERE album_type='smart_person'"
        ).fetchall()
        assert len(remaining) == 1
        rule = json.loads(remaining[0][0])
        assert rule["cluster_id"] == 2

    def test_cascades_to_album_photos(self, conn, tmp_path):
        p = _add_photo(conn, tmp_path, "p.jpg")
        cur = conn.execute(
            "INSERT INTO albums (name, album_type, rule_json) "
            "VALUES ('Cluster 5', 'smart_person', ?)",
            (json.dumps({"cluster_id": 5}, sort_keys=True),),
        )
        album_id = cur.lastrowid
        conn.execute("INSERT INTO album_photos (album_id, photo_id) VALUES (?, ?)", (album_id, p))
        conn.commit()
        cleanup_person_albums(conn, [5])
        ap = conn.execute(
            "SELECT COUNT(*) FROM album_photos WHERE album_id=?", (album_id,)
        ).fetchone()
        assert ap[0] == 0

    def test_empty_cluster_list_is_noop(self, conn):
        cleanup_person_albums(conn, [])

    def test_unknown_cluster_is_noop(self, conn):
        # No album exists for cluster 99 — must not raise
        cleanup_person_albums(conn, [99])


# ── propagate_identity_on_merge ─────────────────────────────────────────


class TestPropagateIdentityOnMerge:
    def test_majority_identity_propagates_to_unlabeled(self, conn, tmp_path):
        from bpp.db.dialect import dialect

        if "identity" not in dialect.column_names(conn, "face_embeddings"):
            pytest.skip("identity column not present in this schema")
        p1 = _add_photo(conn, tmp_path, "p1.jpg")
        p2 = _add_photo(conn, tmp_path, "p2.jpg")
        p3 = _add_photo(conn, tmp_path, "p3.jpg")
        # Two faces labeled "Alice", one unlabeled, all in cluster 1
        _add_face(conn, p1, cluster_id=1, identity="Alice")
        _add_face(conn, p2, cluster_id=1, identity="Alice")
        _add_face(conn, p3, cluster_id=1)
        propagate_identity_on_merge(conn, primary_cluster_id=1)
        rows = conn.execute("SELECT identity FROM face_embeddings WHERE cluster_id=1").fetchall()
        identities = [r[0] for r in rows]
        assert identities.count("Alice") == 3

    def test_does_not_overwrite_existing_labels(self, conn, tmp_path):
        from bpp.db.dialect import dialect

        if "identity" not in dialect.column_names(conn, "face_embeddings"):
            pytest.skip("identity column not present")
        p1 = _add_photo(conn, tmp_path, "p1.jpg")
        p2 = _add_photo(conn, tmp_path, "p2.jpg")
        _add_face(conn, p1, cluster_id=1, identity="Alice")
        _add_face(conn, p2, cluster_id=1, identity="Bob")
        propagate_identity_on_merge(conn, primary_cluster_id=1)
        rows = conn.execute(
            "SELECT identity FROM face_embeddings WHERE cluster_id=1 ORDER BY id"
        ).fetchall()
        # Both keep their existing labels — propagation only fills NULLs
        assert {r[0] for r in rows} == {"Alice", "Bob"}

    def test_no_labeled_faces_is_noop(self, conn, tmp_path):
        from bpp.db.dialect import dialect

        if "identity" not in dialect.column_names(conn, "face_embeddings"):
            pytest.skip("identity column not present")
        p = _add_photo(conn, tmp_path, "p.jpg")
        _add_face(conn, p, cluster_id=1)
        propagate_identity_on_merge(conn, primary_cluster_id=1)
        rows = conn.execute("SELECT identity FROM face_embeddings WHERE cluster_id=1").fetchall()
        assert rows[0][0] is None


# ── propagate_cluster ───────────────────────────────────────────────────


class TestPropagateCluster:
    def test_absorbs_close_faces_into_target(self, conn, tmp_path):
        # Build two faces with similar embeddings (cluster -1 / unassigned),
        # call propagate, assert they land in target_cluster_id.
        p1 = _add_photo(conn, tmp_path, "p1.jpg")
        p2 = _add_photo(conn, tmp_path, "p2.jpg")
        ref_emb = np.ones(128, dtype=np.float32) * 0.1
        near_emb = ref_emb + np.random.normal(0, 0.01, 128)
        _add_face(conn, p1, cluster_id=CLUSTER_UNASSIGNED, embedding=near_emb)
        _add_face(conn, p2, cluster_id=CLUSTER_UNASSIGNED, embedding=near_emb)
        moved = propagate_cluster(conn, ref_emb, target_cluster_id=5, threshold=1.0)
        assert moved == 2
        # Verify in DB
        rows = conn.execute("SELECT cluster_id FROM face_embeddings").fetchall()
        assert all(r[0] == 5 for r in rows)

    def test_skips_dismissed_cluster(self, conn, tmp_path):
        p = _add_photo(conn, tmp_path, "p.jpg")
        ref_emb = np.ones(128, dtype=np.float32) * 0.1
        # Face is dismissed — must not be pulled in
        _add_face(conn, p, cluster_id=CLUSTER_DISMISSED, embedding=ref_emb)
        moved = propagate_cluster(conn, ref_emb, target_cluster_id=5, threshold=10.0)
        assert moved == 0

    def test_distance_threshold_filters_far_faces(self, conn, tmp_path):
        p1 = _add_photo(conn, tmp_path, "near.jpg")
        p2 = _add_photo(conn, tmp_path, "far.jpg")
        ref_emb = np.zeros(128, dtype=np.float32)
        near_emb = np.zeros(128, dtype=np.float32)
        near_emb[0] = 0.1
        far_emb = np.ones(128, dtype=np.float32) * 10
        _add_face(conn, p1, cluster_id=CLUSTER_UNASSIGNED, embedding=near_emb)
        _add_face(conn, p2, cluster_id=CLUSTER_UNASSIGNED, embedding=far_emb)
        moved = propagate_cluster(conn, ref_emb, target_cluster_id=5, threshold=0.5)
        # Only the near face moves
        assert moved == 1

    def test_no_candidates_returns_zero(self, conn):
        ref_emb = np.zeros(128, dtype=np.float32)
        # Empty DB — nothing to propagate
        moved = propagate_cluster(conn, ref_emb, target_cluster_id=5, threshold=1.0)
        assert moved == 0

    def test_exclude_face_ids_honored(self, conn, tmp_path):
        p = _add_photo(conn, tmp_path, "p.jpg")
        ref_emb = np.zeros(128, dtype=np.float32)
        fid = _add_face(conn, p, cluster_id=CLUSTER_UNASSIGNED, embedding=ref_emb)
        moved = propagate_cluster(
            conn, ref_emb, target_cluster_id=5, threshold=1.0, exclude_face_ids={fid}
        )
        assert moved == 0

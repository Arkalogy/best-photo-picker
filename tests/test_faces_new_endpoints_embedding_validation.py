"""Tests for new face endpoints: tag, untag, reassign, avatar, cluster detail, photo faces — part 2
of 2: TestEmbeddingValidation, TestStaleAvatarFallback, TestLoadFaceClustersWithTags."""

# Shared fixtures are imported by name and re-bound as test parameters.
# ruff: noqa: F811

from __future__ import annotations

import json
import os

import numpy as np

from tests._faces_new_endpoints_shared import (  # noqa: F401
    _ctx,
    _get_photo_id_and_hash,
    _insert_face,
    _make_analysis,
    bp_app,
    client,
)


class TestEmbeddingValidation:
    """Embedding shape/type should be validated before DB insert."""

    def test_valid_embedding(self):
        from bpp.web.face_worker import _validate_embedding

        emb = np.random.randn(128).astype(np.float32)
        assert _validate_embedding(emb) is True

    def test_wrong_shape_rejected(self):
        from bpp.web.face_worker import _validate_embedding

        emb = np.random.randn(64).astype(np.float32)
        assert _validate_embedding(emb) is False

    def test_nan_rejected(self):
        from bpp.web.face_worker import _validate_embedding

        emb = np.full(128, np.nan, dtype=np.float32)
        assert _validate_embedding(emb) is False

    def test_inf_rejected(self):
        from bpp.web.face_worker import _validate_embedding

        emb = np.full(128, np.inf, dtype=np.float32)
        assert _validate_embedding(emb) is False

    def test_zero_vector_rejected(self):
        """Zero-magnitude embeddings poison clustering — every distance
        collapses to the centroid norm, so threshold-based clustering snaps
        the zero face onto whatever cluster it sees first."""
        from bpp.web.face_worker import _validate_embedding

        emb = np.zeros(128, dtype=np.float32)
        assert _validate_embedding(emb) is False

    def test_near_zero_vector_rejected(self):
        from bpp.web.face_worker import _validate_embedding

        emb = np.full(128, 1e-9, dtype=np.float32)
        assert _validate_embedding(emb) is False

    def test_small_but_real_magnitude_accepted(self):
        from bpp.web.face_worker import _validate_embedding

        # Norm ≈ 1.0 (16 dims at 0.25 ≈ norm 1.0)
        emb = np.full(128, 0.0884, dtype=np.float32)
        assert _validate_embedding(emb) is True


class TestStaleAvatarFallback:
    """Avatar override pointing to deleted file should fall back gracefully."""

    def test_stale_avatar_ignored(self, client, bp_app):
        ctx = _ctx(bp_app)
        with bp_app.app_context():
            conn = ctx.get_conn()
            pid = conn.execute("SELECT id FROM photos LIMIT 1").fetchone()["id"]
            emb = np.random.randn(128).astype(np.float32).tobytes()
            conn.execute(
                "INSERT INTO face_embeddings"
                " (photo_id, face_index, embedding, cluster_id,"
                " bbox_x, bbox_y, bbox_w, bbox_h)"
                " VALUES (?, 0, ?, 0, 10, 10, 50, 50)",
                (pid, emb),
            )
            # Set avatar to non-existent file
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (
                    "person_avatar_0",
                    json.dumps({"filepath": "/nonexistent/photo.jpg", "face_index": 0}),
                ),
            )
            conn.commit()

        resp = client.get("/api/v1/faces/clusters")
        assert resp.status_code == 200
        clusters = resp.get_json()["clusters"]
        # Should still return clusters (not crash), avatar falls back
        assert len(clusters) >= 1
        # The representative should use the override filepath even if stale
        # (crop generation will fail later, but cluster listing shouldn't crash)
        rep = clusters[0]["representative"]
        assert "filepath" in rep


# ── Phase 4: Face worker logic tests ──


class TestLoadFaceClustersWithTags:
    """load_face_clusters() should include manually tagged photos."""

    def test_manual_tags_in_filepaths(self, bp_app):
        from bpp.web.face_worker import load_face_clusters

        ctx = _ctx(bp_app)
        with bp_app.app_context():
            conn = ctx.get_conn()
            photos = conn.execute("SELECT id, filepath FROM photos LIMIT 3").fetchall()
            emb = np.random.randn(128).astype(np.float32).tobytes()
            # One face embedding in cluster 0 for photo 0
            conn.execute(
                "INSERT INTO face_embeddings"
                " (photo_id, face_index, embedding, cluster_id,"
                " bbox_x, bbox_y, bbox_w, bbox_h)"
                " VALUES (?, 0, ?, 0, 10, 10, 50, 50)",
                (photos[0]["id"], emb),
            )
            # Manually tag photo 1 as cluster 0
            conn.execute(
                "INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (?, 0)",
                (photos[1]["id"],),
            )
            conn.commit()

            clusters = load_face_clusters(conn)
            cluster_0 = [c for c in clusters if c["cluster_id"] == 0]
            assert len(cluster_0) == 1
            fps = cluster_0[0]["filepaths"]
            # Both the embedded photo and the tagged photo should be in filepaths
            assert photos[0]["filepath"] in fps
            assert photos[1]["filepath"] in fps

    def test_tag_only_cluster(self, bp_app):
        """A cluster with only manual tags (no embeddings) should NOT appear
        in load_face_clusters since it only returns embedding-based clusters."""
        from bpp.web.face_worker import load_face_clusters

        ctx = _ctx(bp_app)
        with bp_app.app_context():
            conn = ctx.get_conn()
            pid = conn.execute("SELECT id FROM photos LIMIT 1").fetchone()["id"]
            conn.execute(
                "INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (?, 99)",
                (pid,),
            )
            conn.commit()

            clusters = load_face_clusters(conn)
            cluster_99 = [c for c in clusters if c["cluster_id"] == 99]
            # Not in load_face_clusters (it needs embeddings to compute representative)
            assert len(cluster_99) == 0


class TestSmartAlbumPersonTags:
    """Smart person albums should include manually tagged photos."""

    def test_person_album_includes_tagged_photos(self, bp_app):
        from bpp.db.smart_albums import (
            get_smart_album_photo_ids,
            refresh_smart_albums,
        )

        ctx = _ctx(bp_app)
        with bp_app.app_context():
            conn = ctx.get_conn()
            photos = conn.execute("SELECT id, filepath FROM photos LIMIT 3").fetchall()
            emb = np.random.randn(128).astype(np.float32).tobytes()
            # Embedding in cluster 0 for photo 0
            conn.execute(
                "INSERT INTO face_embeddings"
                " (photo_id, face_index, embedding, cluster_id,"
                " bbox_x, bbox_y, bbox_w, bbox_h)"
                " VALUES (?, 0, ?, 0, 10, 10, 50, 50)",
                (photos[0]["id"], emb),
            )
            # Tag photo 1 as cluster 0
            conn.execute(
                "INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (?, 0)",
                (photos[1]["id"],),
            )
            conn.commit()

            refresh_smart_albums(conn)

            # Find the smart_person album for cluster 0
            album = conn.execute(
                "SELECT * FROM albums WHERE album_type='smart_person'"
                " AND json_extract(rule_json, '$.cluster_id')=0"
            ).fetchone()
            assert album is not None

            photo_ids = get_smart_album_photo_ids(conn, dict(album))
            assert photos[0]["id"] in photo_ids
            assert photos[1]["id"] in photo_ids

    def test_tag_only_creates_person_album(self, bp_app):
        """A cluster with only manual tags should still get a person album."""
        from bpp.db.smart_albums import refresh_smart_albums

        ctx = _ctx(bp_app)
        with bp_app.app_context():
            conn = ctx.get_conn()
            pid = conn.execute("SELECT id FROM photos LIMIT 1").fetchone()["id"]
            conn.execute(
                "INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (?, 42)",
                (pid,),
            )
            conn.commit()

            refresh_smart_albums(conn)

            album = conn.execute(
                "SELECT * FROM albums WHERE album_type='smart_person'"
                " AND json_extract(rule_json, '$.cluster_id')=42"
            ).fetchone()
            assert album is not None


class TestGenerateFaceCrop:
    """Test generate_face_crop with real images."""

    def test_valid_crop(self, tmp_path):
        from PIL import Image

        from bpp.web.face_worker import generate_face_crop

        # Create a test image
        img = Image.new("RGB", (200, 200), color="red")
        img_path = str(tmp_path / "test.jpg")
        img.save(img_path)

        crop_dir = str(tmp_path / "crops")
        os.makedirs(crop_dir)

        result = generate_face_crop(
            filepath=img_path,
            bbox=(50, 50, 80, 80),
            crop_dir=crop_dir,
            path_hash="testhash",
            face_index=0,
            max_long_side=1024,
        )
        assert result is not None
        assert os.path.exists(result)

    def test_invalid_bbox_returns_none(self, tmp_path):
        from PIL import Image

        from bpp.web.face_worker import generate_face_crop

        img = Image.new("RGB", (200, 200), color="blue")
        img_path = str(tmp_path / "test.jpg")
        img.save(img_path)

        crop_dir = str(tmp_path / "crops")
        os.makedirs(crop_dir)

        # Zero-size bbox
        result = generate_face_crop(
            filepath=img_path,
            bbox=(0, 0, 0, 0),
            crop_dir=crop_dir,
            path_hash="testhash",
            face_index=0,
        )
        assert result is None

    def test_nonexistent_file_returns_none(self, tmp_path):
        from bpp.web.face_worker import generate_face_crop

        crop_dir = str(tmp_path / "crops")
        os.makedirs(crop_dir)

        result = generate_face_crop(
            filepath="/nonexistent/photo.jpg",
            bbox=(10, 10, 50, 50),
            crop_dir=crop_dir,
            path_hash="testhash",
            face_index=0,
        )
        assert result is None

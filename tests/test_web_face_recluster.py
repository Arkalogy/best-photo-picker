"""Tests for the web UI: recompute logic + route smoke tests — part 3 of 3: TestFaceRecluster,
TestDeleteRoutes."""

# Shared fixtures are imported by name and re-bound as test parameters.
# ruff: noqa: F811

from __future__ import annotations

import json
import os

from tests._web_shared import (  # noqa: F401
    _default_config,
    _make_analysis,
    _setup_face_db,
    face_web_client,
    web_client,
)


class TestFaceRecluster:
    def test_recluster_blocked_during_extraction(self, face_web_client):
        """Recluster must return 409 while face extraction is running."""
        from unittest.mock import PropertyMock, patch

        with patch(
            "bpp.web.face_worker.FaceWorker.is_alive",
            new_callable=PropertyMock,
            return_value=True,
        ):
            resp = face_web_client.post("/api/v1/faces/recluster", json={"threshold": 0.6})
        assert resp.status_code == 409

    def test_recluster_missing_threshold(self, face_web_client):
        resp = face_web_client.post("/api/v1/faces/recluster", json={})
        assert resp.status_code == 400

    def test_recluster_invalid_threshold(self, face_web_client):
        resp = face_web_client.post("/api/v1/faces/recluster", json={"threshold": 0.1})
        assert resp.status_code == 400

    def test_recluster_rejects_non_numeric(self, face_web_client):
        """Out-of-range and non-numeric thresholds must be rejected."""
        for val in ["abc", None, 99.0, -1.0, 0.0]:
            resp = face_web_client.post("/api/v1/faces/recluster", json={"threshold": val})
            assert resp.status_code == 400, f"Expected 400 for threshold={val!r}"

    def test_recluster_with_real_embeddings(self, tmp_path):
        """Recluster endpoint re-runs clustering with new threshold."""
        import numpy as np

        from bpp.db.connection import get_db
        from bpp.db.smart_albums import refresh_smart_albums
        from bpp.web.app import create_app

        workdir = str(tmp_path / "workdir")
        os.makedirs(workdir)
        analysis = _make_analysis(5)
        with open(os.path.join(workdir, "analysis.json"), "w") as f:
            json.dump(analysis, f)

        app = create_app(workdir=workdir)
        app.config["TESTING"] = True
        client = app.test_client()
        client.get("/api/v1/photos")

        db_path = os.path.join(workdir, "photopicker.db")
        conn = get_db(db_path)

        # Seed 4 face embeddings: 2 similar pairs with controlled distances
        rng = np.random.RandomState(42)
        base_a = rng.randn(128).astype(np.float32)
        # base_b is far enough to separate at 0.5 but close enough to merge at high threshold
        offset = np.zeros(128, dtype=np.float32)
        offset[0] = 0.8  # Euclidean distance ~0.8 between base_a and base_b
        base_b = base_a + offset
        embeddings = [
            base_a,
            base_a + rng.randn(128) * 0.01,  # very close to base_a
            base_b,
            base_b + rng.randn(128) * 0.01,  # very close to base_b
        ]
        for i, emb in enumerate(embeddings):
            photo = conn.execute(
                "SELECT id FROM photos WHERE filepath=?", (analysis[i]["filepath"],)
            ).fetchone()
            if photo:
                conn.execute(
                    "INSERT OR REPLACE INTO face_embeddings "
                    "(photo_id, face_index, embedding, cluster_id) "
                    "VALUES (?, 0, ?, 0)",
                    (photo[0], np.asarray(emb, dtype=np.float32).tobytes()),
                )
        conn.commit()
        refresh_smart_albums(conn)

        # Recluster with a tight threshold — should produce 2 clusters
        resp = client.post("/api/v1/faces/recluster", json={"threshold": 0.5})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "reclustered"
        assert data["clusters"] == 2

        # Recluster with a very loose threshold — should merge into 1 cluster
        resp = client.post("/api/v1/faces/recluster", json={"threshold": 1.2})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["clusters"] == 1

    def test_recluster_preserves_named_clusters(self, tmp_path):
        """Named person albums must survive reclustering with new threshold."""
        import numpy as np

        from bpp.db.connection import get_db
        from bpp.db.smart_albums import refresh_smart_albums
        from bpp.web.app import create_app

        workdir = str(tmp_path / "workdir")
        os.makedirs(workdir)
        analysis = _make_analysis(6)
        with open(os.path.join(workdir, "analysis.json"), "w") as f:
            json.dump(analysis, f)

        app = create_app(workdir=workdir)
        app.config["TESTING"] = True
        client = app.test_client()
        client.get("/api/v1/photos")

        db_path = os.path.join(workdir, "photopicker.db")
        conn = get_db(db_path)

        # Create 2 well-separated clusters with 3 faces each
        rng = np.random.RandomState(42)
        base_a = rng.randn(128).astype(np.float32)
        base_a /= np.linalg.norm(base_a)
        offset = np.zeros(128, dtype=np.float32)
        offset[0] = 2.0  # Far apart
        base_b = base_a + offset
        base_b /= np.linalg.norm(base_b)

        photos = conn.execute("SELECT id FROM photos ORDER BY id").fetchall()
        # Cluster 0: photos 0-2 (person A)
        for i in range(3):
            emb = base_a + rng.randn(128) * 0.01
            conn.execute(
                "INSERT OR REPLACE INTO face_embeddings "
                "(photo_id, face_index, bbox_x, bbox_y, bbox_w, bbox_h, "
                "embedding, cluster_id) VALUES (?, 0, 0, 0, 100, 100, ?, 0)",
                (photos[i][0], np.asarray(emb, dtype=np.float32).tobytes()),
            )
        # Cluster 1: photos 3-5 (person B)
        for i in range(3, 6):
            emb = base_b + rng.randn(128) * 0.01
            conn.execute(
                "INSERT OR REPLACE INTO face_embeddings "
                "(photo_id, face_index, bbox_x, bbox_y, bbox_w, bbox_h, "
                "embedding, cluster_id) VALUES (?, 0, 0, 0, 100, 100, ?, 1)",
                (photos[i][0], np.asarray(emb, dtype=np.float32).tobytes()),
            )
        conn.commit()
        refresh_smart_albums(conn)

        # Name cluster 0 as "Alice"
        conn.execute(
            "UPDATE albums SET name='Alice' WHERE album_type='smart_person' AND rule_json=?",
            (json.dumps({"cluster_id": 0}, sort_keys=True),),
        )
        conn.commit()

        # Verify Alice exists
        album = conn.execute(
            "SELECT name FROM albums WHERE name='Alice' AND album_type='smart_person'"
        ).fetchone()
        assert album is not None

        # Recluster — cluster IDs will change but Alice should survive
        resp = client.post("/api/v1/faces/recluster", json={"threshold": 0.55})
        assert resp.status_code == 200
        assert resp.get_json()["clusters"] == 2

        # Alice's name should still exist on a smart_person album
        alice = conn.execute(
            "SELECT rule_json FROM albums WHERE name='Alice' AND album_type='smart_person'"
        ).fetchone()
        assert alice is not None, "Alice's named album was lost during recluster"

        # And the cluster it references should have photos
        rule = json.loads(alice[0])
        cid = rule["cluster_id"]
        face_count = conn.execute(
            "SELECT COUNT(*) FROM face_embeddings WHERE cluster_id=?",
            (cid,),
        ).fetchone()[0]
        assert face_count == 3, f"Alice's cluster {cid} should have 3 faces"

    def test_recluster_no_embeddings(self, tmp_path):
        """Recluster on empty DB returns 0 clusters."""
        from bpp.web.app import create_app

        workdir = str(tmp_path / "workdir")
        os.makedirs(workdir)
        analysis = _make_analysis(3)
        with open(os.path.join(workdir, "analysis.json"), "w") as f:
            json.dump(analysis, f)

        app = create_app(workdir=workdir)
        app.config["TESTING"] = True
        client = app.test_client()
        client.get("/api/v1/photos")

        resp = client.post("/api/v1/faces/recluster", json={"threshold": 0.6})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["clusters"] == 0


class TestDeleteRoutes:
    def test_delete_photos(self, web_client):
        filepaths = [f"/tmp/test_photos/img_{i:03d}.jpg" for i in range(2)]
        resp = web_client.post("/api/v1/photos/delete", json={"filepaths": filepaths})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["count"] == 2

    def test_delete_empty(self, web_client):
        resp = web_client.post("/api/v1/photos/delete", json={"filepaths": []})
        assert resp.status_code == 400

    def test_list_deleted(self, web_client):
        filepaths = ["/tmp/test_photos/img_000.jpg"]
        web_client.post("/api/v1/photos/delete", json={"filepaths": filepaths})
        resp = web_client.get("/api/v1/photos/deleted")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] >= 1
        found = [p for p in data["photos"] if p["filepath"] == filepaths[0]]
        assert len(found) == 1
        assert found[0]["deleted_at"] != ""

    def test_restore_photos(self, web_client):
        filepaths = ["/tmp/test_photos/img_001.jpg"]
        web_client.post("/api/v1/photos/delete", json={"filepaths": filepaths})
        resp = web_client.post("/api/v1/photos/restore", json={"filepaths": filepaths})
        assert resp.status_code == 200
        assert resp.get_json()["count"] == 1
        # Should not appear in deleted list anymore
        resp = web_client.get("/api/v1/photos/deleted")
        deleted_fps = [p["filepath"] for p in resp.get_json()["photos"]]
        assert filepaths[0] not in deleted_fps

    def test_restore_empty(self, web_client):
        resp = web_client.post("/api/v1/photos/restore", json={"filepaths": []})
        assert resp.status_code == 400

    def test_permanent_delete(self, web_client):
        filepaths = ["/tmp/test_photos/img_002.jpg"]
        web_client.post("/api/v1/photos/delete", json={"filepaths": filepaths})
        resp = web_client.post(
            "/api/v1/photos/delete-permanent",
            json={"filepaths": filepaths, "confirmation": "delete"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 1
        # Gone from deleted list
        resp = web_client.get("/api/v1/photos/deleted")
        deleted_fps = [p["filepath"] for p in resp.get_json()["photos"]]
        assert filepaths[0] not in deleted_fps

    def test_permanent_delete_empty(self, web_client):
        resp = web_client.post(
            "/api/v1/photos/delete-permanent",
            json={"filepaths": [], "confirmation": "delete"},
        )
        assert resp.status_code == 400

    def test_permanent_delete_path_traversal(self, tmp_path):
        """Permanent delete must not remove files outside library/workdir."""
        from bpp.web.app import create_app

        workdir = str(tmp_path / "workdir")
        os.makedirs(workdir)

        # Create a decoy file outside the library/workdir
        decoy = tmp_path / "outside" / "secret.txt"
        decoy.parent.mkdir()
        decoy.write_text("do not delete")

        # Seed analysis with a filepath pointing outside the library
        analysis = [
            {
                "filepath": str(decoy),
                "date": "2024-01-01T12:00:00",
                "date_day": "2024-01-01",
                "date_month": "2024-01",
                "file_size": 100,
                "file_mtime": 1700000000.0,
                "blur_raw": 100.0,
                "blur_score": 0.5,
                "exposure_score": 0.5,
                "face_score": 0.3,
                "face_count": 0,
                "largest_face_ratio": 0.0,
                "face_center_dist": 0.0,
                "composition_score": 0.5,
                "aggregate_score": 0.5,
            }
        ]
        with open(os.path.join(workdir, "analysis.json"), "w") as f:
            json.dump(analysis, f)

        app = create_app(workdir=workdir)
        app.config["TESTING"] = True
        client = app.test_client()
        # Load photos into DB
        client.get("/api/v1/photos")

        # Soft delete, then permanent delete
        client.post("/api/v1/photos/delete", json={"filepaths": [str(decoy)]})
        resp = client.post(
            "/api/v1/photos/delete-permanent",
            json={"filepaths": [str(decoy)], "confirmation": "delete"},
        )
        assert resp.status_code == 200
        # File outside library/workdir must NOT have been deleted from disk
        assert decoy.exists(), "File outside library/workdir was deleted — path traversal!"

    def test_remove_from_album(self, web_client):
        # Create album, add photos, then remove
        resp = web_client.post("/api/v1/albums", json={"name": "RemoveTest"})
        album_id = resp.get_json()["id"]
        filepaths = [f"/tmp/test_photos/img_{i:03d}.jpg" for i in range(3)]
        web_client.post(f"/api/v1/albums/{album_id}/add-photos", json={"filepaths": filepaths})
        resp = web_client.post(
            f"/api/v1/albums/{album_id}/remove-photos",
            json={"filepaths": [filepaths[0]]},
        )
        assert resp.status_code == 200
        assert resp.get_json()["count"] == 1

    def test_remove_from_nonexistent_album(self, web_client):
        resp = web_client.post(
            "/api/v1/albums/9999/remove-photos",
            json={"filepaths": ["/tmp/test_photos/img_000.jpg"]},
        )
        assert resp.status_code == 404

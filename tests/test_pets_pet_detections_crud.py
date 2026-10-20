"""Tests for pet detection module — part 3 of 3: TestPetDetectionsCRUD."""

from __future__ import annotations

import pytest


class TestPetDetectionsCRUD:
    """Tests for pet detections CRUD in db/pets.py."""

    def test_upsert_and_get_detections(self, tmp_path):
        from bpp.db.connection import init_db
        from bpp.db.pets import get_pet_detections, upsert_pet_detections
        from bpp.db.photos import upsert_photo

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)

        f = tmp_path / "pet_photo.jpg"
        f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
        photo_id = upsert_photo(conn, {"filepath": str(f)})

        detections = [
            {
                "class": "cat",
                "confidence": 0.92,
                "bbox_x": 10,
                "bbox_y": 20,
                "bbox_w": 100,
                "bbox_h": 80,
            },
            {
                "class": "dog",
                "confidence": 0.85,
                "bbox_x": 200,
                "bbox_y": 30,
                "bbox_w": 120,
                "bbox_h": 90,
            },
        ]
        upsert_pet_detections(conn, photo_id, detections)

        result = get_pet_detections(conn, photo_id)
        assert len(result) == 2
        assert result[0]["class"] == "cat"
        assert result[0]["confidence"] == pytest.approx(0.92)
        assert result[1]["class"] == "dog"
        conn.close()

    def test_upsert_replaces_old_detections(self, tmp_path):
        from bpp.db.connection import init_db
        from bpp.db.pets import get_pet_detections, upsert_pet_detections
        from bpp.db.photos import upsert_photo

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)

        f = tmp_path / "pet2.jpg"
        f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
        photo_id = upsert_photo(conn, {"filepath": str(f)})

        upsert_pet_detections(conn, photo_id, [{"class": "cat", "confidence": 0.9}])
        assert len(get_pet_detections(conn, photo_id)) == 1

        # Replace with new detections
        upsert_pet_detections(conn, photo_id, [{"class": "dog", "confidence": 0.8}])
        result = get_pet_detections(conn, photo_id)
        assert len(result) == 1
        assert result[0]["class"] == "dog"
        conn.close()

    def test_assign_pet_clusters(self, tmp_path):
        from bpp.db.connection import init_db
        from bpp.db.pets import (
            assign_pet_clusters,
            get_pet_detections,
            upsert_pet_detections,
        )
        from bpp.db.photos import upsert_photo

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)

        f = tmp_path / "pet3.jpg"
        f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
        photo_id = upsert_photo(conn, {"filepath": str(f)})

        detections = [
            {"class": "cat", "confidence": 0.9},
            {"class": "dog", "confidence": 0.8},
        ]
        upsert_pet_detections(conn, photo_id, detections)
        assign_pet_clusters(conn)

        result = get_pet_detections(conn, photo_id)
        assert result[0]["cluster_id"] == 0  # cat
        assert result[1]["cluster_id"] == 1  # dog
        conn.close()

    def test_get_pet_clusters(self, tmp_path):
        from bpp.db.connection import init_db
        from bpp.db.pets import (
            assign_pet_clusters,
            get_pet_clusters,
            upsert_pet_detections,
        )
        from bpp.db.photos import upsert_photo

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)

        for i in range(3):
            f = tmp_path / f"cat_{i}.jpg"
            f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
            photo_id = upsert_photo(conn, {"filepath": str(f)})
            upsert_pet_detections(conn, photo_id, [{"class": "cat", "confidence": 0.8 + i * 0.05}])

        assign_pet_clusters(conn)
        clusters = get_pet_clusters(conn)

        assert len(clusters) == 1
        assert clusters[0]["pet_class"] == "cat"
        assert clusters[0]["photo_count"] == 3
        assert clusters[0]["representative"] is not None
        assert len(clusters[0]["filepaths"]) == 3
        conn.close()

    def test_has_pet_data(self, tmp_path):
        from bpp.db.connection import init_db
        from bpp.db.pets import has_pet_data, upsert_pet_detections
        from bpp.db.photos import upsert_photo

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)
        assert has_pet_data(conn) is False

        f = tmp_path / "pet4.jpg"
        f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
        photo_id = upsert_photo(conn, {"filepath": str(f)})
        upsert_pet_detections(conn, photo_id, [{"class": "cat", "confidence": 0.9}])
        assert has_pet_data(conn) is True
        conn.close()

    def test_split_pet_cluster(self, tmp_path):
        from bpp.db.connection import init_db
        from bpp.db.pets import (
            assign_pet_clusters,
            get_pet_clusters,
            get_pet_detections,
            split_pet_cluster,
            upsert_pet_detections,
        )
        from bpp.db.photos import upsert_photo

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)

        # Create 3 cat photos, all in cluster 0
        det_ids = []
        for i in range(3):
            f = tmp_path / f"split_cat_{i}.jpg"
            f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
            pid = upsert_photo(conn, {"filepath": str(f)})
            upsert_pet_detections(conn, pid, [{"class": "cat", "confidence": 0.9}])
            dets = get_pet_detections(conn, pid)
            det_ids.append(dets[0]["id"])

        assign_pet_clusters(conn)

        # Split first detection into a new cluster
        new_cid = split_pet_cluster(conn, [det_ids[0]])
        assert new_cid is not None
        assert new_cid >= 1  # next after 0 (cat cluster)

        clusters = get_pet_clusters(conn)
        cluster_ids = {c["cluster_id"] for c in clusters}
        assert 0 in cluster_ids  # original cat cluster
        assert new_cid in cluster_ids  # new split cluster
        # Original cluster should have 2 photos, new cluster should have 1
        for c in clusters:
            if c["cluster_id"] == 0:
                assert c["photo_count"] == 2
            elif c["cluster_id"] == new_cid:
                assert c["photo_count"] == 1
        conn.close()

    def test_split_pet_cluster_no_match(self, tmp_path):
        from bpp.db.connection import init_db
        from bpp.db.pets import split_pet_cluster

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)

        result = split_pet_cluster(conn, [99999])
        assert result is None
        conn.close()

    def test_merge_pet_clusters(self, tmp_path):
        from bpp.db.connection import init_db
        from bpp.db.pets import (
            assign_pet_clusters,
            get_pet_clusters,
            get_pet_detections,
            merge_pet_clusters,
            split_pet_cluster,
            upsert_pet_detections,
        )
        from bpp.db.photos import upsert_photo

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)

        # Create 3 cats and 1 dog
        cat_det_ids = []
        for i in range(3):
            f = tmp_path / f"merge_cat_{i}.jpg"
            f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
            pid = upsert_photo(conn, {"filepath": str(f)})
            upsert_pet_detections(conn, pid, [{"class": "cat", "confidence": 0.9}])
            dets = get_pet_detections(conn, pid)
            cat_det_ids.append(dets[0]["id"])

        assign_pet_clusters(conn)

        # Split one cat out
        new_cid = split_pet_cluster(conn, [cat_det_ids[0]])
        assert new_cid is not None

        # Merge the new cluster back into cluster 0
        count = merge_pet_clusters(conn, 0, [new_cid])
        assert count == 1

        clusters = get_pet_clusters(conn)
        cat_clusters = [c for c in clusters if c["pet_class"] == "cat"]
        assert len(cat_clusters) == 1
        assert cat_clusters[0]["photo_count"] == 3
        conn.close()

    def test_merge_empty_list(self, tmp_path):
        from bpp.db.connection import init_db
        from bpp.db.pets import merge_pet_clusters

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)

        count = merge_pet_clusters(conn, 0, [])
        assert count == 0
        conn.close()

    def test_dismiss_pet_cluster(self, tmp_path):
        """'Not a pet' moves every detection to CLUSTER_DISMISSED — gone from clusters + chips."""
        from bpp.constants import CLUSTER_DISMISSED
        from bpp.db.connection import init_db
        from bpp.db.pets import (
            assign_pet_clusters,
            dismiss_pet_cluster,
            get_pet_clusters,
            get_pet_detections,
            upsert_pet_detections,
        )
        from bpp.db.photos import upsert_photo

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)

        pids = []
        for i in range(2):
            f = tmp_path / f"dismiss_cat_{i}.jpg"
            f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
            pid = upsert_photo(conn, {"filepath": str(f)})
            upsert_pet_detections(conn, pid, [{"class": "cat", "confidence": 0.9}])
            pids.append(pid)
        assign_pet_clusters(conn)
        assert len(get_pet_clusters(conn)) == 1

        count = dismiss_pet_cluster(conn, 0)
        assert count == 2

        # Cluster gone from the Pets view…
        assert get_pet_clusters(conn) == []
        # …and from photo chips.
        for pid in pids:
            assert get_pet_detections(conn, pid) == [], f"photo {pid} still shows pet chips"
        # Sentinel actually stored (recoverable, not deleted).
        rows = conn.execute(
            "SELECT cluster_id FROM pet_detections WHERE photo_id IN (?, ?)", pids
        ).fetchall()
        assert len(rows) == 2
        assert all(r[0] == CLUSTER_DISMISSED for r in rows)
        conn.close()

    def test_dismiss_rejects_sentinels_and_empty(self, tmp_path):
        from bpp.constants import CLUSTER_DISMISSED, CLUSTER_UNASSIGNED
        from bpp.db.connection import init_db
        from bpp.db.pets import dismiss_pet_cluster

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)

        assert dismiss_pet_cluster(conn, CLUSTER_UNASSIGNED) == 0
        assert dismiss_pet_cluster(conn, CLUSTER_DISMISSED) == 0
        assert dismiss_pet_cluster(conn, 42) == 0  # no such cluster
        conn.close()

    def test_split_creates_per_cluster_album(self, tmp_path):
        """Splitting a pet cluster and refreshing albums creates a per-cluster album."""
        from bpp.db.connection import init_db
        from bpp.db.pets import (
            assign_pet_clusters,
            get_pet_detections,
            split_pet_cluster,
            upsert_pet_detections,
        )
        from bpp.db.photos import upsert_photo
        from bpp.db.smart_albums import refresh_smart_albums

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)

        det_ids = []
        for i in range(3):
            f = tmp_path / f"album_cat_{i}.jpg"
            f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
            pid = upsert_photo(
                conn,
                {
                    "filepath": str(f),
                    "date": f"2024-01-{i + 1:02d}T12:00:00",
                    "date_day": f"2024-01-{i + 1:02d}",
                    "date_month": "2024-01",
                    "pet_count": 1,
                    "has_cat": 1,
                    "has_dog": 0,
                    "blur_score": 0.5,
                    "aggregate_score": 0.5,
                },
            )
            upsert_pet_detections(conn, pid, [{"class": "cat", "confidence": 0.9}])
            dets = get_pet_detections(conn, pid)
            det_ids.append(dets[0]["id"])

        assign_pet_clusters(conn)
        refresh_smart_albums(conn)

        # Split one detection
        split_pet_cluster(conn, [det_ids[0]])
        refresh_smart_albums(conn)

        # Should have 2 pet albums now — one for original cluster, one for new
        albums = conn.execute(
            "SELECT rule_json FROM albums WHERE album_type='smart_pet'"
        ).fetchall()
        assert len(albums) == 2
        conn.close()

    def test_cluster_survives_reanalysis(self, tmp_path):
        """Manual cluster assignments must survive re-analysis upserts."""
        from bpp.db.connection import init_db
        from bpp.db.pets import (
            assign_pet_clusters,
            bulk_upsert_pet_detections,
            get_pet_detections,
            split_pet_cluster,
            upsert_pet_detections,
        )
        from bpp.db.photos import upsert_photo

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)
        # Insert 3 photos with cat detections
        pids = []
        for i in range(3):
            f = tmp_path / f"reanalysis_{i}.jpg"
            f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
            pid = upsert_photo(conn, {"filepath": str(f)})
            pids.append(pid)
            upsert_pet_detections(conn, pid, [{"class": "cat", "confidence": 0.9}])
        assign_pet_clusters(conn)

        # Split first photo's detection into a new cluster
        det0 = get_pet_detections(conn, pids[0])
        new_cid = split_pet_cluster(conn, [det0[0]["id"]])
        assert new_cid is not None
        assert new_cid != 0  # not the default cat cluster

        # Re-analyze all 3 photos (simulates re-analysis)
        items = [(pid, [{"class": "cat", "confidence": 0.95}]) for pid in pids]
        bulk_upsert_pet_detections(conn, items)
        assign_pet_clusters(conn)

        # The split cluster assignment must survive
        det_after = get_pet_detections(conn, pids[0])
        assert det_after[0]["cluster_id"] == new_cid, (
            f"Expected cluster_id={new_cid}, got {det_after[0]['cluster_id']}. "
            "Re-analysis destroyed manual cluster assignment."
        )
        # Other photos should remain in default cluster
        for pid in pids[1:]:
            det = get_pet_detections(conn, pid)
            assert det[0]["cluster_id"] == 0
        conn.close()

    def test_single_upsert_preserves_cluster(self, tmp_path):
        """Single-photo upsert must also preserve cluster assignments."""
        from bpp.db.connection import init_db
        from bpp.db.pets import (
            assign_pet_clusters,
            get_pet_detections,
            split_pet_cluster,
            upsert_pet_detections,
        )
        from bpp.db.photos import upsert_photo

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)
        f = tmp_path / "pet.jpg"
        f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
        pid = upsert_photo(conn, {"filepath": str(f)})
        upsert_pet_detections(conn, pid, [{"class": "cat", "confidence": 0.9}])
        assign_pet_clusters(conn)

        det = get_pet_detections(conn, pid)
        new_cid = split_pet_cluster(conn, [det[0]["id"]])

        # Re-upsert (simulates single-photo re-analysis)
        upsert_pet_detections(conn, pid, [{"class": "cat", "confidence": 0.95}])
        assign_pet_clusters(conn)

        det_after = get_pet_detections(conn, pid)
        assert det_after[0]["cluster_id"] == new_cid
        conn.close()

    def test_next_cluster_id_avoids_sentinel_collision(self, tmp_path):
        """_next_cluster_id must not return 0 or 1, which collide with cat/dog defaults."""
        from bpp.db.connection import init_db
        from bpp.db.pets import _next_cluster_id, upsert_pet_detections

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)
        # Insert a detection that stays unassigned (cluster_id=-1)
        from bpp.db.photos import upsert_photo

        f = tmp_path / "sentinel.jpg"
        f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
        pid = upsert_photo(conn, {"filepath": str(f)})
        upsert_pet_detections(conn, pid, [{"class": "cat", "confidence": 0.9}])
        # Before assign_pet_clusters, all detections are at -1
        nid = _next_cluster_id(conn)
        assert nid >= 2, f"_next_cluster_id returned {nid}, would collide with cat=0 or dog=1"
        conn.close()

    def test_schema_v5_cluster_id_column(self, tmp_path):
        from bpp.db.connection import init_db

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(pet_detections)").fetchall()}
        assert "cluster_id" in cols
        conn.close()

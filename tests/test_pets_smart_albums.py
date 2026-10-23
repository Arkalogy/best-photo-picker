"""Tests for pet detection module — part 2 of 3: TestSmartAlbums."""

from __future__ import annotations


class TestSmartAlbums:
    """Tests for pet-based smart albums."""

    def test_refresh_creates_pet_albums(self, tmp_path):
        from bpp.db.connection import init_db
        from bpp.db.pets import assign_pet_clusters, upsert_pet_detections
        from bpp.db.photos import bulk_upsert_photos, get_photo_id_by_path
        from bpp.db.smart_albums import refresh_smart_albums

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)

        # Create test files and insert photos with pet data
        for i in range(3):
            f = tmp_path / f"cat_{i}.jpg"
            f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)

        photos = [
            {
                "filepath": str(tmp_path / f"cat_{i}.jpg"),
                "date": f"2024-01-{i + 1:02d}T12:00:00",
                "date_day": f"2024-01-{i + 1:02d}",
                "date_month": "2024-01",
                "pet_count": 1,
                "has_cat": 1,
                "has_dog": 0,
                "blur_score": 0.5,
                "aggregate_score": 0.5,
            }
            for i in range(3)
        ]
        bulk_upsert_photos(conn, photos)

        # Insert pet detections and assign clusters (required for cluster-based albums)
        for i in range(3):
            fp = str(tmp_path / f"cat_{i}.jpg")
            pid = get_photo_id_by_path(conn, fp)
            upsert_pet_detections(conn, pid, [{"class": "cat", "confidence": 0.9}])
        assign_pet_clusters(conn)

        refresh_smart_albums(conn)

        # Check that a cat album was created
        row = conn.execute(
            "SELECT id FROM albums WHERE album_type='smart_pet' AND rule_json LIKE '%cat%'"
        ).fetchone()
        assert row is not None

        # Check photos are in the album
        album_id = row[0]
        count = conn.execute(
            "SELECT COUNT(*) FROM album_photos WHERE album_id=?", (album_id,)
        ).fetchone()[0]
        assert count == 3
        conn.close()

    def test_pet_album_rule_uses_singular_class(self, tmp_path):
        """Smart pet albums must store singular pet_class ('cat', 'dog') to match cluster data."""
        import json

        from bpp.db.connection import init_db
        from bpp.db.pets import assign_pet_clusters, upsert_pet_detections
        from bpp.db.photos import bulk_upsert_photos, get_photo_id_by_path
        from bpp.db.smart_albums import refresh_smart_albums

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)

        for i in range(2):
            f = tmp_path / f"cat_{i}.jpg"
            f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)

        photos = [
            {
                "filepath": str(tmp_path / f"cat_{i}.jpg"),
                "date": f"2024-01-{i + 1:02d}T12:00:00",
                "date_day": f"2024-01-{i + 1:02d}",
                "date_month": "2024-01",
                "pet_count": 1,
                "has_cat": 1,
                "has_dog": 0,
                "blur_score": 0.5,
                "aggregate_score": 0.5,
            }
            for i in range(2)
        ]
        bulk_upsert_photos(conn, photos)

        for i in range(2):
            fp = str(tmp_path / f"cat_{i}.jpg")
            pid = get_photo_id_by_path(conn, fp)
            upsert_pet_detections(conn, pid, [{"class": "cat", "confidence": 0.9}])
        assign_pet_clusters(conn)

        refresh_smart_albums(conn)

        row = conn.execute("SELECT rule_json FROM albums WHERE album_type='smart_pet'").fetchone()
        assert row is not None
        rule = json.loads(row[0])
        assert rule["pet_class"] == "cat"  # singular, not "cats"
        conn.close()

    def test_legacy_plural_rule_migrated_to_singular(self, tmp_path):
        """Existing albums with plural pet_class ('cats') get migrated to singular ('cat')."""
        import json

        from bpp.db.connection import init_db
        from bpp.db.photos import bulk_upsert_photos
        from bpp.db.smart_albums import refresh_smart_albums

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)

        for i in range(2):
            f = tmp_path / f"cat_{i}.jpg"
            f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)

        photos = [
            {
                "filepath": str(tmp_path / f"cat_{i}.jpg"),
                "date": f"2024-01-{i + 1:02d}T12:00:00",
                "date_day": f"2024-01-{i + 1:02d}",
                "date_month": "2024-01",
                "pet_count": 1,
                "has_cat": 1,
                "has_dog": 0,
                "blur_score": 0.5,
                "aggregate_score": 0.5,
            }
            for i in range(2)
        ]
        bulk_upsert_photos(conn, photos)

        # Manually insert a legacy album with plural "cats"
        legacy_rule = json.dumps({"pet_class": "cats"}, sort_keys=True)
        conn.execute(
            "INSERT INTO albums (name, album_type, rule_json) VALUES (?, ?, ?)",
            ("Cats", "smart_pet", legacy_rule),
        )
        conn.commit()

        # Refresh should migrate the rule to singular
        refresh_smart_albums(conn)

        rows = conn.execute("SELECT rule_json FROM albums WHERE album_type='smart_pet'").fetchall()
        for row in rows:
            rule = json.loads(row[0])
            assert rule["pet_class"] == "cat", f"Expected singular 'cat', got '{rule['pet_class']}'"
        conn.close()

    def test_pet_class_matches_cluster_data(self, tmp_path):
        """Album pet_class must match what get_pet_clusters returns for navigation to work."""
        import json

        from bpp.db.connection import init_db
        from bpp.db.pets import assign_pet_clusters, get_pet_clusters, upsert_pet_detections
        from bpp.db.photos import bulk_upsert_photos
        from bpp.db.smart_albums import refresh_smart_albums

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)

        for i in range(3):
            f = tmp_path / f"mixed_{i}.jpg"
            f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)

        photos = [
            {
                "filepath": str(tmp_path / f"mixed_{i}.jpg"),
                "date": f"2024-01-{i + 1:02d}T12:00:00",
                "date_day": f"2024-01-{i + 1:02d}",
                "date_month": "2024-01",
                "pet_count": 1,
                "has_cat": 1 if i < 2 else 0,
                "has_dog": 0 if i < 2 else 1,
                "blur_score": 0.5,
                "aggregate_score": 0.5,
            }
            for i in range(3)
        ]
        bulk_upsert_photos(conn, photos)

        # Insert detections and cluster them
        from bpp.db.photos import get_photo_id_by_path

        for i in range(3):
            fp = str(tmp_path / f"mixed_{i}.jpg")
            pid = get_photo_id_by_path(conn, fp)
            cls = "cat" if i < 2 else "dog"
            upsert_pet_detections(conn, pid, [{"class": cls, "confidence": 0.9}])
        assign_pet_clusters(conn)

        refresh_smart_albums(conn)

        # Get cluster pet_class values
        clusters = get_pet_clusters(conn)
        cluster_classes = {c["pet_class"] for c in clusters}

        # Get album rule pet_class values
        album_rows = conn.execute(
            "SELECT rule_json FROM albums WHERE album_type='smart_pet'"
        ).fetchall()
        album_classes = {json.loads(r[0])["pet_class"] for r in album_rows}

        # They must match — this is what broke navigation
        assert cluster_classes == album_classes
        conn.close()

    def test_no_pet_albums_when_no_pets(self, tmp_path):
        from bpp.db.connection import init_db
        from bpp.db.photos import bulk_upsert_photos
        from bpp.db.smart_albums import refresh_smart_albums

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)

        f = tmp_path / "no_pet.jpg"
        f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)

        photos = [
            {
                "filepath": str(f),
                "date": "2024-01-01T12:00:00",
                "date_day": "2024-01-01",
                "date_month": "2024-01",
                "pet_count": 0,
                "has_cat": 0,
                "has_dog": 0,
                "blur_score": 0.5,
                "aggregate_score": 0.5,
            }
        ]
        bulk_upsert_photos(conn, photos)
        refresh_smart_albums(conn)

        row = conn.execute("SELECT COUNT(*) FROM albums WHERE album_type='smart_pet'").fetchone()
        assert row[0] == 0
        conn.close()

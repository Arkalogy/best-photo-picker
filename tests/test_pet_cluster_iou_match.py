"""TDD tests for M-13: pet cluster preservation via IoU matching."""

from __future__ import annotations

import sqlite3

import pytest

from bpp.constants import CLUSTER_UNASSIGNED
from bpp.db.connection import init_db
from bpp.db.pets import bulk_upsert_pet_detections, upsert_pet_detections
from bpp.db.photos import upsert_photo

CAT_BBOX = {"bbox_x": 10, "bbox_y": 10, "bbox_w": 50, "bbox_h": 50}
DOG_BBOX = {"bbox_x": 200, "bbox_y": 200, "bbox_w": 60, "bbox_h": 60}


@pytest.fixture()
def conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


def _insert_photo(conn, fp="/tmp/p.jpg"):
    return upsert_photo(
        conn,
        {
            "filepath": fp,
            "original_filename": "p.jpg",
            "file_size": 100,
            "file_mtime": 1000.0,
        },
    )


class TestIoUClusterPreservation:
    def test_reorder_preserves_cluster_via_iou(self, conn):
        """If detections come back in different order, cluster is preserved by bbox overlap."""
        pid = _insert_photo(conn)

        # First run: cat at (10,10,50,50), dog at (200,200,60,60)
        det_v1 = [
            {"class": "cat", "confidence": 0.9, **CAT_BBOX},
            {"class": "dog", "confidence": 0.8, **DOG_BBOX},
        ]
        upsert_pet_detections(conn, pid, det_v1)

        # Manually assign clusters
        conn.execute(
            "UPDATE pet_detections SET cluster_id=5 WHERE photo_id=? AND class='cat'",
            (pid,),
        )
        conn.execute(
            "UPDATE pet_detections SET cluster_id=7 WHERE photo_id=? AND class='dog'",
            (pid,),
        )
        conn.commit()

        # Second run: SAME bboxes but REVERSED order (dog first, cat second)
        det_v2 = [
            {"class": "dog", "confidence": 0.85, **DOG_BBOX},
            {"class": "cat", "confidence": 0.92, **CAT_BBOX},
        ]
        upsert_pet_detections(conn, pid, det_v2)

        rows = conn.execute(
            "SELECT class, cluster_id FROM pet_detections WHERE photo_id=? ORDER BY class",
            (pid,),
        ).fetchall()
        result = {r["class"]: r["cluster_id"] for r in rows}
        assert result["cat"] == 5, f"Cat cluster should be 5, got {result['cat']}"
        assert result["dog"] == 7, f"Dog cluster should be 7, got {result['dog']}"

    def test_shifted_bbox_still_matches(self, conn):
        """Slightly shifted bbox (high IoU) should still preserve cluster."""
        pid = _insert_photo(conn)

        det_v1 = [
            {
                "class": "cat",
                "confidence": 0.9,
                "bbox_x": 100,
                "bbox_y": 100,
                "bbox_w": 80,
                "bbox_h": 80,
            },
        ]
        upsert_pet_detections(conn, pid, det_v1)
        conn.execute("UPDATE pet_detections SET cluster_id=3 WHERE photo_id=?", (pid,))
        conn.commit()

        # Shifted by 5px — still high IoU
        det_v2 = [
            {
                "class": "cat",
                "confidence": 0.88,
                "bbox_x": 105,
                "bbox_y": 105,
                "bbox_w": 80,
                "bbox_h": 80,
            },
        ]
        upsert_pet_detections(conn, pid, det_v2)

        row = conn.execute(
            "SELECT cluster_id FROM pet_detections WHERE photo_id=?", (pid,)
        ).fetchone()
        assert row["cluster_id"] == 3

    def test_new_detection_gets_unassigned(self, conn):
        """A detection with no bbox overlap gets CLUSTER_UNASSIGNED."""
        pid = _insert_photo(conn)

        det_v1 = [
            {"class": "cat", "confidence": 0.9, **CAT_BBOX},
        ]
        upsert_pet_detections(conn, pid, det_v1)
        conn.execute("UPDATE pet_detections SET cluster_id=3 WHERE photo_id=?", (pid,))
        conn.commit()

        # Completely different location — no overlap
        det_v2 = [
            {"class": "cat", "confidence": 0.9, **CAT_BBOX},
            {
                "class": "dog",
                "confidence": 0.8,
                "bbox_x": 500,
                "bbox_y": 500,
                "bbox_w": 60,
                "bbox_h": 60,
            },
        ]
        upsert_pet_detections(conn, pid, det_v2)

        rows = conn.execute(
            "SELECT class, cluster_id FROM pet_detections WHERE photo_id=? ORDER BY class",
            (pid,),
        ).fetchall()
        result = {r["class"]: r["cluster_id"] for r in rows}
        assert result["cat"] == 3
        assert result["dog"] == CLUSTER_UNASSIGNED

    def test_bulk_upsert_preserves_via_iou(self, conn):
        """bulk_upsert_pet_detections also preserves clusters via IoU."""
        pid = _insert_photo(conn)

        det_v1 = [
            {"class": "cat", "confidence": 0.9, **CAT_BBOX},
        ]
        upsert_pet_detections(conn, pid, det_v1)
        conn.execute("UPDATE pet_detections SET cluster_id=9 WHERE photo_id=?", (pid,))
        conn.commit()

        # Re-insert via bulk with reversed order shouldn't matter (same bbox)
        det_v2 = [
            {"class": "cat", "confidence": 0.92, **CAT_BBOX},
        ]
        bulk_upsert_pet_detections(conn, [(pid, det_v2)])

        row = conn.execute(
            "SELECT cluster_id FROM pet_detections WHERE photo_id=?", (pid,)
        ).fetchone()
        assert row["cluster_id"] == 9

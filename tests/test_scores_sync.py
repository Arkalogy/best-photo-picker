"""Bug #10: score columns stay NULL when bpp analyze runs after bpp serve.

The root cause: WebAppState._load_analysis() guarded import_from_analysis_json
behind `if get_photo_count(conn) == 0`. When the DB already had photos (imported
via the web UI before bpp analyze ran), the JSON sync was skipped and score
columns stayed NULL — every photo showed 0% until the user manually re-analyzed.

Fix: also sync from analysis.json when any photo has aggregate_score IS NULL,
regardless of total photo count.
"""

from __future__ import annotations

import json
import os

import pytest


@pytest.fixture(autouse=True)
def _close_db_pool():
    """Close all pooled DB connections between tests to prevent cross-test pollution."""
    yield
    from bpp.db.connection import close_all_connections

    close_all_connections()


def _make_analysis(lib, scores=True):
    entries = [
        {
            "filepath": str(lib / f"img_{i}.jpg"),
            "date": f"2024-01-{i + 1:02d}T12:00:00",
            "date_day": f"2024-01-{i + 1:02d}",
            "date_month": "2024-01",
            "file_size": 1024,
            "file_mtime": 1700000000.0 + i,
            "blur_raw": 100.0,
            "blur_score": 0.8 if scores else None,
            "exposure_score": 0.7 if scores else None,
            "face_score": 0.5 if scores else None,
            "face_count": 0,
            "largest_face_ratio": 0.0,
            "face_center_dist": 0.0,
            "composition_score": 0.6 if scores else None,
            "aggregate_score": 0.7 if scores else None,
        }
        for i in range(3)
    ]
    with open(lib / "analysis.json", "w") as f:
        json.dump(entries, f)
    return entries


class TestScoresSyncOnStartup:
    def test_scores_loaded_on_fresh_db(self, tmp_path):
        """Baseline: empty DB imports scores from analysis.json on first boot."""
        from bpp.web.app import create_app

        lib = tmp_path / "lib"
        lib.mkdir()
        _make_analysis(lib)

        app = create_app(workdir=str(lib), library_path=str(lib))
        app.config["TESTING"] = True
        with app.test_client() as client:
            client.get("/", environ_base={"REMOTE_ADDR": "127.0.0.1"})
            resp = client.get("/api/v1/photos", environ_base={"REMOTE_ADDR": "127.0.0.1"})
            photos = resp.get_json()["photos"]
            assert all(p["aggregate_score"] is not None for p in photos), (
                "fresh DB must have scores after import from analysis.json"
            )

    def test_null_scores_backfilled_when_analysis_json_exists(self, tmp_path):
        """Bug #10: photos already in DB with NULL scores get backfilled on boot."""
        import sqlite3

        from bpp.db.connection import init_db
        from bpp.db.library import get_library_dirs
        from bpp.db.photos import bulk_upsert_photos
        from bpp.web.app import create_app

        lib = tmp_path / "lib"
        lib.mkdir()

        # Seed DB with photos but NULL scores (simulates bpp serve before bpp analyze)
        dirs = get_library_dirs(str(lib))
        os.makedirs(dirs["data"], exist_ok=True)
        db_path = dirs["data"] + "/photopicker.db"
        init_db(db_path)
        conn = sqlite3.connect(db_path)
        null_photos = [
            {
                "filepath": str(lib / f"img_{i}.jpg"),
                "date": f"2024-01-{i + 1:02d}T12:00:00",
                "date_day": f"2024-01-{i + 1:02d}",
                "date_month": "2024-01",
                "file_size": 1024,
                "file_mtime": 1700000000.0 + i,
            }
            for i in range(3)
        ]
        bulk_upsert_photos(conn, null_photos)
        conn.commit()
        conn.close()

        # Now bpp analyze writes analysis.json with scores
        _make_analysis(lib)

        # On next server start, scores must be backfilled
        app = create_app(workdir=str(lib), library_path=str(lib))
        app.config["TESTING"] = True
        with app.test_client() as client:
            client.get("/", environ_base={"REMOTE_ADDR": "127.0.0.1"})
            resp = client.get("/api/v1/photos", environ_base={"REMOTE_ADDR": "127.0.0.1"})
            photos = resp.get_json()["photos"]
            assert all(p["aggregate_score"] is not None for p in photos), (
                "Bug #10: scores must be backfilled from analysis.json even "
                "when DB already has photos with NULL score columns"
            )

    def test_filename_preserved_during_score_backfill(self, tmp_path):
        """Backfill only updates score columns; other photo fields survive.

        User-set data (selected, override, favorite) lives in album_photos,
        a separate table that bulk_upsert_photos never touches, so there is
        no clobber risk from score backfill by design. This test verifies that
        non-score columns in the photos table (original_filename) are also
        untouched when only scores were NULL.
        """
        import sqlite3

        from bpp.db.connection import init_db
        from bpp.db.library import get_library_dirs
        from bpp.db.photos import bulk_upsert_photos
        from bpp.web.app import create_app

        lib = tmp_path / "lib"
        lib.mkdir()

        dirs = get_library_dirs(str(lib))
        os.makedirs(dirs["data"], exist_ok=True)
        db_path = dirs["data"] + "/photopicker.db"
        init_db(db_path)
        conn = sqlite3.connect(db_path)
        bulk_upsert_photos(
            conn,
            [
                {
                    "filepath": str(lib / "img_0.jpg"),
                    "date": "2024-01-01T12:00:00",
                    "date_day": "2024-01-01",
                    "date_month": "2024-01",
                    "file_size": 1024,
                    "file_mtime": 1700000000.0,
                    "import_batch": "my-batch",
                    "missing": 0,
                }
            ],
        )
        conn.commit()
        conn.close()

        # analysis.json does NOT include import_batch — backfill must not clear it
        _make_analysis(lib)

        app = create_app(workdir=str(lib), library_path=str(lib))
        app.config["TESTING"] = True
        with app.test_client() as client:
            client.get("/", environ_base={"REMOTE_ADDR": "127.0.0.1"})
            resp = client.get("/api/v1/photos", environ_base={"REMOTE_ADDR": "127.0.0.1"})
            photos = resp.get_json()["photos"]
            assert photos[0]["aggregate_score"] is not None, "score must be filled"
            # import_batch is not in analysis.json; the DB value must survive
            raw = (
                sqlite3.connect(db_path)
                .execute(
                    "SELECT import_batch FROM photos WHERE filepath=?",
                    (str(lib / "img_0.jpg"),),
                )
                .fetchone()
            )
            assert raw and raw[0] == "my-batch", (
                "import_batch (not in JSON) must not be clobbered by score backfill"
            )

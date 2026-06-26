"""Tests for DB connection teardown and library switching."""

from __future__ import annotations

import json
import os

import pytest


class TestCloseAllConnections:
    def test_close_clears_thread_local(self, tmp_path):
        from bpp.db.connection import close_all_connections, get_db

        db_path = str(tmp_path / "test.db")
        conn = get_db(db_path)
        assert conn is not None
        # Verify we get the same connection back
        assert get_db(db_path) is conn
        close_all_connections()
        # After close, should get a fresh connection
        conn2 = get_db(db_path)
        assert conn2 is not conn

    def test_close_when_no_connections(self):
        from bpp.db.connection import close_all_connections

        # Should not raise
        close_all_connections()


class TestSwitchLibrary:
    @pytest.fixture
    def two_libraries(self, tmp_path):
        """Create two library dirs with analysis data."""
        lib1 = tmp_path / "lib1"
        lib2 = tmp_path / "lib2"
        lib1.mkdir()
        lib2.mkdir()

        # Seed lib1 with analysis.json (3 photos)
        analysis1 = [
            {
                "filepath": f"{lib1}/img_{i}.jpg",
                "date": f"2024-01-{i + 1:02d}T12:00:00",
                "date_day": f"2024-01-{i + 1:02d}",
                "date_month": "2024-01",
                "file_size": 1024,
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
            for i in range(3)
        ]
        with open(lib1 / "analysis.json", "w") as f:
            json.dump(analysis1, f)

        # Seed lib2 with analysis.json (5 photos)
        analysis2 = [
            {
                "filepath": f"{lib2}/photo_{i}.jpg",
                "date": f"2024-06-{i + 1:02d}T12:00:00",
                "date_day": f"2024-06-{i + 1:02d}",
                "date_month": "2024-06",
                "file_size": 2048,
                "file_mtime": 1700000000.0,
                "blur_raw": 200.0,
                "blur_score": 0.8,
                "exposure_score": 0.7,
                "face_score": 0.4,
                "face_count": 1,
                "largest_face_ratio": 0.05,
                "face_center_dist": 0.3,
                "composition_score": 0.6,
                "aggregate_score": 0.7,
            }
            for i in range(5)
        ]
        with open(lib2 / "analysis.json", "w") as f:
            json.dump(analysis2, f)

        return str(lib1), str(lib2)

    def test_switch_library_changes_db(self, two_libraries):
        from bpp.web.app import create_app

        lib1, lib2 = two_libraries
        app = create_app(workdir=lib1, library_path=lib1)
        app.config["TESTING"] = True
        client = app.test_client()

        # Load lib1
        resp = client.get("/api/v1/photos")
        assert resp.status_code == 200
        data = resp.get_json()
        lib1_count = len(data["photos"])
        assert lib1_count == 3

        # Switch to lib2
        ctx = app.extensions["bpp"]
        ctx.switch_library(lib2)

        # Trigger load
        resp = client.get("/api/v1/photos")
        assert resp.status_code == 200
        data = resp.get_json()
        lib2_count = len(data["photos"])
        assert lib2_count == 5

    def test_switch_library_updates_paths(self, two_libraries):
        from bpp.web.app import create_app

        lib1, lib2 = two_libraries
        app = create_app(workdir=lib1, library_path=lib1)
        ctx = app.extensions["bpp"]

        with app.app_context():
            assert ctx.state["library_path"] == lib1

            ctx.switch_library(lib2)

            assert ctx.state["library_path"] == lib2
            assert ctx.state["workdir"] == os.path.join(lib2, "data")

    def test_switch_library_resets_state(self, two_libraries):
        from bpp.web.app import create_app

        lib1, lib2 = two_libraries
        app = create_app(workdir=lib1, library_path=lib1)
        ctx = app.extensions["bpp"]

        with app.app_context():
            # Load analysis for lib1
            ctx.load_analysis_if_needed()
            assert ctx.state["analysis"] is not None
            assert ctx.thumbs is not None

            # Switch resets everything
            ctx.switch_library(lib2)
            # Analysis from lib1 should be cleared
            # (new lib2 analysis loads via startup)
            photos = ctx.state["analysis"]
            if photos:
                # All should be lib2 photos
                for p in photos:
                    assert "photo_" in p["filepath"]

    def test_switch_cancels_running_workers(self, two_libraries):
        from bpp.web.app import create_app

        lib1, lib2 = two_libraries
        app = create_app(workdir=lib1, library_path=lib1)
        ctx = app.extensions["bpp"]

        with app.app_context():
            # Simulate a running worker with cancel + join support
            cancelled = []
            fake = type(
                "FakeThread",
                (),
                {
                    "is_alive": lambda self: True,
                    "join": lambda self, timeout=None: None,
                },
            )()
            ctx.worker._thread = fake
            ctx.worker.cancel = lambda: cancelled.append(True) or True

            # Switch should succeed — cancels workers instead of refusing
            ctx.switch_library(lib2)
            assert ctx.state["library_path"] == lib2
            assert len(cancelled) == 1, "Worker should have been cancelled"

    def test_switch_drains_thumb_warmer(self, two_libraries):
        """The thumb-warming daemon thread must be cancelled and joined
        before swap, otherwise the old library's warmer can still write
        into the new library's ctx.thumbs after switch_library returns."""
        import threading
        import time

        from bpp.web.app import create_app

        lib1, lib2 = two_libraries
        app = create_app(workdir=lib1, library_path=lib1)
        ctx = app.extensions["bpp"]

        # Replace the warm thread with a long-running fake that respects
        # the cancel event. Simulates a slow disk warming an old DB.
        warmer_observed_cancel = threading.Event()

        def slow_warm():
            while not ctx._cancel_warm.is_set():
                time.sleep(0.01)
            warmer_observed_cancel.set()

        slow_thread = threading.Thread(target=slow_warm, daemon=True)
        ctx._warm_thread = slow_thread
        ctx._cancel_warm.clear()
        slow_thread.start()

        with app.app_context():
            ctx.switch_library(lib2)

        # The warmer must have observed the cancel signal and exited.
        # If switch_library didn't drain it, the slow_warm loop would
        # spin forever and warmer_observed_cancel would stay clear.
        assert warmer_observed_cancel.is_set(), (
            "switch_library must signal _cancel_warm so the old warmer drains"
        )
        # And the slow thread must be dead (joined). The handle on
        # ctx._warm_thread is now whatever startup() spawned for the
        # new library — that's expected, just verify it's not the
        # original slow one.
        assert not slow_thread.is_alive()

"""Tests for background workers — state machine, progress reporting, error handling."""

from __future__ import annotations

import os
import queue
import random

import pytest

# AnalyzeWorker tests spawn a scoring subprocess that imports the entire ML
# stack (cv2, dlib, face_recognition, onnxruntime, CLIP). On free GitHub
# Actions runners that import takes 3-4 minutes per spawn — even with 180s
# join timeouts the test hits the deadline before the worker reports. They
# pass deterministically locally in <60s.
_SKIP_SLOW_ANALYZE = pytest.mark.skipif(
    os.environ.get("CI") == "true",
    reason="AnalyzeWorker subprocess startup exceeds 3 min on CI free runners",
)


class TestAnalyzeWorkerLifecycle:
    """Test AnalyzeWorker state transitions and progress reporting."""

    def test_initial_state(self):
        from bpp.web.analyze_worker import AnalyzeWorker

        w = AnalyzeWorker()
        assert not w.is_alive
        assert not w.running
        assert w.results is None

    def test_empty_dir_reports_zero(self, tmp_path):
        from bpp.web.analyze_worker import AnalyzeWorker

        input_dir = str(tmp_path / "empty")
        os.makedirs(input_dir)
        workdir = str(tmp_path / "work")
        os.makedirs(workdir)

        w = AnalyzeWorker()
        started = w.start(input_dir, workdir, {"max_long_side": 1024}, [".jpg", ".png"])
        assert started
        w._thread.join(timeout=10)

        msgs = _drain_queue(w.progress_queue)
        types = [m["type"] for m in msgs]
        assert "start" in types
        assert "done" in types
        done = next(m for m in msgs if m["type"] == "done")
        assert done["total"] == 0

    def test_cannot_start_twice(self, tmp_path):
        """`start()` must reject a second call while the worker is busy.

        Empty input dirs make the worker exit microseconds after start —
        so we override `_run` to block on a Event, deterministically
        keeping the worker 'alive' until the test releases it. Without
        this synchronization the test is racy under parallel xdist load
        (worker exits before the second start() probe).
        """
        import threading

        from bpp.web.analyze_worker import AnalyzeWorker

        block = threading.Event()

        class BlockingWorker(AnalyzeWorker):
            def _run(self, *a, **kw):
                block.wait(timeout=5)

        input_dir = str(tmp_path / "empty")
        os.makedirs(input_dir)
        workdir = str(tmp_path / "work")
        os.makedirs(workdir)

        w = BlockingWorker()
        try:
            assert w.start(input_dir, workdir, {"max_long_side": 1024}, [".jpg"])
            second = w.start(input_dir, workdir, {"max_long_side": 1024}, [".jpg"])
            assert not second
        finally:
            block.set()
            if w._thread is not None:
                w._thread.join(timeout=10)

    def test_cancel(self, tmp_path):
        from bpp.web.analyze_worker import AnalyzeWorker

        input_dir = str(tmp_path / "empty")
        os.makedirs(input_dir)
        workdir = str(tmp_path / "work")
        os.makedirs(workdir)

        w = AnalyzeWorker()
        # Can't cancel if not running
        assert not w.cancel()

    def test_error_message_sanitized(self, tmp_path):
        """Error messages sent to the progress queue should not contain raw exceptions."""
        from bpp.web.analyze_worker import AnalyzeWorker

        # Point to a non-existent file to trigger an error
        workdir = str(tmp_path / "work")
        os.makedirs(workdir)

        w = AnalyzeWorker()
        # Use a file that's not a valid archive
        fake_file = tmp_path / "bad.zip"
        fake_file.write_text("not a zip")
        w.start(str(fake_file), workdir, {"max_long_side": 1024}, [".jpg"])
        w._thread.join(timeout=10)

        msgs = _drain_queue(w.progress_queue)
        error_msgs = [m for m in msgs if m.get("type") == "error"]
        for msg in error_msgs:
            # Should not contain filesystem paths or raw exception strings
            assert "Traceback" not in msg["message"]
            # Error messages should be generic, not contain local paths
            assert "Check server logs" in msg["message"] or "Unsupported" in msg["message"]


class TestImportWorkerLifecycle:
    """Test ImportWorker state transitions."""

    def test_initial_state(self):
        from bpp.web.import_worker import ImportWorker

        w = ImportWorker()
        assert not w.is_alive
        assert not w.running

    def test_cannot_start_twice(self, tmp_path):
        """Same race-fix pattern as TestAnalyzeWorkerLifecycle: empty
        source → fast exit → second start() races. Block on an Event
        to make it deterministic."""
        import threading

        from bpp.web.import_worker import ImportWorker

        block = threading.Event()

        class BlockingWorker(ImportWorker):
            def _run(self, *a, **kw):
                block.wait(timeout=5)

        src = str(tmp_path / "src")
        lib = str(tmp_path / "lib")
        work = str(tmp_path / "work")
        for d in (src, lib, work):
            os.makedirs(d)

        w = BlockingWorker()
        try:
            assert w.start(src, lib, work, {"max_long_side": 1024}, [".jpg"])
            second = w.start(src, lib, work, {"max_long_side": 1024}, [".jpg"])
            assert not second
        finally:
            block.set()
            if w._thread is not None:
                w._thread.join(timeout=10)

    def test_empty_source_reports_done(self, tmp_path):
        from bpp.web.import_worker import ImportWorker

        src = str(tmp_path / "src")
        lib = str(tmp_path / "lib")
        work = str(tmp_path / "work")
        for d in (src, lib, work):
            os.makedirs(d)

        w = ImportWorker()
        w.start(src, lib, work, {"max_long_side": 1024}, [".jpg"])
        w._thread.join(timeout=10)

        msgs = _drain_queue(w.progress_queue)
        types = [m["type"] for m in msgs]
        assert "done" in types

    def test_pet_save_uses_batched_lookup_not_per_item(self):
        """R8-H7: import_worker must batch the filepath→photo_id
        lookup with `get_photo_id_map_by_paths` instead of calling
        `get_photo_by_path` per item. With 1000+ photos imported in
        one batch, the per-item version is 1000 extra DB round-trips
        in a tight loop. Source-scan: ensure the import path
        references the batched API and not the per-item one."""
        from pathlib import Path

        worker_src = Path("bpp/web/import_worker.py").read_text()

        # The batched lookup must be present in the pet-save section
        assert "get_photo_id_map_by_paths" in worker_src, (
            "import_worker must use batched filepath→id lookup "
            "(get_photo_id_map_by_paths) for pet save"
        )
        # The per-item lookup must be ABSENT — that's the regression
        # we're guarding against
        assert "get_photo_by_path" not in worker_src, (
            "import_worker still references the per-item lookup "
            "`get_photo_by_path` — N+1 DB call regression"
        )

    def test_pet_clustering_refreshes_smart_pet_album(self):
        """R8-M9: after clustering pets in import, the pet smart
        album must be refreshed so the UI sidebar reflects the new
        clusters immediately. Source-scan: assert the refresh call
        is wired alongside `assign_pet_clusters`."""
        from pathlib import Path

        worker_src = Path("bpp/web/import_worker.py").read_text()

        assert "refresh_smart_albums" in worker_src, (
            "import_worker must refresh smart albums after pet clustering"
        )
        # Scope: the refresh must go through get_affected_album_types("import")
        # rather than a full sweep. The registry guarantees that domain still
        # maps to ALBUM_PET, so the contract ("only refresh pet album") holds
        # without hardcoding the literal in import_worker.
        assert 'get_affected_album_types("import")' in worker_src, (
            "import_worker must scope the refresh via get_affected_album_types('import') "
            "(R4-M / cheaper than the full sweep)"
        )
        from bpp.db.smart_albums import ALBUM_PET, get_affected_album_types

        assert get_affected_album_types("import") == (ALBUM_PET,), (
            "import domain must still map to (ALBUM_PET,) — if you changed the registry, "
            "update this test too"
        )
        # Structurally: the refresh must be inside the same block
        # as `assign_pet_clusters`. Find both and check ordering.
        assign_idx = worker_src.find("assign_pet_clusters(conn)")
        refresh_idx = worker_src.find("refresh_smart_albums")
        assert assign_idx > 0 and refresh_idx > 0
        assert refresh_idx > assign_idx, (
            "refresh_smart_albums must come AFTER assign_pet_clusters; "
            "refreshing before clustering would render stale data"
        )


class TestFaceWorkerLifecycle:
    """Test FaceWorker state transitions."""

    def test_initial_state(self):
        from bpp.web.face_worker import FaceWorker

        w = FaceWorker()
        assert not w.is_alive
        assert not w.running

    def test_empty_analysis_reports_done(self, tmp_path):
        from bpp.web.face_worker import FaceWorker

        w = FaceWorker()
        db_path = str(tmp_path / "photopicker.db")
        started = w.start([], db_path, {"max_long_side": 1024})
        assert started
        w._thread.join(timeout=10)

        msgs = _drain_queue(w.progress_queue)
        types = [m["type"] for m in msgs]
        assert "done" in types
        done = next(m for m in msgs if m["type"] == "done")
        assert done["total"] == 0
        assert done["faces_found"] == 0

    def test_cannot_start_twice(self, tmp_path):
        from bpp.web.face_worker import FaceWorker

        w = FaceWorker()
        db_path = str(tmp_path / "photopicker.db")
        w.start([], db_path, {"max_long_side": 1024})
        second = w.start([], db_path, {"max_long_side": 1024})
        assert not second
        w._thread.join(timeout=10)


class TestClipWorkerLifecycle:
    """Test ClipWorker state transitions."""

    def test_initial_state(self):
        from bpp.web.clip_worker import ClipWorker

        w = ClipWorker()
        assert not w.is_alive
        assert not w.running

    def test_cannot_start_twice(self, tmp_path):
        from bpp.web.clip_worker import ClipWorker

        w = ClipWorker()
        # Need a DB with photos table for ClipWorker
        import sqlite3

        db_path = str(tmp_path / "photopicker.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE IF NOT EXISTS photos (id INTEGER PRIMARY KEY, filepath TEXT)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS clip_embeddings "
            "(id INTEGER PRIMARY KEY, photo_id INTEGER, model_name TEXT, embedding BLOB)"
        )
        conn.commit()
        conn.close()

        dummy = [{"filepath": "/fake/photo.jpg"}]
        w.start(dummy, db_path)
        second = w.start(dummy, db_path)
        assert not second
        w._thread.join(timeout=10)

    def test_empty_analysis_reports_done(self, tmp_path):
        import sqlite3

        from bpp.db.schema import create_tables
        from bpp.web.clip_worker import ClipWorker

        db_path = str(tmp_path / "photopicker.db")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        create_tables(conn)
        conn.close()

        w = ClipWorker()
        w.start([], db_path)
        w._thread.join(timeout=10)

        msgs = _drain_queue(w.progress_queue)
        types = [m["type"] for m in msgs]
        assert "done" in types
        done = next(m for m in msgs if m["type"] == "done")
        assert done["total"] == 0


class TestAnalyzeWorkerHappyPath:
    """Test AnalyzeWorker with real images."""

    @_SKIP_SLOW_ANALYZE
    def test_analyze_single_image(self, tmp_path):
        from bpp.demo.generate import _sunset_gradient
        from bpp.web.analyze_worker import AnalyzeWorker

        input_dir = str(tmp_path / "photos")
        os.makedirs(input_dir)
        img = _sunset_gradient(random.Random(42))
        img.save(os.path.join(input_dir, "test.jpg"), "JPEG")

        workdir = str(tmp_path / "work")
        os.makedirs(workdir)

        w = AnalyzeWorker()
        w.start(input_dir, workdir, {"max_long_side": 512}, [".jpg"])
        w._thread.join(timeout=90)

        msgs = _drain_queue(w.progress_queue)
        types = [m["type"] for m in msgs]
        assert "start" in types
        assert "progress" in types
        assert "done" in types
        done = next(m for m in msgs if m["type"] == "done")
        assert done["total"] == 1
        assert done["processed"] == 1
        assert w.results is not None
        assert len(w.results) == 1
        assert w.results[0]["aggregate_score"] > 0

    @_SKIP_SLOW_ANALYZE
    def test_analyze_cancel_mid_run(self, tmp_path):
        from bpp.demo.generate import _sunset_gradient
        from bpp.web.analyze_worker import AnalyzeWorker

        input_dir = str(tmp_path / "photos")
        os.makedirs(input_dir)
        # Create enough images that cancel has time to fire
        for i in range(20):
            img = _sunset_gradient(random.Random(42))
            img.save(os.path.join(input_dir, f"img_{i:03d}.jpg"), "JPEG")

        workdir = str(tmp_path / "work")
        os.makedirs(workdir)

        w = AnalyzeWorker()
        w.start(input_dir, workdir, {"max_long_side": 256}, [".jpg"])
        # Wait briefly for processing to begin, then cancel
        import time

        time.sleep(0.3)
        if w.is_alive:
            cancelled = w.cancel()
            assert cancelled
        w._thread.join(timeout=90)

        msgs = _drain_queue(w.progress_queue)
        types = [m["type"] for m in msgs]
        # Either cancelled or finished before we could cancel
        assert "cancelled" in types or "done" in types


class TestImportWorkerHappyPath:
    """Test ImportWorker with real images."""

    def test_import_and_analyze(self, tmp_path):
        from bpp.demo.generate import _bokeh_circles
        from bpp.web.import_worker import ImportWorker

        src = str(tmp_path / "src")
        lib = str(tmp_path / "lib")
        work = str(tmp_path / "work")
        for d in (src, lib, work):
            os.makedirs(d)

        img = _bokeh_circles(random.Random(42))
        img.save(os.path.join(src, "photo.jpg"), "JPEG")

        w = ImportWorker()
        w.start(src, lib, work, {"max_long_side": 512}, [".jpg"])
        w._thread.join(timeout=90)

        msgs = _drain_queue(w.progress_queue)
        types = [m["type"] for m in msgs]
        assert "phase" in types
        assert "import_done" in types
        assert "done" in types

        import_done = next(m for m in msgs if m["type"] == "import_done")
        assert import_done["imported"] == 1
        assert import_done["skipped"] == 0

        done = next(m for m in msgs if m["type"] == "done")
        assert done["analyzed"] == 1

    def test_import_cancel(self, tmp_path):
        from bpp.demo.generate import _bokeh_circles
        from bpp.web.import_worker import ImportWorker

        src = str(tmp_path / "src")
        lib = str(tmp_path / "lib")
        work = str(tmp_path / "work")
        for d in (src, lib, work):
            os.makedirs(d)

        # Create enough images for cancel to fire
        for i in range(20):
            img = _bokeh_circles(random.Random(i))
            img.save(os.path.join(src, f"img_{i:03d}.jpg"), "JPEG")

        w = ImportWorker()
        w.start(src, lib, work, {"max_long_side": 256}, [".jpg"])

        import time

        time.sleep(0.3)
        if w.is_alive:
            assert w.cancel()
        w._thread.join(timeout=90)

        msgs = _drain_queue(w.progress_queue)
        types = [m["type"] for m in msgs]
        assert "cancelled" in types or "done" in types


def _drain_queue(q: queue.Queue) -> list[dict]:
    """Drain all messages from a queue into a list."""
    msgs = []
    while not q.empty():
        try:
            msgs.append(q.get_nowait())
        except queue.Empty:
            break
    return msgs

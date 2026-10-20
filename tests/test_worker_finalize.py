"""Workers finish their own bookkeeping; the progress stream is optional.

An import or analyze can run for minutes. If the tab that started it is
gone by the end, nobody consumes the progress stream, so the stream's
on-done hook never runs. Each worker therefore finalizes in its own
thread, reaching the app state through the ``ctx`` the pool gave it, and
a failed final write is reported, not just logged.
"""

from __future__ import annotations

import os
import queue

import pytest
from PIL import Image


def _drain(q: queue.Queue) -> list[dict]:
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


@pytest.mark.usefixtures("offline_downloads")
class TestImportFinalizesInTheWorker:
    def test_the_pool_gives_every_worker_its_app_state(self, tmp_path):
        from bpp.web.app import create_app

        app = create_app(library_path=str(tmp_path / "lib"))
        ctx = app.extensions["bpp"]
        assert all(w.ctx is ctx for w in ctx.workers.values())

    def test_an_unwatched_import_still_lands_in_the_library_view(self, tmp_path):
        from bpp.db.settings import get_setting, set_setting
        from bpp.web.app import create_app

        lib = str(tmp_path / "lib")
        src = str(tmp_path / "src")
        os.makedirs(src)
        for i in range(2):
            Image.new("RGB", (48, 48), (40 * i, 90, 120)).save(
                os.path.join(src, f"p{i}.jpg"), "JPEG"
            )
        app = create_app(library_path=lib)
        ctx = app.extensions["bpp"]
        with app.app_context():
            set_setting(ctx.get_conn(), "first_run", "true")
            w = ctx.workers["import"]
            assert w.start(src, lib, ctx.workdir, {"max_long_side": 256}, [".jpg"])
            w._thread.join(timeout=180)
            msgs = _drain(w.progress_queue)
            assert any(m["type"] == "done" for m in msgs), msgs
            # Nobody read the stream's on-done hook, yet the view is current
            # and first-run is over.
            assert ctx.state["analysis"] is not None
            assert len(ctx.state["analysis"]) == 2
            assert get_setting(ctx.get_conn(), "first_run") is None


class TestAnalyzeReportsAFailedFinalWrite:
    def test_a_failed_save_is_a_warning_and_marked_on_done(self, tmp_path):
        from unittest.mock import patch

        from bpp.web.analyze_worker import AnalyzeWorker

        photos = tmp_path / "photos"
        photos.mkdir()
        photo = str(photos / "a.jpg")
        Image.new("RGB", (32, 32), "green").save(photo, "JPEG")
        workdir = tmp_path / "work"
        workdir.mkdir()
        scored = [
            {
                "filepath": photo,
                "date": "2024-01-01T00:00:00",
                "file_size": 1,
                "file_mtime": 1.0,
                "blur_raw": 100.0,
                "blur_score": 0.5,
                "exposure_score": 0.5,
                "face_score": 0.5,
                "composition_score": 0.5,
                "face_count": 0,
            }
        ]
        worker = AnalyzeWorker()
        with (
            patch.object(AnalyzeWorker, "_score", return_value=scored),
            patch(
                "bpp.web.analyze_worker.store_scoring_results",
                side_effect=RuntimeError("disk full"),
            ),
        ):
            worker.start(
                input_dir=str(photos),
                workdir=str(workdir),
                config={"max_long_side": 256},
                extensions=[".jpg"],
            )
            worker._thread.join(timeout=60)
        msgs = _drain(worker.progress_queue)
        warnings = [m for m in msgs if m["type"] == "warning"]
        assert any("saved" in m["message"] for m in warnings), msgs
        done = next(m for m in msgs if m["type"] == "done")
        assert done["db_write_failed"] is True


class TestTheStreamBackstopDoesNotRepeatTheWorker:
    def test_a_done_message_the_worker_finalized_is_left_alone(self, tmp_path):
        from unittest.mock import MagicMock

        from bpp.web.bp_analysis import _finalize_import

        ctx = MagicMock()
        _finalize_import({"type": "done", "finalized": True}, ctx)
        ctx.invalidate_analysis.assert_not_called()
        _finalize_import({"type": "done"}, ctx)
        ctx.invalidate_analysis.assert_called_once()

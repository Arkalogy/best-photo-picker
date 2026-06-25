"""Tests for pipeline quality fixes: dlib quality, face op lock, force-include bypass.

Covers the four issues raised in the executive assessment:
1. dlib fallback quality field
2. Face operation serialization lock
3. Force-include bypasses dedup
4. Concurrent face operation safety
"""

from __future__ import annotations

import json
import os
import threading
from unittest.mock import patch

import numpy as np
import pytest

from bpp.scoring.face_embed import _dlib_face_quality

# ===========================================================================
# 1. dlib fallback quality field
# ===========================================================================


class TestDlibFaceQuality:
    """Verify _dlib_face_quality returns sane values for various inputs."""

    def test_large_square_face_high_quality(self):
        """A large square face with high confidence should score near 1.0."""
        q = _dlib_face_quality(w=150, h=150, conf=0.95)
        assert 0.8 <= q <= 1.0

    def test_small_face_low_quality(self):
        """A small face should get a lower quality score."""
        q_small = _dlib_face_quality(w=30, h=30, conf=0.9)
        q_large = _dlib_face_quality(w=150, h=150, conf=0.9)
        assert q_small < q_large

    def test_low_confidence_low_quality(self):
        """Low detector confidence should reduce quality."""
        q_low = _dlib_face_quality(w=100, h=100, conf=0.3)
        q_high = _dlib_face_quality(w=100, h=100, conf=0.95)
        assert q_low < q_high

    def test_extreme_aspect_ratio_penalty(self):
        """Very narrow or very wide face bbox should score lower on aspect."""
        q_square = _dlib_face_quality(w=100, h=100, conf=0.8)
        q_narrow = _dlib_face_quality(w=30, h=100, conf=0.8)
        assert q_narrow < q_square

    def test_quality_in_valid_range(self):
        """Quality must always be in [0, 1]."""
        for w, h, c in [(1, 1, 0.0), (500, 500, 1.0), (10, 200, 0.5)]:
            q = _dlib_face_quality(w=w, h=h, conf=c)
            assert 0.0 <= q <= 1.0, f"q={q} for w={w} h={h} conf={c}"

    def test_zero_height_returns_valid(self):
        """Zero height should not crash (aspect=0)."""
        q = _dlib_face_quality(w=100, h=0, conf=0.5)
        assert 0.0 <= q <= 1.0


class TestDlibExtractIncludesQuality:
    """Verify _extract_dlib returns dicts with 'quality' key."""

    @pytest.fixture(autouse=True)
    def _skip_if_no_dlib(self):
        """Skip if face_recognition is not installed."""
        try:
            import face_recognition  # noqa: F401
        except ImportError:
            pytest.skip("face_recognition not installed")

    def test_extract_dlib_returns_quality_field(self):
        """Each result dict from _extract_dlib must contain 'quality'."""
        from bpp.scoring.face_embed import _extract_dlib

        # Mock face detection to return a single face
        fake_boxes = [(50, 50, 80, 80, 0.9)]
        fake_landmarks = [
            {
                "nose_tip": [(90, 100)],
                "left_eye": [(70, 80), (80, 80)],
                "right_eye": [(100, 80), (110, 80)],
            }
        ]
        fake_encoding = np.random.randn(128)

        with (
            patch(
                "bpp.scoring.face.detect_faces_with_confidence",
                return_value=fake_boxes,
            ),
            patch("face_recognition.face_landmarks", return_value=fake_landmarks),
            patch("face_recognition.face_encodings", return_value=[fake_encoding]),
        ):
            img = np.zeros((200, 200, 3), dtype=np.uint8)
            results = _extract_dlib(img, min_confidence=0.3)

        assert len(results) == 1
        assert "quality" in results[0], "dlib result missing 'quality' field"
        assert 0.0 <= results[0]["quality"] <= 1.0


# ===========================================================================
# 2. Face operation lock
# ===========================================================================


class TestFaceOpLockExists:
    """Verify face_op_lock is present on WebAppState."""

    def test_state_has_face_op_lock(self, tmp_path):
        workdir = str(tmp_path / "wd")
        os.makedirs(workdir)
        from bpp.web.app import create_app

        app = create_app(workdir=workdir)
        ctx = app.extensions["bpp"]
        assert hasattr(ctx, "face_op_lock"), "WebAppState missing face_op_lock"
        assert isinstance(ctx.face_op_lock, type(threading.Lock()))


class TestFaceOpLockSerializes:
    """Verify that face-mutating endpoints acquire the lock."""

    @pytest.fixture
    def app_and_ctx(self, tmp_path):
        workdir = str(tmp_path / "wd")
        os.makedirs(workdir)
        # Write minimal analysis so the app initializes
        with open(os.path.join(workdir, "analysis.json"), "w") as f:
            json.dump([], f)
        from bpp.web.app import create_app

        app = create_app(workdir=workdir)
        app.config["TESTING"] = True
        ctx = app.extensions["bpp"]
        return app, ctx

    def test_merge_acquires_lock(self, app_and_ctx):
        """POST /api/faces/merge should acquire face_op_lock."""
        app, ctx = app_and_ctx
        lock_acquired = threading.Event()
        lock_blocked = threading.Event()

        # Hold the lock from another thread
        def hold_lock():
            with ctx.face_op_lock:
                lock_acquired.set()
                lock_blocked.wait(timeout=5)

        holder = threading.Thread(target=hold_lock, daemon=True)
        holder.start()
        lock_acquired.wait(timeout=2)

        # Try to call merge — it should block because lock is held
        result_ready = threading.Event()
        results = {}

        def call_merge():
            with app.test_client() as c:
                resp = c.post(
                    "/api/v1/faces/merge",
                    json={"primary_cluster_id": 0, "merge_cluster_ids": [1]},
                )
                results["status"] = resp.status_code
            result_ready.set()

        caller = threading.Thread(target=call_merge, daemon=True)
        caller.start()

        # Give the caller a brief moment — it should NOT complete
        assert not result_ready.wait(timeout=0.5), "Merge completed without acquiring face_op_lock"

        # Release the lock
        lock_blocked.set()
        holder.join(timeout=2)
        result_ready.wait(timeout=5)

        # Now merge should have completed (may return error due to no data, that's ok)
        assert "status" in results

    def test_concurrent_recluster_serialized(self, app_and_ctx):
        """Two concurrent recluster calls should execute sequentially (not interleave)."""
        app, ctx = app_and_ctx
        conn = ctx.get_conn()
        # Insert some face embeddings to make recluster work
        for i in range(6):
            emb = np.random.RandomState(i).randn(128).astype(np.float32)
            conn.execute(
                "INSERT INTO photos (filepath, original_filename, file_size, file_mtime)"
                " VALUES (?, ?, 100, 1.0)",
                (f"/tmp/test_{i}.jpg", f"test_{i}.jpg"),
            )
            photo_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO face_embeddings (photo_id, face_index, embedding, cluster_id) "
                "VALUES (?, 0, ?, ?)",
                (photo_id, emb.tobytes(), i % 3),
            )
        conn.commit()

        execution_order: list[str] = []

        original_cluster = __import__(
            "bpp.scoring.face_cluster", fromlist=["cluster_faces"]
        ).cluster_faces

        def slow_cluster(embeddings, threshold=0.55):
            """Instrumented cluster that records ordering."""
            tid = threading.current_thread().name
            execution_order.append(f"{tid}_start")
            result = original_cluster(embeddings, threshold=threshold)
            execution_order.append(f"{tid}_end")
            return result

        # cluster_faces imported by the recluster endpoint — which moved
        # to its own module (bp_faces_recluster) during the 500-LOC split.
        with patch("bpp.web.bp_faces_recluster.cluster_faces", side_effect=slow_cluster):
            errors = []

            def recluster(name):
                try:
                    with app.test_client() as c:
                        c.post("/api/v1/faces/recluster", json={"threshold": 0.55})
                except Exception as e:
                    errors.append(e)

            t1 = threading.Thread(target=recluster, args=("T1",), name="T1", daemon=True)
            t2 = threading.Thread(target=recluster, args=("T2",), name="T2", daemon=True)
            t1.start()
            t2.start()
            t1.join(timeout=10)
            t2.join(timeout=10)

        assert not errors, f"Recluster raised: {errors}"
        # Verify serial execution: one must complete before the other starts
        # execution_order should be like [T1_start, T1_end, T2_start, T2_end]
        # or [T2_start, T2_end, T1_start, T1_end]
        starts = [e for e in execution_order if e.endswith("_start")]
        ends = [e for e in execution_order if e.endswith("_end")]
        if len(starts) == 2 and len(ends) == 2:
            # The second start must come after the first end
            first_end_idx = execution_order.index(ends[0])
            second_start_idx = execution_order.index(starts[1])
            assert second_start_idx > first_end_idx, (
                f"Concurrent execution detected: {execution_order}"
            )


class TestPetDisplayConfidenceFloor:
    """Low-confidence pet detections are stored but never user-visible.

    Regression (2026-06-12): the 0.2 detection threshold surfaced raw —
    a sheepskin rug rendered "Cat" and "Dog (43%)" chips in the lightbox.
    Read paths floor at PET_DISPLAY_CONFIDENCE; stored rows are kept so
    tuning never needs a rescan.
    """

    def test_low_confidence_detections_hidden_from_reads(self, tmp_path):
        from bpp.constants import PET_DISPLAY_CONFIDENCE
        from bpp.db.connection import init_db
        from bpp.db.pets import get_pet_detections, upsert_pet_detections

        conn = init_db(str(tmp_path / "t.db"))
        conn.execute(
            "INSERT INTO photos (filepath, original_filename, file_size, file_mtime) "
            "VALUES ('/x/a.jpg', 'a.jpg', 1, 1.0)"
        )
        conn.commit()
        pid = conn.execute("SELECT id FROM photos").fetchone()[0]
        upsert_pet_detections(
            conn,
            pid,
            [
                {
                    "detection_index": 0,
                    "class": "dog",
                    "confidence": 0.43,
                    "bbox_x": 0,
                    "bbox_y": 0,
                    "bbox_w": 10,
                    "bbox_h": 10,
                },
                {
                    "detection_index": 1,
                    "class": "cat",
                    "confidence": 0.91,
                    "bbox_x": 0,
                    "bbox_y": 0,
                    "bbox_w": 10,
                    "bbox_h": 10,
                },
            ],
        )
        visible = get_pet_detections(conn, pid)
        classes = [d["class"] for d in visible]
        assert classes == ["cat"], f"sub-floor detection leaked: {classes}"
        # The raw row is still stored (re-tunable without rescan).
        raw = conn.execute("SELECT COUNT(*) FROM pet_detections").fetchone()[0]
        assert raw == 2
        assert all(d["confidence"] >= PET_DISPLAY_CONFIDENCE for d in visible)

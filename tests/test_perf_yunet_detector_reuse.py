"""The YuNet detector is built once per thread and resized in place.

Every photo has its own dimensions, so a detector that is rebuilt (and
the model file re-hashed) whenever the size changes is rebuilt for
nearly every photo of an analysis run.
"""

from __future__ import annotations

import threading

import numpy as np


def _stub_yunet(monkeypatch):
    import cv2

    from bpp.scoring import face_yunet as fy

    created: list[tuple[int, int]] = []
    resized: list[tuple[int, int]] = []
    ensured: list[int] = []

    class _Det:
        def setInputSize(self, size):
            resized.append(tuple(size))

        def detect(self, img):
            return None, None

    class _YN:
        @staticmethod
        def create(path, cfg, size, **kw):
            created.append(tuple(size))
            return _Det()

    monkeypatch.setattr(cv2, "FaceDetectorYN", _YN)
    monkeypatch.setattr(fy, "_ensure_yunet_model", lambda: ensured.append(1) or True)
    monkeypatch.setattr(fy, "_yunet_tls", threading.local())
    monkeypatch.setattr(fy, "_YUNET_AVAILABLE", None)
    monkeypatch.setattr(fy, "_yunet_generation", 0)
    return fy, created, resized, ensured


def test_a_size_change_resizes_the_detector_instead_of_rebuilding_it(monkeypatch):
    fy, created, resized, ensured = _stub_yunet(monkeypatch)
    fy._yunet_detect(np.zeros((100, 200, 3), np.uint8))
    fy._yunet_detect(np.zeros((300, 400, 3), np.uint8))
    fy._yunet_detect(np.zeros((300, 400, 3), np.uint8))
    fy._yunet_detect_raw(np.zeros((100, 200, 3), np.uint8))
    assert created == [(200, 100)], "one detector per thread"
    assert resized == [(400, 300), (200, 100)]
    assert len(ensured) == 1, "the model file is verified once per process"


def test_another_thread_gets_its_own_detector_without_re_verifying(monkeypatch):
    fy, created, _resized, ensured = _stub_yunet(monkeypatch)
    fy._yunet_detect(np.zeros((100, 200, 3), np.uint8))
    t = threading.Thread(target=fy._yunet_detect, args=(np.zeros((100, 200, 3), np.uint8),))
    t.start()
    t.join(5)
    assert len(created) == 2
    assert len(ensured) == 1


def test_a_reset_reaches_a_detector_built_on_another_thread(monkeypatch):
    fy, created, _resized, ensured = _stub_yunet(monkeypatch)
    fy._yunet_detect(np.zeros((100, 200, 3), np.uint8))
    fy.reset_yunet_cache()
    fy._yunet_detect(np.zeros((100, 200, 3), np.uint8))
    assert len(created) == 2, "the thread rebuilt its detector after the reset"
    assert len(ensured) == 2, "the load gate ran again"

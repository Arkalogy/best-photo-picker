"""A face mutation that can't get the face lock in time is refused (409),
never parked: a resume thread that outlived a library switch, or a long
resume, must not hang request threads."""

from __future__ import annotations

import threading

import pytest


def test_a_held_face_lock_turns_into_a_409(tmp_path, monkeypatch):
    from bpp.errors import ConflictError
    from bpp.web import face_lock
    from bpp.web.app import create_app

    monkeypatch.setattr(face_lock, "FACE_LOCK_TIMEOUT_S", 0.2)
    app = create_app(library_path=str(tmp_path / "lib"))
    ctx = app.extensions["bpp"]

    @face_lock.with_face_lock
    def mutate():
        return "ran"

    release = threading.Event()

    def holder():
        with ctx.face_op_lock:
            release.wait(5)

    t = threading.Thread(target=holder)
    t.start()
    try:
        with app.app_context(), pytest.raises(ConflictError, match="face"):
            mutate()
    finally:
        release.set()
        t.join(5)
    with app.app_context():
        assert mutate() == "ran"

"""Third whole-tree review batch, part one: sharing off ends LAN
streams, rename recovery trusts the database, a failed phase 5 is
reported, the restore lock works on every filesystem, a dead scoring
child is noticed at once, a skipped download is not retried, face
embedding honours the model toggles, SFace resets per thread and is
verified once, EACCES is not transient, videos use the face settings,
MediaPipe inference is serialised, the chooser is linear in months,
--seed is gone, and SFace availability respects the gate."""

from __future__ import annotations

import errno
import inspect
import os
import queue
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest


def test_sharing_switched_off_revokes_every_lan_stream(monkeypatch):
    from bpp.web.sse import _revoked

    monkeypatch.setattr("bpp.web.share_devices.is_device_trusted", lambda conn, fp: True)
    monkeypatch.setattr("bpp.web.share_runtime.is_lan_sharing_enabled", lambda conn: False)
    ctx = SimpleNamespace(get_conn=lambda: None)
    assert _revoked(ctx, "phone-1") is True
    assert _revoked(ctx, None) is False, "the local app has no device to revoke"
    monkeypatch.setattr("bpp.web.share_runtime.is_lan_sharing_enabled", lambda conn: True)
    assert _revoked(ctx, "phone-1") is False


def _lib_with_one_photo(tmp_path, name):
    from bpp.db.connection import init_db
    from bpp.db.photos import upsert_photo

    lib = tmp_path / "lib"
    (lib / "photos").mkdir(parents=True, exist_ok=True)
    (lib / "data").mkdir(exist_ok=True)
    conn = init_db(str(lib / "data" / "photopicker.db"))
    old = str(lib / "photos" / name)
    upsert_photo(conn, {"filepath": old, "file_size": 1, "file_mtime": 1})
    conn.commit()
    pid = conn.execute("SELECT id FROM photos WHERE filepath=?", (old,)).fetchone()[0]
    return lib, conn, old, pid


def test_rename_recovery_keeps_a_rename_the_database_already_committed(tmp_path):
    from bpp.db.batch_rename import _journal_path, _write_journal, recover_interrupted_rename

    lib, conn, old, pid = _lib_with_one_photo(tmp_path, "a.jpg")
    new = old.replace("a.jpg", "b.jpg")
    with open(new, "wb") as f:
        f.write(b"x")
    conn.execute("UPDATE photos SET filepath=? WHERE id=?", (new, pid))
    conn.commit()
    jp = _journal_path(str(lib))
    _write_journal(jp, [{"old": old, "new": new, "id": pid}])
    assert recover_interrupted_rename(conn, str(lib)) == []
    assert os.path.exists(new) and not os.path.exists(old), "a committed rename was undone"
    assert not os.path.exists(jp)


def test_rename_recovery_still_reverts_a_rename_the_database_never_saw(tmp_path):
    from bpp.db.batch_rename import _journal_path, _write_journal, recover_interrupted_rename

    lib, conn, old, pid = _lib_with_one_photo(tmp_path, "a.jpg")
    new = old.replace("a.jpg", "b.jpg")
    with open(new, "wb") as f:
        f.write(b"x")
    _write_journal(_journal_path(str(lib)), [{"old": old, "new": new, "id": pid}])
    assert len(recover_interrupted_rename(conn, str(lib))) == 1
    assert os.path.exists(old) and not os.path.exists(new)


def test_rename_recovery_waits_when_the_rows_cannot_be_read(tmp_path, monkeypatch):
    import sqlite3

    from bpp.db import batch_rename
    from bpp.db.batch_rename import _journal_path, _write_journal, recover_interrupted_rename

    lib, conn, old, pid = _lib_with_one_photo(tmp_path, "a.jpg")
    new = old.replace("a.jpg", "b.jpg")
    with open(new, "wb") as f:
        f.write(b"x")
    conn.execute("UPDATE photos SET filepath=? WHERE id=?", (new, pid))
    conn.commit()
    jp = _journal_path(str(lib))
    _write_journal(jp, [{"old": old, "new": new, "id": pid}])

    def unreadable(*a, **k):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(batch_rename, "_owners", unreadable)
    assert recover_interrupted_rename(conn, str(lib)) == []
    assert os.path.exists(new) and os.path.exists(jp), "moved or forgot without knowing"


def test_a_failed_smart_album_refresh_in_phase_5_is_raised(monkeypatch, tmp_path):
    from bpp.db.connection import init_db
    from bpp.web.state_init_phases import backfill_dup_clusters_and_refresh

    conn = init_db(str(tmp_path / "t.db"))

    def boom(conn):
        raise RuntimeError("refresh died")

    monkeypatch.setattr("bpp.db.smart_albums.refresh_smart_albums", boom)
    with pytest.raises(RuntimeError, match="refresh died"):
        backfill_dup_clusters_and_refresh(conn, force_refresh=True)


def test_the_restore_lock_needs_no_hard_links(tmp_path, monkeypatch):
    from bpp.commands import db_restore

    def no_links(*a, **k):
        raise OSError(errno.EPERM, "Operation not permitted")  # exFAT / SMB

    monkeypatch.setattr(os, "link", no_links)
    lock = str(tmp_path / ".restore.lock")
    assert db_restore._acquire_maintenance_lock(lock, b"123456") is None
    with open(lock, encoding="utf-8") as f:
        assert f.read().strip() == "123456"
    # Held by a live process: refused. Stale: taken over.
    with open(lock, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    assert db_restore._acquire_maintenance_lock(lock, b"7") == 7
    with open(lock, "w", encoding="utf-8") as f:
        f.write("999999999")
    assert db_restore._acquire_maintenance_lock(lock, b"7") is None
    assert not any(n.startswith(".restore.lock.tmp") for n in os.listdir(tmp_path))


def test_an_empty_restore_lock_is_a_contender_still_writing(tmp_path, monkeypatch):
    from bpp.commands import db_restore

    monkeypatch.setattr(db_restore, "_EMPTY_LOCK_RETRY_S", 0.01)
    lock = str(tmp_path / ".restore.lock")
    open(lock, "w").close()

    def fill_in():
        time.sleep(0.02)
        with open(lock, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))

    t = threading.Thread(target=fill_in)
    t.start()
    assert db_restore._acquire_maintenance_lock(lock, b"7") == 7
    t.join()
    # Stays empty (a crashed contender, or a share showing the content
    # late): ownership unknown, so the lock is refused, never taken.
    open(lock, "w").close()
    assert db_restore._acquire_maintenance_lock(lock, b"7") == 7
    with open(lock, encoding="utf-8") as f:
        assert f.read() == ""


def test_the_drain_notices_a_dead_child_without_waiting_for_the_timeout():
    from bpp.utils.subprocess_runner import BoundedSubprocessRunner

    class _Phase:
        name = "scoring"

        def initial_state(self):
            return {}

        def reduce(self, state, msg):
            return state, None

    class _Queue:
        def get(self, timeout=None):
            time.sleep(min(timeout or 0, 0.05))
            raise queue.Empty

    proc = SimpleNamespace(pid=1, exitcode=-9, is_alive=lambda: False)
    runner = BoundedSubprocessRunner(_Phase(), message_timeout_s=60.0)
    runner.dead_child_poll_s = 0.05
    t0 = time.monotonic()
    _state, crashed = runner._drain(_Queue(), proc, None)
    assert crashed is True
    assert time.monotonic() - t0 < 5.0, "waited for the whole message timeout"

    # A child that finished in the same poll window is not a crash.
    class _LateSentinel(_Queue):
        calls = 0

        def get(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise queue.Empty
            from bpp.utils.subprocess_runner import SENTINEL

            return SENTINEL

    done = SimpleNamespace(pid=1, exitcode=0, is_alive=lambda: False)
    events = []
    _state, crashed = runner._drain(_LateSentinel(), done, events.append)
    assert crashed is False and events == []


def test_a_download_the_registry_skips_is_not_retried(tmp_path, monkeypatch):
    import bpp.utils.download as dl
    from bpp.scoring.model_download import download_model

    def skip(*a, **k):
        raise dl.DownloadSkippedError("m: license not accepted")

    monkeypatch.setattr(dl, "download_file", skip)
    monkeypatch.setattr(
        "time.sleep", lambda s: (_ for _ in ()).throw(AssertionError("retry slept"))
    )
    model = SimpleNamespace(
        name="m",
        model_path=tmp_path / "m.onnx",
        model_url="https://example.com/m.onnx",
        registry_id="m",
        model_sha256="a" * 64,
    )
    path, reason = download_model(model, repairing=False)
    assert path is None and "license" in reason


def test_face_embedding_passes_the_model_toggles_to_the_detector(monkeypatch):
    from bpp.scoring import face_embed, face_embed_detect, face_embed_sface

    seen = {}

    def fake_rows(image, min_confidence, embedding_confidence, model_toggles=None):
        seen["toggles"] = model_toggles
        return [np.zeros(15, np.float32)] * (face_embed.MAX_FACES_PER_PHOTO + 1)

    monkeypatch.setattr(face_embed_detect, "detect_face_rows", fake_rows)
    monkeypatch.setattr(face_embed_sface, "_get_sface_recognizer", lambda: object())
    toggles = {"model_scrfd": False}
    img = np.zeros((32, 32, 3), np.uint8)
    assert face_embed.extract_face_embeddings(img, method="sface", model_toggles=toggles) == []
    assert seen["toggles"] == toggles


def test_yunet_switched_off_does_no_direct_detection(monkeypatch):
    from bpp.scoring import face_embed_detect

    calls = []
    monkeypatch.setattr(
        "bpp.scoring.face_yunet._yunet_detect_raw", lambda img, **kw: calls.append(1)
    )
    monkeypatch.setattr("bpp.scoring.face.detect_faces_with_confidence", lambda *a, **k: [])
    img = np.zeros((32, 32, 3), np.uint8)
    assert face_embed_detect.detect_face_rows(img, 0.3, 0.65, {"model_yunet": False}) == []
    assert calls == []
    face_embed_detect.detect_face_rows(img, 0.3, 0.65, {"model_yunet": True})
    assert calls == [1]


def test_the_face_worker_threads_the_toggles_through(monkeypatch):
    from bpp.web import face_worker

    seen = {}
    monkeypatch.setattr(face_worker, "load_and_downscale", lambda fp, m: np.zeros((8, 8, 3)))
    monkeypatch.setattr(
        face_worker, "extract_face_embeddings", lambda img, **kw: seen.update(kw) or []
    )
    face_worker._extract_one("/p.jpg", 512, model_toggles={"model_yunet": False})
    assert seen["model_toggles"] == {"model_yunet": False}


def test_a_sface_reset_drops_every_threads_recognizer(monkeypatch):
    from bpp.scoring import face_embed_sface_runtime as rt

    monkeypatch.setattr(rt, "_SFACE_AVAILABLE", None)
    monkeypatch.setattr(rt, "_sface_generation", rt._sface_generation)
    rt._sface_tls.recognizer = "old"
    rt._sface_tls.generation = rt._sface_generation
    rt._reset_sface_cache()
    monkeypatch.setattr(rt, "_ensure_sface_model", lambda: False)
    assert rt._get_sface_recognizer() is None
    del rt._sface_tls.recognizer


def test_a_verified_sface_model_is_not_hashed_again_per_thread(monkeypatch):
    from bpp.scoring import face_embed_sface_runtime as rt

    monkeypatch.setattr(rt, "_SFACE_AVAILABLE", True)
    monkeypatch.setattr(rt, "_sface_generation", rt._sface_generation + 1)
    monkeypatch.setattr(
        rt, "_ensure_sface_model", lambda: (_ for _ in ()).throw(AssertionError("re-hashed"))
    )
    sentinel = object()
    monkeypatch.setattr(rt.cv2.FaceRecognizerSF, "create", lambda *a, **k: sentinel)
    for attr in ("recognizer", "generation"):
        if hasattr(rt._sface_tls, attr):
            delattr(rt._sface_tls, attr)
    assert rt._get_sface_recognizer() is sentinel
    del rt._sface_tls.recognizer


def test_workers_arriving_together_verify_the_sface_model_once(monkeypatch):
    from bpp.scoring import face_embed_sface_runtime as rt

    monkeypatch.setattr(rt, "_SFACE_AVAILABLE", None)
    monkeypatch.setattr(rt, "_sface_generation", rt._sface_generation + 1)
    monkeypatch.setattr(rt, "_sface_verified_generation", -1)
    ensured = []

    def slow_ensure():
        ensured.append(1)
        time.sleep(0.05)
        return True

    monkeypatch.setattr(rt, "_ensure_sface_model", slow_ensure)
    monkeypatch.setattr(rt.cv2.FaceRecognizerSF, "create", lambda *a, **k: object())
    got = []

    def worker():
        got.append(rt._get_sface_recognizer())
        del rt._sface_tls.recognizer

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(got) == 4 and all(r is not None for r in got)
    assert ensured == [1], "every worker hashed the model"


def test_sface_availability_respects_the_license_gate(monkeypatch):
    from bpp.scoring import face_embed_sface_runtime as rt

    monkeypatch.setattr(rt, "_SFACE_AVAILABLE", None)
    monkeypatch.setattr("bpp.registry.policy.is_load_blocked", lambda rid: True)
    assert rt._sface_available() is False
    monkeypatch.setattr("bpp.registry.policy.is_load_blocked", lambda rid: False)
    assert rt._sface_available() is True


def test_permission_denied_is_not_a_transient_io_error():
    from bpp.utils.retry import is_transient

    assert is_transient(OSError(errno.EACCES, "Permission denied")) is False
    assert is_transient(OSError(errno.ESTALE, "Stale file handle")) is True


def test_videos_are_scored_with_the_users_face_settings(monkeypatch, tmp_path):
    from bpp.scoring import aggregate_video as av

    class _Cap:
        def get(self, prop):
            return 10

        def set(self, *a):
            pass

        def read(self):
            return True, np.zeros((48, 64, 3), np.uint8)

        def release(self):
            pass

    seen = {}

    def fake_detect(frame, **kw):
        seen.update(kw)
        return []

    monkeypatch.setattr("bpp.utils.video.open_capture", lambda fp: _Cap())
    monkeypatch.setattr(av, "detect_faces", fake_detect)
    monkeypatch.setattr(
        av,
        "score_face",
        lambda *a, **k: {
            "face_score": 0.0,
            "face_count": 0,
            "largest_face_ratio": 0.0,
            "face_center_dist": 0.0,
        },
    )
    monkeypatch.setattr(av, "score_composition", lambda *a, **k: 0.0)
    monkeypatch.setattr(av, "pets_available", lambda: False)
    monkeypatch.setattr("bpp.utils.video.extract_video_metadata", lambda fp: None)
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    result = av.analyze_single_video(
        str(clip), 256, face_confidence=0.9, min_face_area_frac=0.2, model_toggles={}
    )
    assert result is not None
    assert seen["min_confidence"] == 0.9 and seen["min_face_area_frac"] == 0.2


def test_the_scoring_worker_hands_videos_the_face_settings(monkeypatch, tmp_path):
    from bpp.scoring import aggregate

    seen = {}
    monkeypatch.setattr(aggregate, "_get_cached", lambda *a: None)
    monkeypatch.setattr(aggregate, "_cache_key", lambda fp: (fp, 1, 1))
    monkeypatch.setattr(
        aggregate, "analyze_single_video", lambda fp, m, **kw: seen.update(kw) or None
    )
    aggregate.process_one((str(tmp_path / "v.mp4"), 512, "", 0.7, {}, {"min_face_area_pct": 4.0}))
    assert seen["face_confidence"] == 0.7
    assert seen["min_face_area_frac"] == pytest.approx(0.04)


def test_segmenter_and_pose_inference_hold_their_locks(monkeypatch):
    pytest.importorskip("mediapipe")
    from bpp.scoring import pose, segmentation

    held = {}

    class _Seg:
        def segment(self, mp_image):
            held["seg"] = segmentation._segmenter_lock.locked()
            return SimpleNamespace(confidence_masks=[])

    class _Pose:
        def detect(self, mp_image):
            held["pose"] = pose._pose_lock.locked()
            return SimpleNamespace(pose_landmarks=[])

    monkeypatch.setattr(segmentation, "_get_segmenter", lambda: _Seg())
    monkeypatch.setattr(pose._pose_model, "get", lambda: _Pose())
    img = np.zeros((16, 16, 3), np.uint8)
    assert segmentation.segment_subject(img) is None
    assert pose.detect_poses(img) == []
    assert held == {"seg": True, "pose": True}


def _old_month_pass(sorted_candidates, all_months, k, min_per_month, tracker):
    selected, used = [], set()
    for month in sorted(all_months):
        if len(selected) >= k:
            break
        picked = 0
        for cand in sorted_candidates:
            if picked >= min_per_month or len(selected) >= k:
                break
            if cand["filepath"] in used or cand.get("date_month") != month:
                continue
            ok, _ = tracker.can_accept(cand)
            if ok:
                tracker.accept(cand)
                selected.append(cand["filepath"])
                used.add(cand["filepath"])
                picked += 1
    return selected


def _candidates(n, months, rnd):
    out = []
    for i in range(n):
        m = rnd.randrange(months)
        month = f"{2000 + m // 12:04d}-{m % 12 + 1:02d}"
        out.append(
            {
                "filepath": f"/p{i}.jpg",
                "aggregate_score": rnd.random(),
                "date": f"{month}-{rnd.randrange(1, 29):02d}T10:00:00",
                "date_month": month,
                "date_day": f"{month}-{(i % 28) + 1:02d}",
            }
        )
    return out


def test_month_coverage_is_linear_in_months_and_unchanged():
    import random

    from bpp.selection.choose import choose
    from bpp.selection.diversity import DiversityTracker

    rnd = random.Random(3)
    small = _candidates(500, 40, rnd)
    cfg = {"min_per_month": 2, "max_per_day": 3, "max_per_month": 100}
    got = [c["filepath"] for c in choose(small, k=30, config=cfg)]
    tracker = DiversityTracker(max_per_day=3, min_per_month=2, max_per_month=100)
    months = {c["date_month"] for c in small}
    tracker.set_available_months(months)
    ordered = sorted(small, key=lambda x: (-x["aggregate_score"], x.get("date", ""), x["filepath"]))
    assert got[: len(months) * 2][:10] == _old_month_pass(ordered, months, 30, 2, tracker)[:10]

    big = _candidates(20000, 2000, rnd)
    t0 = time.monotonic()
    choose(big, k=5000, config={"min_per_month": 1, "max_per_day": 3, "max_per_month": 100})
    assert time.monotonic() - t0 < 3.0, "phase 1 is still months x candidates"


def test_the_seed_flag_is_gone_because_selection_is_deterministic():
    import bpp.selection.choose as choose_mod
    from bpp.cli import build_parser
    from bpp.scoring.aggregate import analyze_all

    assert "seed" not in inspect.signature(analyze_all).parameters
    assert not hasattr(choose_mod, "random")
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--seed", "1", "serve"])

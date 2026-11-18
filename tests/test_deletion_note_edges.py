"""The note of photos deleted for good: what may end it, where it is read."""

from __future__ import annotations

import json
import os
import threading

import pytest

# ── the note of deleted photos ───────────────────────────────────────────


def test_a_pipe_at_the_notes_name_is_not_read(tmp_path):
    from bpp.db.analysis_leftovers import PENDING, pending_forgets

    os.mkfifo(tmp_path / PENDING)
    result: list = []
    reader = threading.Thread(target=lambda: result.append(pending_forgets(str(tmp_path))))
    reader.start()
    reader.join(5)
    if reader.is_alive():  # unblock it before failing
        with open(tmp_path / PENDING, "w"):
            pass
        pytest.fail("reading the note blocked on a pipe")
    assert result == [set()]


def test_a_failed_settling_of_companions_does_not_stop_the_start(tmp_path, monkeypatch):
    import sqlite3

    from bpp.web import state_init
    from bpp.web.app import create_app

    def locked(conn):
        conn.execute("UPDATE photos SET is_live_photo_sidecar = 0 WHERE 0")
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(state_init, "settle_companions_of_binned", locked)
    app = create_app(library_path=str(tmp_path / "library"))
    with app.app_context():
        assert not app.extensions["bpp"].get_conn().in_transaction


# ── a later analysis never ends a deletion ───────────────────────────────


def _external_photo_deleted(tmp_path):
    from bpp.db import analysis_leftovers as leftovers

    work = tmp_path / "data"
    work.mkdir()
    photo = tmp_path / "external.jpg"  # an original outside the library stays on disk
    photo.write_bytes(b"x")
    leftovers._note_pending(str(work), [str(photo)])  # its rewrite has not run yet
    return work, str(photo)


def test_a_command_line_analysis_written_after_a_delete_keeps_the_photo_out(tmp_path, monkeypatch):
    from bpp.db import analysis_leftovers as leftovers
    from bpp.db.connection import init_db
    from bpp.scoring import aggregate
    from bpp.web.state_init_phases import import_from_legacy_caches

    work, photo = _external_photo_deleted(tmp_path)
    old = {"filepath": photo, "blur_raw": 200.0, "gps_lat": 39.1}  # scored before the delete
    monkeypatch.setattr(aggregate, "parallel_map", lambda *a, **k: [old])

    aggregate.analyze_all([photo], str(work), {}, workers=1)

    assert leftovers.pending_forgets(str(work)) == {photo}
    conn = init_db(str(work / "photopicker.db"))
    import_from_legacy_caches(conn, str(work), str(tmp_path), 0)
    assert conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0] == 0


def test_an_analysis_in_the_app_does_not_clear_a_note(tmp_path, offline_downloads):
    from unittest.mock import patch

    from bpp.db import analysis_leftovers as leftovers
    from bpp.web.analyze_worker import AnalyzeWorker

    work, photo = _external_photo_deleted(tmp_path)
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
    with patch.object(AnalyzeWorker, "_score", return_value=scored):
        worker.start(str(tmp_path), str(work), {"max_long_side": 256}, [".jpg"])
        worker._thread.join(timeout=60)

    events = []
    while not worker.progress_queue.empty():
        events.append(worker.progress_queue.get_nowait())
    assert any(e["type"] == "done" for e in events), events
    assert [d["filepath"] for d in json.loads((work / "analysis.json").read_text())] == [photo]
    assert leftovers.pending_forgets(str(work)) == {photo}


def _cli_args(photos, out):
    import argparse

    return argparse.Namespace(
        input=str(photos),
        out=str(out),
        config=None,
        max=0,
        workers=1,
        debug=False,
        dry_run=False,
        seed=42,
        extensions=".jpg",
    )


@pytest.mark.parametrize("out", ["data", "root"], ids=["its data folder", "its root"])
def test_a_command_line_analysis_does_not_write_into_a_library_the_app_has_open(
    tmp_path, monkeypatch, out
):
    from bpp import commands
    from bpp.db.library import ensure_library_dirs
    from bpp.scoring import aggregate

    lib = tmp_path / "library"
    work = ensure_library_dirs(str(lib))["data"]
    open(os.path.join(work, "photopicker.db"), "w").close()
    with open(os.path.join(work, ".serving.lock"), "w") as f:
        f.write(str(os.getppid()))  # the app, still running
    (tmp_path / "a.jpg").write_bytes(b"\xff\xd8\xff" + bytes(32))
    ran = []
    monkeypatch.setattr(aggregate, "analyze_all", lambda *a, **k: ran.append(1))

    target = work if out == "data" else lib
    assert commands.do_analyze(_cli_args(tmp_path, target)) == 1

    assert ran == [], "a delete made in the app meanwhile would be undone by its result"
    assert not os.path.exists(os.path.join(lib, ".serving.lock")), "nothing left held"


def test_a_command_line_analysis_frees_the_library_when_it_ends(tmp_path, monkeypatch):
    from bpp import commands
    from bpp.scoring import aggregate

    work = tmp_path / "data"
    (tmp_path / "a.jpg").write_bytes(b"\xff\xd8\xff" + bytes(32))
    seen = []

    def analyze(*a, **k):
        seen.append(os.path.isfile(work / ".serving.lock"))
        return {"processed": 1, "skipped": 0}

    monkeypatch.setattr(aggregate, "analyze_all", analyze)

    assert commands.do_analyze(_cli_args(tmp_path, work)) == 0

    assert seen == [True], "held while it runs"
    assert not (work / ".serving.lock").exists(), "the app can open the library after"


def test_selecting_from_a_library_root_leaves_out_a_photo_deleted_in_the_app(tmp_path, monkeypatch):
    import argparse

    from bpp import commands
    from bpp.db import analysis_leftovers as leftovers
    from bpp.scoring import aggregate

    root, work = tmp_path, tmp_path / "data"
    work.mkdir()
    with open(root / "analysis.json", "w") as f:  # an older copy, in the root
        json.dump([{"filepath": p, "aggregate_score": 0.5} for p in ("/p/gone.jpg", "/p/b.jpg")], f)
    leftovers._note_pending(str(work), ["/p/gone.jpg"])  # the app's note, its rewrite waiting
    loaded = []
    real = aggregate.load_analysis
    monkeypatch.setattr(
        aggregate, "load_analysis", lambda *a, **k: loaded.append(real(*a, **k)) or loaded[-1]
    )
    ns = argparse.Namespace(
        workdir=str(root),
        out=str(tmp_path / "out"),
        config=None,
        k=10,
        export_mode="copy",
        gallery=False,
        force=False,
        dry_run=True,
        seed=42,
        extensions=".jpg",
    )

    commands.do_select(ns)

    assert [d["filepath"] for d in loaded[0]] == ["/p/b.jpg"]


# ── a note in the library root ───────────────────────────────────────────


def _root_with_note(tmp_path, *paths):
    from bpp.db import analysis_leftovers as leftovers

    root, work = tmp_path, tmp_path / "data"
    work.mkdir()
    with open(root / "analysis.json", "w") as f:
        json.dump([{"filepath": p} for p in paths], f)
    leftovers._note_pending(str(root), [paths[0]])  # a session that kept its data in the root
    return root, work


def test_a_note_in_the_library_root_is_honoured_and_then_done(tmp_path):
    from bpp.db.analysis_leftovers import PENDING
    from bpp.db.connection import init_db
    from bpp.web.state_init_phases import import_from_legacy_caches

    root, work = _root_with_note(tmp_path, "/p/gone.jpg", "/p/b.jpg")
    conn = init_db(str(work / "photopicker.db"))

    import_from_legacy_caches(conn, str(work), str(root), 0)

    assert [r[0] for r in conn.execute("SELECT filepath FROM photos")] == ["/p/b.jpg"]
    assert not (root / PENDING).exists()
    assert [d["filepath"] for d in json.loads((root / "analysis.json").read_text())] == ["/p/b.jpg"]


def test_a_note_in_the_library_root_still_waiting_keeps_its_photo_out_of_either_copy(
    tmp_path, monkeypatch
):
    from bpp.db import analysis_leftovers as leftovers
    from bpp.db.connection import init_db
    from bpp.scoring.aggregate import load_analysis
    from bpp.web.state_init_phases import import_from_legacy_caches

    root, work = _root_with_note(tmp_path, "/p/gone.jpg", "/p/b.jpg")
    with open(work / "analysis.json", "w") as f:  # the copy start-up reads first
        json.dump([{"filepath": p} for p in ("/p/gone.jpg", "/p/b.jpg")], f)

    def cannot_write(*a, **k):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(leftovers, "replace_fresh", cannot_write)  # the rewrite fails
    conn = init_db(str(work / "photopicker.db"))

    import_from_legacy_caches(conn, str(work), str(root), 0)

    assert [r[0] for r in conn.execute("SELECT filepath FROM photos")] == ["/p/b.jpg"]
    assert (root / leftovers.PENDING).exists(), "kept until the rewrite is done"
    rows = load_analysis(str(work), notes_in=(str(work), str(root)))
    assert [d["filepath"] for d in rows] == ["/p/b.jpg"]


def test_clear_library_removes_a_note_in_the_library_root(tmp_path):
    from bpp.db.analysis_leftovers import PENDING, forget_analysis_files

    root, work = _root_with_note(tmp_path, "/p/gone.jpg")

    forget_analysis_files(str(work), str(root))

    assert not (root / PENDING).exists()


def test_a_photo_added_back_after_its_delete_keeps_its_analysis(tmp_path):
    from bpp.db import analysis_leftovers as leftovers
    from bpp.db.connection import init_db
    from bpp.db.photos import upsert_photo

    work, photo = _external_photo_deleted(tmp_path)
    with open(work / "analysis.json", "w") as f:
        json.dump([{"filepath": photo}], f)
    upsert_photo(init_db(str(work / "photopicker.db")), {"filepath": photo})  # added back

    leftovers.apply_pending_forgets(str(work))

    assert json.loads((work / "analysis.json").read_text()) == [{"filepath": photo}]
    assert leftovers.pending_forgets(str(work)) == set()

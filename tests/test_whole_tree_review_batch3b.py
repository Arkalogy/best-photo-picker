"""Third whole-tree review batch, part two (the web layer): album
config is validated on create, a cancelled face resume is not a failed
attempt, a queued derived-recovery re-run survives a crash, pet
detections are written in bulk, every alias-qualified active-photo
clause is built by active_photo_sql, video trim validates its numbers
and finds the row however the path was spelled, renaming an unmounted
library folder is refused, a migration error after RELEASE is the real
error, v35 rewrites every row, and a switch clears the old lock."""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def app(tmp_path):
    from bpp.web.app import create_app

    lib = tmp_path / "lib"
    (lib / "photos").mkdir(parents=True)
    app = create_app(workdir=str(lib / "data"), library_path=str(lib))
    app.config["TESTING"] = True
    return app


def test_creating_an_album_validates_its_config_like_updating_one(app):
    from bpp.web.recompute import recompute_weight_keys

    key = recompute_weight_keys()[0]
    with app.test_client() as c:
        r = c.post("/api/v1/albums", json={"name": "a", "config": {key: 5, "bogus": 1}})
        assert r.status_code == 201, r.get_data(as_text=True)
        album = c.get(f"/api/v1/albums/{r.get_json()['id']}").get_json()["album"]
    assert album["config"] == {key: 1.0}


def test_a_cancelled_face_resume_does_not_spend_a_retry(tmp_path, monkeypatch):
    from bpp.db.connection import init_db
    from bpp.db.photos import upsert_photo
    from bpp.web import face_extraction_journal as journal
    from bpp.web.face_recovery import recover_pending_face_extractions

    conn = init_db(str(tmp_path / "t.db"))
    upsert_photo(conn, {"filepath": "/a.jpg", "file_size": 1, "file_mtime": 1})
    run_id = journal.start_run(conn)
    cancel = threading.Event()

    def cancelled_resume(conn, with_faces, photo_map, mls, conf, cfg, **kw):
        cancel.set()  # the library switched while the run was resuming
        return 0, 0

    monkeypatch.setattr("bpp.web.face_worker.extract_and_cluster_faces", cancelled_resume)
    ctx = SimpleNamespace(
        load_analysis_if_needed=lambda: [{"filepath": "/a.jpg", "face_count": 1}],
        config={},
        invalidate_face_cluster_map=lambda: None,
        analysis_store=SimpleNamespace(face_recovery_thread=None, face_recovery_failed=False),
    )
    assert recover_pending_face_extractions(conn, ctx=ctx, cancel_event=cancel) == 0
    assert journal.get_retry_count(conn, run_id) == 0
    assert [r["run_id"] for r in journal.pending_runs(conn)] == [run_id]


def test_a_queued_recovery_rerun_survives_a_crashed_run(app, monkeypatch):
    from bpp.web import derived_recovery as dr

    ctx = app.extensions["bpp"]
    store = ctx.analysis_store
    store.recovery_rerun = [{"filepath": "/later.jpg"}]
    monkeypatch.setattr(
        dr, "_backfill_hashes", lambda *a: (_ for _ in ()).throw(RuntimeError("hash died"))
    )
    spawned = []
    monkeypatch.setattr(
        dr, "precompute_phashes", lambda c, data: spawned.append((data, store.recovery_running))
    )
    dr._run_recovery(ctx, ["/x.jpg"], store.phash_generation, store.phash_ready)
    assert store.recovery_rerun is None
    # Spawned after this run's flag is down, so the re-run's own flag stands.
    assert spawned == [([{"filepath": "/later.jpg"}], False)]


def test_a_queued_rerun_is_dropped_when_the_run_was_cancelled(app, monkeypatch):
    from bpp.web import derived_recovery as dr

    ctx = app.extensions["bpp"]
    store = ctx.analysis_store
    store.recovery_rerun = [{"filepath": "/later.jpg"}]
    monkeypatch.setattr(dr, "_backfill_hashes", lambda *a: None)  # cancelled
    spawned = []
    monkeypatch.setattr(dr, "precompute_phashes", lambda c, data: spawned.append(data))
    dr._run_recovery(ctx, ["/x.jpg"], store.phash_generation, store.phash_ready)
    assert store.recovery_rerun is None and spawned == []


def test_import_writes_pet_detections_in_one_bulk_call(tmp_path, monkeypatch):
    from bpp.db.connection import init_db
    from bpp.db.photos import upsert_photo
    from bpp.web import import_worker

    conn = init_db(str(tmp_path / "t.db"))
    valid = []
    for i in range(3):
        fp = f"/p{i}.jpg"
        upsert_photo(conn, {"filepath": fp, "file_size": 1, "file_mtime": 1})
        det = {"class": "dog", "confidence": 0.9, "bbox": [0, 0, 10, 10]}
        valid.append({"filepath": fp, "pet_detections": [det, dict(det)]})
    valid.append({"filepath": "/none.jpg"})
    conn.commit()
    monkeypatch.setattr(
        "bpp.db.pets.upsert_pet_detections",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("one call per photo")),
    )
    assert import_worker._store_pet_detections(conn, valid) == 6
    assert conn.execute("SELECT COUNT(*) FROM pet_detections").fetchone()[0] == 6


def test_alias_qualified_active_clauses_come_from_active_photo_sql():
    for path in (ROOT / "bpp").rglob("*.py"):
        src = path.read_text()
        hit = re.search(r"\w+\.\{_ACTIVE\}", src)
        assert hit is None, f"{path.name}: {hit.group(0)} qualifies only the first predicate"


def _trimmable_clip(app, monkeypatch):
    from bpp.db.photos import upsert_photo
    from bpp.web.state import get_ctx

    with app.app_context():
        ctx = get_ctx()
        d = os.path.join(ctx.state["library_path"], "photos")
        fp = os.path.join(d, "clip.mov")
        with open(fp, "wb") as f:
            f.write(b"old video bytes" * 10)
        conn = ctx.get_conn()
        upsert_photo(conn, {"filepath": fp, "sha256": "0" * 64, "file_size": 150, "is_video": 1})
        conn.commit()

    def fake_trim(src, out, *, start, end):
        with open(out, "wb") as f:
            f.write(b"trimmed")
        return {"ok": True}

    monkeypatch.setattr("bpp.utils.video.ffmpeg_available", lambda: True)
    monkeypatch.setattr("bpp.utils.video.trim_video", fake_trim)
    monkeypatch.setattr("bpp.utils.video.extract_video_metadata", lambda p: None)
    return fp


def test_video_trim_rejects_bad_numbers_with_a_400(app, monkeypatch):
    fp = _trimmable_clip(app, monkeypatch)
    with app.test_client() as c:
        for start, end in (("abc", 2), (3, 1), (-1, 2), (float("nan"), 1)):
            r = c.post("/api/v1/video/trim", json={"filepath": fp, "start": start, "end": end})
            assert r.status_code == 400, (start, end, r.status_code)
    with open(fp, "rb") as f:
        assert f.read() != b"trimmed"


def test_video_trim_updates_the_row_however_the_path_was_spelled(app, monkeypatch):
    import hashlib

    from bpp.web.state import get_ctx

    fp = _trimmable_clip(app, monkeypatch)
    spelled = os.path.join(os.path.dirname(fp), ".", "clip.mov")
    with app.test_client() as c:
        r = c.post("/api/v1/video/trim", json={"filepath": spelled, "start": 0, "end": 1})
    assert r.status_code == 200, r.get_data(as_text=True)
    with app.app_context():
        row = get_ctx().get_conn().execute("SELECT sha256 FROM photos WHERE filepath=?", (fp,))
        assert row.fetchone()["sha256"] == hashlib.sha256(b"trimmed").hexdigest()


def test_renaming_the_folder_of_an_unreachable_library_is_refused(app, tmp_path):
    from bpp.db.registry import add_library, list_libraries

    gone = str(tmp_path / "unmounted")
    add_library(gone, "Old")
    with app.test_client() as c:
        r = c.put(
            "/api/v1/libraries/rename", json={"path": gone, "name": "New", "rename_folder": True}
        )
    assert r.status_code in (404, 409), r.get_data(as_text=True)
    assert [lib["name"] for lib in list_libraries() if lib["path"] == gone] == ["Old"]


def test_a_failed_version_bump_rolls_the_whole_step_back(tmp_path, monkeypatch):
    import sqlite3

    from bpp.db import schema_migrate
    from bpp.db.schema import SCHEMA_VERSION

    conn = sqlite3.connect(str(tmp_path / "t.db"))
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    monkeypatch.setattr(
        "bpp.db.migrations.MIGRATIONS",
        [(SCHEMA_VERSION + 1, lambda c: c.execute("CREATE TABLE marker (x)"))],
    )

    def broken(conn, v):
        raise RuntimeError("version write failed")

    monkeypatch.setattr(schema_migrate.dialect, "set_user_version", broken)
    with pytest.raises(RuntimeError, match="version write failed"):
        schema_migrate._migrate(conn)
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master").fetchall()]
    assert "marker" not in tables, "the step stayed committed without its version"
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert not conn.in_transaction


def test_a_step_inside_a_callers_transaction_rolls_back_when_the_commit_fails(tmp_path):
    import sqlite3

    from bpp.db.schema_migrate import _run_migration_step

    conn = sqlite3.connect(str(tmp_path / "t.db"))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
    conn.execute(
        "CREATE TABLE child (pid INTEGER REFERENCES parent(id) DEFERRABLE INITIALLY DEFERRED)"
    )
    conn.execute("BEGIN")
    conn.execute("INSERT INTO child VALUES (99)")  # the commit will refuse this
    with pytest.raises(sqlite3.IntegrityError):
        _run_migration_step(conn, 2, lambda c: c.execute("CREATE TABLE marker (x)"))
    assert not conn.in_transaction
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master").fetchall()]
    assert "marker" not in tables
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0


def test_v35_rewrites_every_float64_embedding(tmp_path):
    from bpp.db.connection import init_db
    from bpp.db.migrations_recent import _migrate_v35
    from bpp.db.photos import upsert_photo

    conn = init_db(str(tmp_path / "t.db"))
    upsert_photo(conn, {"filepath": "/a.jpg", "file_size": 1, "file_mtime": 1})
    pid = conn.execute("SELECT id FROM photos").fetchone()[0]
    blob = np.arange(128, dtype=np.float64).tobytes()
    conn.executemany(
        "INSERT INTO face_embeddings (photo_id, face_index, embedding) VALUES (?, ?, ?)",
        [(pid, i, blob) for i in range(2500)],
    )
    conn.commit()
    _migrate_v35(conn)
    sizes = conn.execute("SELECT DISTINCT length(embedding) FROM face_embeddings").fetchall()
    assert [s[0] for s in sizes] == [512]


def test_switching_libraries_releases_the_old_serving_lock(tmp_path):
    from bpp.db.library import ensure_library_dirs
    from bpp.utils.serving_lock import lock_path
    from bpp.web.app import create_app

    a, b = tmp_path / "a", tmp_path / "b"
    for lib in (a, b):
        (lib / "photos").mkdir(parents=True)
    app = create_app(workdir=str(a / "data"), library_path=str(a))
    app.config["TESTING"] = True
    ctx = app.extensions["bpp"]
    with app.app_context():
        assert os.path.exists(lock_path(str(a / "data")))
        ctx.switch_library(str(b))
    b_dirs = ensure_library_dirs(str(b))
    assert os.path.exists(lock_path(b_dirs["data"]))
    assert not os.path.exists(lock_path(str(a / "data"))), "the old library still reads as served"

"""A storage blip must not mark the whole library missing.

``os.path.exists`` never raises, so wrapping it in ``retry_io`` could not
retry anything: during a NAS outage every photo read as absent and the
library emptied itself. The check now asks ``os.stat`` (which does raise),
treats only "no such file" as missing, skips the scan when the photo
folder isn't reachable, and refuses to flip most of the library at once
unless the owner forces a recheck.
"""

from __future__ import annotations

import errno
import os

import pytest
from PIL import Image

from bpp.db.photos_missing import MASS_FLIP_MIN_ROWS, check_missing


def _library(tmp_path, n: int):
    from bpp.db.connection import init_db
    from bpp.db.photos import upsert_photo

    photos = tmp_path / "photos"
    photos.mkdir()
    conn = init_db(str(tmp_path / "t.db"))
    paths = []
    for i in range(n):
        p = str(photos / f"img_{i}.jpg")
        Image.new("RGB", (4, 4), "gray").save(p, "JPEG")
        upsert_photo(conn, {"filepath": p, "date": "2024-01-01T00:00:00"})
        paths.append(p)
    conn.commit()
    return conn, str(photos), paths


def _missing_flags(conn) -> dict[str, int]:
    return dict(conn.execute("SELECT filepath, missing FROM photos").fetchall())


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch):
    monkeypatch.setattr("bpp.utils.retry.time.sleep", lambda s: None)


class TestCheckMissing:
    def test_a_deleted_file_is_marked(self, tmp_path):
        conn, photos, paths = _library(tmp_path, 3)
        os.remove(paths[1])
        assert check_missing(conn, photos_dir=photos) == [paths[1]]
        assert _missing_flags(conn)[paths[1]] == 1
        assert _missing_flags(conn)[paths[0]] == 0

    def test_a_transient_error_leaves_the_row_alone(self, tmp_path, monkeypatch):
        conn, photos, paths = _library(tmp_path, 2)
        real_stat = os.stat

        def flaky_stat(path, *a, **kw):
            if path == paths[0]:
                raise OSError(errno.EIO, "I/O error")
            return real_stat(path, *a, **kw)

        monkeypatch.setattr(os, "stat", flaky_stat)
        assert check_missing(conn, photos_dir=photos) == []
        assert all(v == 0 for v in _missing_flags(conn).values())

    def test_an_unreachable_photo_folder_skips_the_scan(self, tmp_path):
        conn, _photos, paths = _library(tmp_path, 2)
        for p in paths:
            os.remove(p)
        assert check_missing(conn, photos_dir=str(tmp_path / "not-mounted")) == []
        assert all(v == 0 for v in _missing_flags(conn).values())

    def test_most_of_the_library_vanishing_at_once_is_not_marked(self, tmp_path):
        conn, photos, paths = _library(tmp_path, MASS_FLIP_MIN_ROWS)
        for p in paths:
            os.remove(p)
        assert check_missing(conn, photos_dir=photos) == []
        assert all(v == 0 for v in _missing_flags(conn).values())

    def test_the_owner_can_force_the_scan(self, tmp_path):
        conn, photos, paths = _library(tmp_path, MASS_FLIP_MIN_ROWS)
        for p in paths:
            os.remove(p)
        assert sorted(check_missing(conn, photos_dir=photos, force=True)) == sorted(paths)
        assert all(v == 1 for v in _missing_flags(conn).values())


class TestUnansweredStorage:
    def test_a_few_unanswered_files_in_a_row_end_the_scan(self, tmp_path, monkeypatch):
        from bpp.db.photos_missing import UNANSWERED_LIMIT

        conn, photos, _paths = _library(tmp_path, UNANSWERED_LIMIT + 5)
        calls = []
        real_stat = os.stat

        def dead_storage(path, *a, **kw):
            if str(path).startswith(str(photos)):
                calls.append(path)
                raise OSError(errno.EIO, "I/O error")
            return real_stat(path, *a, **kw)

        monkeypatch.setattr(os, "stat", dead_storage)
        assert check_missing(conn) == []
        assert len({c for c in calls}) == UNANSWERED_LIMIT  # it stopped asking
        assert all(v == 0 for v in _missing_flags(conn).values())


class TestTheOwnersRecheck:
    def test_the_recheck_marks_what_is_gone_and_restores_what_is_back(self, tmp_path):
        from bpp.db.photos import upsert_photo
        from bpp.web.app import create_app

        lib = str(tmp_path / "lib")
        os.makedirs(os.path.join(lib, "photos"))
        app = create_app(library_path=lib)
        app.config["TESTING"] = True
        ctx = app.extensions["bpp"]
        gone = os.path.join(lib, "photos", "gone.jpg")
        back = os.path.join(lib, "photos", "back.jpg")
        for p in (gone, back):
            Image.new("RGB", (4, 4), "gray").save(p, "JPEG")
        with app.app_context():
            conn = ctx.get_conn()
            for p in (gone, back):
                upsert_photo(conn, {"filepath": p, "date": "2024-01-01T00:00:00"})
            conn.execute("UPDATE photos SET missing=1 WHERE filepath=?", (back,))
            conn.commit()
        os.remove(gone)
        with app.test_client() as c:
            r = c.post("/api/v1/photos/recheck-missing")
        assert r.status_code == 200
        assert r.get_json() == {"restored": 1, "still_missing": 1, "newly_missing": 1}
        with app.app_context():
            flags = dict(ctx.get_conn().execute("SELECT filepath, missing FROM photos").fetchall())
        assert flags == {gone: 1, back: 0}
        # The view follows: the gone photo left it, the restored one is in it.
        with app.test_client() as c:
            listed = {p["filepath"] for p in c.get("/api/v1/photos").get_json()["photos"]}
        assert back in listed and gone not in listed


class TestImportCollisionCheck:
    def test_a_storage_error_aborts_instead_of_reporting_the_slot_free(self, tmp_path, monkeypatch):
        from bpp.db.library import _unique_dest_path

        batch = tmp_path / "batch"
        batch.mkdir()
        real_stat = os.stat

        def flaky_stat(path, *a, **kw):
            if str(path).startswith(str(batch)):
                raise OSError(errno.EIO, "I/O error")
            return real_stat(path, *a, **kw)

        monkeypatch.setattr(os, "stat", flaky_stat)
        with pytest.raises(OSError):
            _unique_dest_path(str(batch), "a.jpg")

    def test_a_free_slot_and_a_taken_slot(self, tmp_path):
        from bpp.db.library import _unique_dest_path

        batch = tmp_path / "batch"
        batch.mkdir()
        assert _unique_dest_path(str(batch), "a.jpg") == str(batch / "a.jpg")
        (batch / "a.jpg").write_bytes(b"x")
        assert _unique_dest_path(str(batch), "a.jpg") == str(batch / "a_1.jpg")

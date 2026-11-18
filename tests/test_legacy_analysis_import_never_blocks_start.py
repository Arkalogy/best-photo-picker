"""A bad analysis.json in a library can't stop the app from starting.

The start-up import of a legacy analysis.json ran unguarded, and its
trigger (an unscored photo plus the file) stayed true while the server
could not start, so the same file failed every launch. Entries are now
checked, and a file that still can't be imported is set aside.
"""

from __future__ import annotations

import json
import os

import pytest


def _library_with(tmp_path, analysis) -> tuple[str, str]:
    from bpp.db.connection import close_all_connections, get_db, init_db
    from bpp.db.library import ensure_library_dirs
    from bpp.db.photos import upsert_photo

    lib = str(tmp_path / "lib")
    os.makedirs(lib)
    data = ensure_library_dirs(lib)["data"]
    db_path = os.path.join(data, "photopicker.db")
    init_db(db_path)
    conn = get_db(db_path)
    photo = os.path.join(lib, "photos", "a.jpg")
    with open(photo, "wb") as f:
        f.write(b"\xff\xd8\xff")
    upsert_photo(conn, {"filepath": photo})  # unscored: the import's trigger
    conn.commit()
    close_all_connections(new_epoch=True)
    path = os.path.join(data, "analysis.json")
    with open(path, "w") as f:
        json.dump(analysis, f)
    return lib, path


def _open(lib):
    from bpp.web.app import create_app

    return create_app(workdir=os.path.join(lib, "data"), library_path=lib)


@pytest.mark.parametrize(
    "analysis",
    [
        [{"aggregate_score": 0.5}],
        [{"filepath": 5, "aggregate_score": 0.5}],
        [{"filepath": "", "aggregate_score": 0.5}],
    ],
)
def test_entries_without_a_usable_path_are_skipped(tmp_path, analysis):
    lib, _ = _library_with(tmp_path, analysis)

    app = _open(lib)

    with app.app_context():
        conn = app.extensions["bpp"].get_conn()
        assert conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0] == 1


def test_a_file_that_cannot_be_imported_is_set_aside_and_start_goes_on(tmp_path, monkeypatch):
    lib, path = _library_with(tmp_path, [{"filepath": "/x/a.jpg", "aggregate_score": 0.5}])

    def boom(conn, rows):
        raise ValueError("row the importer can't take")

    monkeypatch.setattr("bpp.db.migrate.bulk_upsert_photos", boom)

    _open(lib)

    assert not os.path.exists(path)
    assert [n for n in os.listdir(os.path.dirname(path)) if n.startswith("analysis.json.corrupt")]

    # The next launch has nothing left to trip on.
    from bpp.db.connection import close_all_connections

    close_all_connections(new_epoch=True)
    _open(lib)


def test_a_failure_that_is_not_the_files_fault_keeps_the_file(tmp_path, monkeypatch):
    import sqlite3

    lib, path = _library_with(tmp_path, [{"filepath": "/x/a.jpg", "aggregate_score": 0.5}])

    def busy(conn, rows):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("bpp.db.migrate.bulk_upsert_photos", busy)

    _open(lib)  # the launch still goes on

    assert os.path.exists(path), "a good file is kept for the next launch"

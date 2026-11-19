"""Rename crash recovery acts only on files inside the open library.

The journal lives in the library folder, so a folder prepared by someone
else can carry any paths in it. Recovery must not move a file into or
out of the library on that journal's say-so, and must only revert a
rename whose photo row still owns the old path.
"""

from __future__ import annotations

import json
import os

import pytest

from bpp.db.batch_rename import _journal_path, recover_interrupted_rename
from bpp.db.connection import init_db


@pytest.fixture
def lib(tmp_path):
    lib = tmp_path / "library"
    (lib / "data").mkdir(parents=True)
    return lib


@pytest.fixture
def conn(tmp_path):
    c = init_db(str(tmp_path / "test.db"))
    yield c
    c.close()


def _row(conn, filepath) -> int:
    name = os.path.basename(filepath)
    cur = conn.execute(
        "INSERT INTO photos (filepath, sha256, original_filename, file_size, file_mtime) "
        "VALUES (?, ?, ?, ?, ?)",
        (filepath, "hash_" + name, name, 1, 0.0),
    )
    conn.commit()
    return cur.lastrowid


def _write_journal(lib, entries) -> str:
    jp = _journal_path(str(lib))
    with open(jp, "w") as f:
        json.dump(entries, f)
    return jp


def _set_aside(jp) -> list[str]:
    folder, name = os.path.split(jp)
    return sorted(n for n in os.listdir(folder) if n.startswith(name + ".corrupt-"))


def test_a_file_outside_the_library_is_not_moved_into_it(conn, lib, tmp_path):
    outside = tmp_path / "owner_docs" / "private.txt"
    outside.parent.mkdir()
    outside.write_text("owner file")
    wanted = lib / "holiday.jpg"
    photo_id = _row(conn, str(wanted))
    jp = _write_journal(lib, [{"old": str(wanted), "new": str(outside), "id": photo_id}])

    assert recover_interrupted_rename(conn, str(lib)) == []

    assert outside.read_text() == "owner file"
    assert not wanted.exists()
    assert not os.path.exists(jp), "a journal with a refused entry must not be replayed again"
    assert len(_set_aside(jp)) == 1, "the refused journal is kept as a copy"


def test_a_library_file_is_not_moved_out_of_the_library(conn, lib, tmp_path):
    inside = lib / "photo.jpg"
    inside.write_text("photo")
    target = tmp_path / "elsewhere" / "photo.jpg"
    target.parent.mkdir()
    photo_id = _row(conn, str(target))
    _write_journal(lib, [{"old": str(target), "new": str(inside), "id": photo_id}])

    assert recover_interrupted_rename(conn, str(lib)) == []

    assert inside.read_text() == "photo"
    assert not target.exists()


def test_a_path_through_a_link_that_leaves_the_library_is_refused(conn, lib, tmp_path):
    outside_dir = tmp_path / "owner_pictures"
    outside_dir.mkdir()
    (outside_dir / "b.jpg").write_text("owner file")
    (lib / "link").symlink_to(outside_dir, target_is_directory=True)
    old, new = lib / "link" / "a.jpg", lib / "link" / "b.jpg"
    photo_id = _row(conn, str(old))
    _write_journal(lib, [{"old": str(old), "new": str(new), "id": photo_id}])

    assert recover_interrupted_rename(conn, str(lib)) == []

    assert sorted(os.listdir(outside_dir)) == ["b.jpg"]


@pytest.mark.parametrize("entry_id", [None, "1", 1.5, 424242])
def test_an_entry_whose_photo_row_does_not_own_the_old_path_is_not_reverted(conn, lib, entry_id):
    old, new = lib / "a.jpg", lib / "b.jpg"
    new.write_text("data")
    _row(conn, str(lib / "unrelated.jpg"))
    _write_journal(lib, [{"old": str(old), "new": str(new), "id": entry_id}])

    assert recover_interrupted_rename(conn, str(lib)) == []

    assert new.exists(), f"id={entry_id!r}: the file was moved without an owning photo row"
    assert not old.exists()


def test_a_genuine_interrupted_rename_is_still_reverted_next_to_a_refused_one(conn, lib, tmp_path):
    old, new = lib / "a.jpg", lib / "b.jpg"
    new.write_text("data")
    good_id = _row(conn, str(old))
    outside = tmp_path / "private.txt"
    outside.write_text("owner file")
    bad_id = _row(conn, str(lib / "x.jpg"))
    jp = _write_journal(
        lib,
        [
            {"old": str(old), "new": str(new), "id": good_id},
            {"old": str(lib / "x.jpg"), "new": str(outside), "id": bad_id},
        ],
    )

    reverted = recover_interrupted_rename(conn, str(lib))

    assert [e["old"] for e in reverted] == [str(old)]
    assert old.exists() and not new.exists()
    assert outside.read_text() == "owner file"
    assert not os.path.exists(jp)
    assert len(_set_aside(jp)) == 1


def test_apply_journals_only_the_entries_it_may_run(conn, lib, tmp_path, monkeypatch):
    from bpp.db import batch_rename

    inside = lib / "a.jpg"
    inside.write_text("data")
    outside = tmp_path / "owner.jpg"
    outside.write_text("owner file")
    good_id, bad_id = _row(conn, str(inside)), _row(conn, str(outside))
    written: list[list[dict]] = []
    monkeypatch.setattr(batch_rename, "_write_journal", lambda jp, entries: written.append(entries))
    mapping = [
        {
            "id": good_id,
            "old_filepath": str(inside),
            "new_filepath": str(lib / "b.jpg"),
            "changed": True,
        },
        {
            "id": bad_id,
            "old_filepath": str(outside),
            "new_filepath": str(lib / "c.jpg"),
            "changed": True,
        },
    ]

    results = batch_rename.apply_rename(conn, mapping, library_path=str(lib))

    assert [[e["id"] for e in entries] for entries in written] == [[good_id]]
    assert [r["success"] for r in results] == [True, False]
    assert outside.read_text() == "owner file"


def test_recovery_does_not_move_a_file_the_batch_never_renamed(conn, lib, monkeypatch):
    """Photo A's file is missing and its new name is another photo's
    file. The batch skips A; after a crash, recovery must not take the
    other photo's file for A's renamed one."""
    from bpp.db import batch_rename

    a_old, a_new = lib / "a.jpg", lib / "b.jpg"
    a_new.write_text("photo B")  # B's file sits at A's target name
    a_id = _row(conn, str(a_old))
    _row(conn, str(a_new))
    monkeypatch.setattr(batch_rename, "_remove_journal", lambda jp: None)  # the crash
    mapping = [
        {"id": a_id, "old_filepath": str(a_old), "new_filepath": str(a_new), "changed": True}
    ]
    results = batch_rename.apply_rename(conn, mapping, library_path=str(lib))
    assert [r["success"] for r in results] == [False]
    monkeypatch.undo()

    assert recover_interrupted_rename(conn, str(lib)) == []

    assert a_new.read_text() == "photo B" and not a_old.exists()

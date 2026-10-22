"""The claim-and-reconcile half of permanent delete under the odd cases:
a put-back that would overwrite, a put-back the OS refuses mid-request,
the DB half racing a restore, two deletes of one path, two operations
on one path, a claim name that must never be imported."""

from __future__ import annotations

import os
import pathlib

import pytest

from bpp.web.permanent_delete import _put_back, claim, claim_name
from tests.test_permanent_delete_recycle_bin import (
    TOKEN,
    _bin,
    _claims_in,
    _delete,
    _journal,
    _pending,
    _plan,
    _recover,
    _rows,
)


@pytest.fixture(autouse=True)
def _reset_journal_handlers():
    from bpp.db.journal import _reset_handlers_for_tests

    _reset_handlers_for_tests()
    yield
    _reset_handlers_for_tests()


@pytest.fixture
def app(tmp_path):
    from bpp.web.app import create_app

    lib = tmp_path / "library"
    (lib / "photos").mkdir(parents=True)
    app = create_app(workdir=str(lib / "data"), library_path=str(lib))
    app.config["TESTING"] = True
    return app


@pytest.mark.parametrize("occupied", [True, False])
def test_a_put_back_never_replaces_a_file_at_the_path(tmp_path, occupied):
    """The put-back is a hard link (atomic, refuses an existing name),
    so a file that appeared at the path between the check and the move
    is never overwritten."""
    fp = str(tmp_path / "p.jpg")
    with open(fp, "wb") as f:
        f.write(b"selected")
    name = claim_name(fp, TOKEN)
    os.rename(fp, name)
    if occupied:
        with open(fp, "wb") as f:
            f.write(b"arrived meanwhile")
    assert _put_back(name, fp) == ("stuck" if occupied else "back")
    with open(fp, "rb") as f:
        assert f.read() == (b"arrived meanwhile" if occupied else b"selected")
    assert os.path.exists(name) is occupied


def test_a_stranger_that_cannot_be_put_back_keeps_its_row_and_the_entry(app, monkeypatch):
    """Selection → claim moved a replacement (different inode); putting it
    back is refused. Its row stays, the record stays, the entry is
    pending, and the next start puts it back."""
    from bpp.db.photos import get_photo_id_map_by_paths
    from bpp.web import permanent_delete

    rows = _rows(app, ["swapped.jpg"])
    (fp,) = rows
    _bin(app, [rows[fp]])
    real_rename, real_link = os.rename, os.link
    phase = {"swap": True, "refuse_back": True}

    def swap_then_rename(src, dst):
        if src == fp and phase["swap"]:
            os.remove(fp)
            with open(fp, "wb") as f:
                f.write(b"a different file arrived between selection and the claim")
            phase["swap"] = False
        if dst == fp and phase["refuse_back"]:
            raise PermissionError("folder read-only for a moment")
        real_rename(src, dst)

    def refuse_link(src, dst):
        if dst == fp and phase["refuse_back"]:
            raise PermissionError("folder read-only for a moment")
        return real_link(src, dst)

    def refuse_noreplace(src, dst):
        if dst == fp and phase["refuse_back"]:
            raise PermissionError("read-only for a moment")
        os.rename(src, dst)

    monkeypatch.setattr(permanent_delete.os, "rename", swap_then_rename)
    monkeypatch.setattr(permanent_delete.os, "link", refuse_link)
    monkeypatch.setattr(permanent_delete, "_rename_noreplace", refuse_noreplace)
    body = _delete(app, [fp]).get_json()
    assert body["count"] == 0 and body["files_failed"] == [fp], body
    (entry,) = _pending(app)
    token = entry["payload"]["token"]
    assert entry["payload"]["files"][fp]["stuck"] and os.path.exists(claim_name(fp, token))
    ctx = app.extensions["bpp"]
    with app.app_context():
        assert get_photo_id_map_by_paths(ctx.get_conn(), [fp]) == {fp: rows[fp]}
    phase["refuse_back"] = False
    _recover(app)
    assert _pending(app) == [] and os.path.exists(fp)
    with open(fp, "rb") as f:
        assert f.read().startswith(b"a different file")


def test_the_db_half_reports_only_the_rows_it_removed(app):
    """A restore from another request between selection and the DELETE:
    the helper must not report that row as deleted."""
    from bpp.db.photos_lifecycle import permanent_delete_photos, restore_photos

    rows = _rows(app, ["restored.jpg", "gone.jpg"])
    restored = next(fp for fp in rows if fp.endswith("restored.jpg"))
    gone = next(fp for fp in rows if fp.endswith("gone.jpg"))
    _bin(app, list(rows.values()))
    ctx = app.extensions["bpp"]
    with app.app_context():
        conn = ctx.get_conn()
        restore_photos(conn, [rows[restored]])
        conn.commit()
        assert permanent_delete_photos(conn, list(rows.values())) == [gone]


def test_two_deletes_of_one_path_do_not_bring_the_file_back(app, monkeypatch):
    """The second request's DB half finds the row already gone: the
    reconcile asks the database and removes the claimed file."""
    from bpp.db import photos_lifecycle

    rows = _rows(app, ["twice.jpg"])
    (fp,) = rows
    _bin(app, [rows[fp]])
    real = photos_lifecycle.permanent_delete_photos

    def other_request_first(conn, ids):
        real(conn, ids)  # the other tab's request wins the race
        return []  # so this one reports nothing deleted

    monkeypatch.setattr("bpp.web.bp_photos_lifecycle.permanent_delete_photos", other_request_first)
    body = _delete(app, [fp]).get_json()
    assert not os.path.exists(fp) and _claims_in(os.path.dirname(fp)) == []
    assert _pending(app) == [] and body["count"] == 0 and body["files_removed"] == 1


def test_two_operations_on_one_path_cannot_share_a_claim_name(app):
    """Each operation's token names its own claim; a second operation that
    plans the same path while the first is interrupted claims nothing
    of the first's and the first's file survives for its row."""
    rows = _rows(app, ["shared.jpg"])
    (fp,) = rows
    first = _plan(app, {fp: rows[fp]}, token="aaaa0000aaaa0000")
    assert claim(first, "aaaa0000aaaa0000") == []
    _journal(app, first, token="aaaa0000aaaa0000")
    # A second operation (another database's purge, say) sees no file.
    second = _plan(app, {fp: rows[fp]}, token="bbbb1111bbbb1111")
    assert "ino" not in second[fp] and claim(second, "bbbb1111bbbb1111") == []
    _journal(app, second, token="bbbb1111bbbb1111")
    _recover(app)  # the row is still there: the first operation's file comes back
    assert os.path.exists(fp) and _pending(app) == []
    assert _claims_in(os.path.dirname(fp)) == []


def test_claim_names_are_invisible_to_the_scanner(tmp_path):
    from bpp.io_scan import scan_images

    folder = tmp_path / "photos"
    folder.mkdir()
    (folder / "keep.jpg").write_bytes(b"x")
    os.rename(str(folder / "keep.jpg"), claim_name(str(folder / "keep.jpg"), TOKEN))
    assert scan_images(str(folder), recursive=True) == []


def test_a_put_back_without_hard_links_never_overwrites_either(tmp_path, monkeypatch):
    """Where links are unsupported the put-back is an atomic no-replace
    rename; a file that lands at the path in between is kept and the
    claim stays."""
    from bpp.web import permanent_delete

    fp = str(tmp_path / "p.jpg")
    with open(fp, "wb") as f:
        f.write(b"selected")
    name = claim_name(fp, TOKEN)
    os.rename(fp, name)

    def no_links(*_a, **_k):
        raise OSError(45, "Operation not supported")

    monkeypatch.setattr(permanent_delete.os, "link", no_links)
    with open(fp, "wb") as f:
        f.write(b"arrived meanwhile")
    assert _put_back(name, fp) == "stuck"  # the no-replace rename refuses
    with open(fp, "rb") as f:
        assert f.read() == b"arrived meanwhile"
    assert os.path.exists(name)
    os.remove(fp)
    assert _put_back(name, fp) == "back"  # same rename, path free
    with open(fp, "rb") as f:
        assert f.read() == b"selected"


def test_a_rejected_stranger_is_never_unlinked_even_after_its_row_goes(app, monkeypatch):
    """Request 1 moved a stranger under its claim name and could not put
    it back. The owner's second request deletes the (binned) row. The
    replay of request 1 must put the stranger back, not unlink it."""
    from bpp.web import permanent_delete

    rows = _rows(app, ["swapped.jpg"])
    (fp,) = rows
    _bin(app, [rows[fp]])
    real_rename, real_link = os.rename, os.link
    phase = {"swap": True, "refuse_back": True}

    def swap_then_rename(src, dst):
        if src == fp and phase["swap"]:
            os.remove(fp)
            with open(fp, "wb") as f:
                f.write(b"UNSELECTED stranger, different inode")
            phase["swap"] = False
        if dst == fp and phase["refuse_back"]:
            raise PermissionError("read-only for a moment")
        real_rename(src, dst)

    def refuse_link(src, dst):
        if dst == fp and phase["refuse_back"]:
            raise PermissionError("read-only for a moment")
        return real_link(src, dst)

    def refuse_noreplace(src, dst):
        if dst == fp and phase["refuse_back"]:
            raise PermissionError("read-only for a moment")
        os.rename(src, dst)

    monkeypatch.setattr(permanent_delete.os, "rename", swap_then_rename)
    monkeypatch.setattr(permanent_delete.os, "link", refuse_link)
    monkeypatch.setattr(permanent_delete, "_rename_noreplace", refuse_noreplace)
    assert _delete(app, [fp]).get_json()["count"] == 0
    (entry,) = _pending(app)
    token = entry["payload"]["token"]
    phase["refuse_back"] = False
    # The owner retries: the path is empty, the row is binned → row goes.
    assert _delete(app, [fp]).get_json()["count"] == 1
    _recover(app)
    assert os.path.exists(fp), "the rejected stranger was unlinked"
    with open(fp, "rb") as f:
        assert f.read().startswith(b"UNSELECTED")
    assert not os.path.exists(claim_name(fp, token))


def test_a_leftover_link_after_a_put_back_is_removed_later(app, monkeypatch):
    """The link back succeeded but the claim name could not be removed:
    the extra link is tracked and removed at the next start."""
    from bpp.web import permanent_delete

    rows = _rows(app, ["kept.jpg"])
    (fp,) = rows
    _bin(app, [rows[fp]])
    real_unlink = os.unlink
    refuse = {"on": True}

    def maybe_locked(path, *a, **k):
        if refuse["on"] and ".bpp-trash-" in str(path):
            raise PermissionError("in use")
        real_unlink(path)

    monkeypatch.setattr(permanent_delete.os, "unlink", maybe_locked)
    monkeypatch.setattr("bpp.web.bp_photos_lifecycle.permanent_delete_photos", lambda c, ids: [])
    from bpp.db.photos_lifecycle import restore_photos

    ctx = app.extensions["bpp"]
    real_delete = permanent_delete.run_permanent_delete

    def restore_then_run(*a, **k):
        with app.app_context():
            restore_photos(ctx.get_conn(), [rows[fp]])
            ctx.get_conn().commit()
        return real_delete(*a, **k)

    monkeypatch.setattr(permanent_delete, "run_permanent_delete", restore_then_run)
    body = _delete(app, [fp]).get_json()
    assert body["count"] == 0 and body["files_failed"] == [fp], body
    (entry,) = _pending(app)
    token = entry["payload"]["token"]
    assert os.path.exists(fp) and os.path.exists(claim_name(fp, token))
    assert os.stat(fp).st_ino == os.stat(claim_name(fp, token)).st_ino
    refuse["on"] = False
    _recover(app)
    assert _pending(app) == []
    assert os.path.exists(fp) and not os.path.exists(claim_name(fp, token))


def test_a_claim_moved_outside_the_library_keeps_its_entry(app, tmp_path):
    """The photos folder is a symlink; between the claim and the replay
    its target moves outside the library. Nothing outside is touched,
    and the unfinished put-back is not forgotten."""
    from bpp.db.photos import upsert_photo

    ctx = app.extensions["bpp"]
    lib = ctx.state["library_path"]
    target = pathlib.Path(lib) / "inside-target"
    target.mkdir()
    link = os.path.join(lib, "linked")
    os.symlink(str(target), link)
    fp = os.path.join(link, "p.jpg")
    with open(fp, "wb") as f:
        f.write(b"photo in a linked folder")
    with app.app_context():
        pid = upsert_photo(ctx.get_conn(), {"filepath": fp, "file_size": 1, "file_mtime": 1})
        ctx.get_conn().commit()
    files = _plan(app, {fp: pid})
    assert "ino" in files[fp] and claim(files, TOKEN) == []
    _journal(app, files)
    outside = tmp_path / "outside-target"
    os.rename(str(target), str(outside))
    os.remove(link)
    os.symlink(str(outside), link)
    _recover(app)
    assert len(_pending(app)) == 1, "an unfinished put-back was forgotten"
    assert (outside / claim_name("p.jpg", TOKEN)).exists()
    assert not os.path.exists(fp)


def test_a_symlink_at_the_path_pointing_at_the_claim_is_not_a_link_back(tmp_path):
    """The owner symlinked the original name to the held file. That is
    not the file being back: the claim stays and nothing is removed."""
    fp = str(tmp_path / "p.jpg")
    with open(fp, "wb") as f:
        f.write(b"the only copy")
    name = claim_name(fp, TOKEN)
    os.rename(fp, name)
    os.symlink(name, fp)
    assert _put_back(name, fp) == "stuck"
    assert os.path.exists(name)
    with open(name, "rb") as f:
        assert f.read() == b"the only copy"


def test_an_inode_of_zero_is_not_evidence_of_a_link_back(tmp_path, monkeypatch):
    """A filesystem that reports inode 0 for everything must not make
    an unrelated file at the path look like the claim linked back."""
    from bpp.web import permanent_delete

    fp = str(tmp_path / "p.jpg")
    with open(fp, "wb") as f:
        f.write(b"ORIGINAL")
    name = claim_name(fp, TOKEN)
    os.rename(fp, name)
    with open(fp, "wb") as f:
        f.write(b"UNRELATED ARRIVAL")
    real_lstat = os.lstat

    class _Zero:
        def __init__(self, st):
            self._st = st

        def __getattr__(self, attr):
            return 0 if attr == "st_ino" else getattr(self._st, attr)

    monkeypatch.setattr(permanent_delete.os, "lstat", lambda path: _Zero(real_lstat(path)))
    assert _put_back(name, fp) == "stuck"
    assert os.path.exists(name)
    with open(fp, "rb") as f:
        assert f.read() == b"UNRELATED ARRIVAL"

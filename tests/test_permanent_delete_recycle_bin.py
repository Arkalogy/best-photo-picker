"""Permanent delete and the recycle bin, across a crash and a retry.

The DB half deletes only rows in the recycle bin. On disk each selected
file is first renamed to a hidden sibling (claimed), so no file that
appears at the same path later can be mistaken for it; the reconcile
puts back a file whose row survived and removes one whose row is gone;
a file that cannot be claimed keeps its row; and what is still stuck
is retried a bounded number of times.
"""

from __future__ import annotations

import os

import pytest

from bpp.web.permanent_delete import claim, claim_name, plan_delete
from tests.test_journal_recovery import _add_photo_file

TOKEN = "0123456789abcdef"


@pytest.fixture(autouse=True)
def _reset_journal_handlers():
    from bpp.db.journal import _reset_handlers_for_tests

    _reset_handlers_for_tests()
    yield
    _reset_handlers_for_tests()


@pytest.fixture
def app(tmp_path):
    """A WebAppState rigged with a real library directory."""
    from bpp.web.app import create_app

    lib = tmp_path / "library"
    (lib / "photos").mkdir(parents=True)
    app = create_app(workdir=str(lib / "data"), library_path=str(lib))
    app.config["TESTING"] = True
    return app


def _rows(app, names):
    """Real files with live rows; returns {filepath: id}."""
    from bpp.db.photos import upsert_photo

    ctx = app.extensions["bpp"]
    lib = ctx.state["library_path"]
    out = {}
    with app.app_context():
        conn = ctx.get_conn()
        for name in names:
            fp = _add_photo_file(lib, name)
            out[fp] = upsert_photo(conn, {"filepath": fp, "file_size": 16, "file_mtime": 1})
        conn.commit()
    return out


def _bin(app, ids):
    from bpp.db.photos_lifecycle import soft_delete_photos

    ctx = app.extensions["bpp"]
    with app.app_context():
        soft_delete_photos(ctx.get_conn(), ids)
        ctx.get_conn().commit()


def _delete(app, filepaths):
    return app.test_client().post(
        "/api/v1/photos/delete-permanent",
        json={"confirmation": "delete", "filepaths": filepaths},
    )


def _pending(app):
    from bpp.db.journal import pending_journals

    ctx = app.extensions["bpp"]
    with app.app_context():
        return pending_journals(ctx.get_conn())


def _recover(app):
    from bpp.db.journal import recover_pending
    from bpp.web.permanent_delete import register_permanent_delete_recovery

    ctx = app.extensions["bpp"]
    register_permanent_delete_recovery(ctx)
    with app.app_context():
        recover_pending(ctx.get_conn())


def _plan(app, path_ids, token=TOKEN):
    st = app.extensions["bpp"].state
    return plan_delete(path_ids, token, [st["library_path"], st["workdir"]])


def _journal(app, files, token=TOKEN, filepaths=None):
    from bpp.db.journal import journal_start

    ctx = app.extensions["bpp"]
    st = ctx.state
    with app.app_context():
        journal_start(
            ctx.get_conn(),
            "permanent_delete",
            {
                "filepaths": sorted(filepaths or files),
                "sha256_map": {},
                "allowed_dirs": [st["library_path"], st["workdir"]],
                "token": token,
                "files": files,
            },
        )


def _drop_rows(app, filepaths):
    ctx = app.extensions["bpp"]
    with app.app_context():
        conn = ctx.get_conn()
        for fp in filepaths:
            conn.execute("DELETE FROM photos WHERE filepath = ?", (fp,))
        conn.commit()


def _claims_in(folder):
    return [n for n in os.listdir(folder) if ".bpp-trash-" in n]


class TestTheRequest:
    def test_only_recycle_bin_rows_are_deleted_and_journaled(self, app):
        rows = _rows(app, ["kept.jpg", "binned.jpg"])
        kept = next(fp for fp in rows if fp.endswith("kept.jpg"))
        binned = next(fp for fp in rows if fp.endswith("binned.jpg"))
        _bin(app, [rows[binned]])
        body = _delete(app, [kept, binned]).get_json()
        assert body["count"] == 1 and body["files_removed"] == 1, body
        assert os.path.exists(kept) and not os.path.exists(binned)
        assert _pending(app) == []
        assert _claims_in(os.path.dirname(kept)) == []

    def test_a_refused_unlink_is_reported_and_retried_by_claim_name(self, app, monkeypatch):
        """The file stays under its claim name (nobody else writes it) and
        the request's own entry becomes the retry."""
        from bpp.web import permanent_delete

        rows = _rows(app, ["stuck.jpg"])
        (fp,) = rows
        _bin(app, [rows[fp]])
        real_unlink = os.unlink
        refuse = {"on": True}

        def maybe_locked(path, *a, **k):
            if refuse["on"] and ".bpp-trash-" in str(path):
                raise PermissionError("in use")
            real_unlink(path)

        monkeypatch.setattr(permanent_delete.os, "unlink", maybe_locked)
        body = _delete(app, [fp]).get_json()
        assert body["status"] == "partial" and body["files_failed"] == [fp], body
        (entry,) = _pending(app)
        token = entry["payload"]["token"]
        assert entry["payload"]["files"][fp]["claimed"]
        assert os.path.exists(claim_name(fp, token)) and not os.path.exists(fp)
        # A different photo at the old path is not what was deleted.
        with open(fp, "wb") as f:
            f.write(b"a new photo at the old name")
        refuse["on"] = False
        _recover(app)
        assert _pending(app) == []
        assert os.path.exists(fp) and not os.path.exists(claim_name(fp, token))

    def test_a_file_replaced_between_selection_and_the_claim_is_put_back(self, app, monkeypatch):
        """Step 2 compares what it moved with what was selected (inode,
        size, mtime) and puts a stranger back; its row stays."""
        from bpp.web import permanent_delete

        rows = _rows(app, ["swapped.jpg"])
        (fp,) = rows
        _bin(app, [rows[fp]])
        real_rename = os.rename

        def swap_then_rename(src, dst):
            if src == fp:
                os.remove(fp)
                with open(fp, "wb") as f:
                    f.write(b"arrived after the owner chose the delete, different length")
            real_rename(src, dst)

        monkeypatch.setattr(permanent_delete.os, "rename", swap_then_rename)
        body = _delete(app, [fp]).get_json()
        assert body["count"] == 0 and body["files_failed"] == [fp], body
        with open(fp, "rb") as f:
            assert f.read().startswith(b"arrived after")
        assert _pending(app) == []

    def test_a_crash_before_the_rows_go_puts_the_claimed_files_back(self, app, monkeypatch):
        rows = _rows(app, ["a.jpg", "b.jpg"])
        _bin(app, list(rows.values()))

        def crash(*_a, **_k):
            raise RuntimeError("crash in the DB half")

        monkeypatch.setattr("bpp.web.bp_photos_lifecycle.permanent_delete_photos", crash)
        with pytest.raises(RuntimeError):
            _delete(app, list(rows))
        for fp in rows:
            assert os.path.exists(fp), "a claimed file was not put back"
        assert _pending(app) == []

    def test_a_crash_after_the_rows_go_finishes_on_replay(self, app, monkeypatch):
        from bpp.db import journal

        rows = _rows(app, ["a.jpg", "b.jpg"])
        _bin(app, list(rows.values()))

        def crash(*_a):
            raise RuntimeError("crash before journal_complete")

        monkeypatch.setattr(journal, "journal_complete", crash)
        with pytest.raises(RuntimeError):
            _delete(app, list(rows))
        (entry,) = _pending(app)
        token = entry["payload"]["token"]
        assert all(rec["claimed"] for rec in entry["payload"]["files"].values())
        monkeypatch.undo()
        _recover(app)
        assert _pending(app) == []
        for fp in rows:
            assert not os.path.exists(fp) and not os.path.exists(claim_name(fp, token))

    def test_an_exception_after_the_rows_went_still_removes_the_files(self, app, monkeypatch):
        """The DB half committed, then raised: the reconcile asks the
        database, finds the rows gone, and finishes — nothing comes back."""
        from bpp.db import photos_lifecycle

        rows = _rows(app, ["x.jpg"])
        (fp,) = rows
        _bin(app, [rows[fp]])
        real = photos_lifecycle.permanent_delete_photos

        def commit_then_raise(conn, ids):
            real(conn, ids)
            raise RuntimeError("plugin hook blew up after the commit")

        monkeypatch.setattr(
            "bpp.web.bp_photos_lifecycle.permanent_delete_photos", commit_then_raise
        )
        with pytest.raises(RuntimeError):
            _delete(app, [fp])
        assert not os.path.exists(fp)
        assert _claims_in(os.path.dirname(fp)) == []
        assert _pending(app) == []

    def test_a_file_that_cannot_be_checked_keeps_its_row(self, app, monkeypatch):
        from bpp.db.photos import get_photo_id_map_by_paths
        from bpp.web import permanent_delete

        rows = _rows(app, ["flaky.jpg", "fine.jpg"])
        flaky = next(fp for fp in rows if fp.endswith("flaky.jpg"))
        fine = next(fp for fp in rows if fp.endswith("fine.jpg"))
        _bin(app, list(rows.values()))
        real_stat = os.stat

        def blocked(path, *a, **k):
            if str(path) == flaky:
                raise PermissionError("mount gone")
            return real_stat(path, *a, **k)

        monkeypatch.setattr(permanent_delete.os, "stat", blocked)
        body = _delete(app, [flaky, fine]).get_json()
        assert body["count"] == 1 and body["files_failed"] == [flaky], body
        assert body["status"] == "partial" and not os.path.exists(fine)
        ctx = app.extensions["bpp"]
        with app.app_context():
            assert get_photo_id_map_by_paths(ctx.get_conn(), [flaky]) == {flaky: rows[flaky]}
        assert _pending(app) == []

    def test_a_photo_outside_the_library_loses_its_row_only(self, app, tmp_path):
        from bpp.db.photos import get_photo_id_map_by_paths, upsert_photo

        outside = tmp_path / "elsewhere" / "in-place.jpg"
        outside.parent.mkdir()
        outside.write_bytes(b"analysed in place, never moved by the app")
        ctx = app.extensions["bpp"]
        with app.app_context():
            pid = upsert_photo(
                ctx.get_conn(), {"filepath": str(outside), "file_size": 1, "file_mtime": 1}
            )
            ctx.get_conn().commit()
        _bin(app, [pid])
        body = _delete(app, [str(outside)]).get_json()
        assert body["count"] == 1 and body["files_failed"] == [], body
        assert outside.exists()
        with app.app_context():
            assert get_photo_id_map_by_paths(ctx.get_conn(), [str(outside)]) == {}


class TestReplayAndTheRows:
    def test_a_live_row_gets_its_file_back(self, app):
        rows = _rows(app, ["live.jpg", "orphan.jpg"])
        live = next(fp for fp in rows if fp.endswith("live.jpg"))
        orphan = next(fp for fp in rows if fp.endswith("orphan.jpg"))
        _drop_rows(app, [orphan])
        files = _plan(app, {live: rows[live], orphan: rows[orphan]})
        assert claim(files, TOKEN) == []
        _journal(app, files)
        _recover(app)
        assert _pending(app) == []
        assert os.path.exists(live), "the photo whose row is live was not put back"
        assert not os.path.exists(orphan) and not os.path.exists(claim_name(orphan, TOKEN))

    def test_a_row_still_in_the_bin_gets_its_file_back(self, app):
        rows = _rows(app, ["binned.jpg"])
        (fp,) = rows
        _bin(app, [rows[fp]])
        files = _plan(app, {fp: rows[fp]})
        assert claim(files, TOKEN) == []
        _journal(app, files)
        _recover(app)
        assert os.path.exists(fp) and _pending(app) == []

    def test_a_claim_made_just_before_the_crash_is_honoured(self, app):
        """The journal says nothing was claimed yet (the crash came before
        the update), but the claim name exists: the disk is the truth."""
        rows = _rows(app, ["a.jpg", "b.jpg"])
        a, b = sorted(rows)
        files = _plan(app, {a: rows[a], b: rows[b]})
        assert claim({a: files[a]}, TOKEN) == []
        files[a]["claimed"] = False
        _journal(app, files)
        _recover(app)  # rows still there: both come back
        assert os.path.exists(a) and os.path.exists(b) and _pending(app) == []
        _drop_rows(app, [a])
        assert claim({a: files[a]}, TOKEN) == []
        files[a]["claimed"] = False
        _journal(app, files)
        _recover(app)  # a's row gone: its claimed file is removed
        assert not os.path.exists(a) and not os.path.exists(claim_name(a, TOKEN))
        assert os.path.exists(b) and _pending(app) == []

    def test_an_entry_without_records_touches_nothing(self, app):
        rows = _rows(app, ["unclaimed.jpg"])
        (fp,) = rows
        _drop_rows(app, [fp])
        _journal(app, {}, filepaths=[fp])
        _recover(app)
        assert os.path.exists(fp) and _pending(app) == []

    def test_a_reimported_row_at_the_path_is_never_overwritten(self, app):
        """Row gone, file claimed, then the owner imports a new photo at
        the same path (new row). The replay removes the old claimed file
        and leaves the new one alone."""
        from bpp.db.photos import upsert_photo

        rows = _rows(app, ["reused.jpg"])
        (fp,) = rows
        files = _plan(app, {fp: rows[fp]})
        assert claim(files, TOKEN) == []
        _drop_rows(app, [fp])
        with open(fp, "wb") as f:
            f.write(b"the new photo, imported after the delete")
        ctx = app.extensions["bpp"]
        with app.app_context():
            upsert_photo(ctx.get_conn(), {"filepath": fp, "file_size": 40, "file_mtime": 2})
            ctx.get_conn().commit()
        _journal(app, files)
        _recover(app)
        assert _pending(app) == []
        with open(fp, "rb") as f:
            assert f.read().startswith(b"the new photo")
        assert not os.path.exists(claim_name(fp, TOKEN))

    def test_a_put_back_blocked_by_an_occupied_path_keeps_the_entry(self, app):
        """The row survived but a stranger sits at the path: the claimed
        file stays under its claim name and the entry stays pending
        (retried, then given up with the claim name in the log)."""
        from bpp.web.permanent_delete import MAX_DELETE_RETRIES

        rows = _rows(app, ["occupied.jpg"])
        (fp,) = rows
        files = _plan(app, {fp: rows[fp]})
        assert claim(files, TOKEN) == []
        with open(fp, "wb") as f:
            f.write(b"someone else's file")
        _journal(app, files)
        _recover(app)
        (entry,) = _pending(app)
        assert entry["payload"]["attempts"] == 1
        assert os.path.exists(claim_name(fp, TOKEN))
        with open(fp, "rb") as f:
            assert f.read() == b"someone else's file"
        for _ in range(MAX_DELETE_RETRIES):
            _recover(app)
        assert _pending(app) == []
        assert os.path.exists(claim_name(fp, TOKEN)), "given up, but the file is still there"

    def test_retries_stop_after_the_cap(self, app, monkeypatch):
        from bpp.web import permanent_delete

        rows = _rows(app, ["forever.jpg"])
        (fp,) = rows
        _bin(app, [rows[fp]])

        def locked(*_a, **_k):
            raise PermissionError("in use")

        monkeypatch.setattr(permanent_delete.os, "unlink", locked)
        _delete(app, [fp])
        for _ in range(permanent_delete.MAX_DELETE_RETRIES):
            _recover(app)
        assert _pending(app) == [], "a file the OS never releases is retried forever"
        (left,) = _claims_in(os.path.dirname(fp))
        assert left.startswith(".forever.jpg.bpp-trash-")

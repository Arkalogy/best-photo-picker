"""What an independent review of the second round of fixes found."""

from __future__ import annotations

import io
import json
import os
import tarfile

import pytest


def _tar(path, *entries) -> str:
    """*entries*: ``(name, data)`` for a file, ``(name, None, mtime)`` for a folder."""
    with tarfile.open(path, "w") as tf:
        for name, data, *rest in entries:
            info = tarfile.TarInfo(name)
            if data is None:
                info.type, info.mtime, info.mode = tarfile.DIRTYPE, rest[0], 0o755
                tf.addfile(info)
            else:
                info.size, info.mode = len(data), 0o644
                tf.addfile(info, io.BytesIO(data))
    return str(path)


# ── nothing is written into a file that is already there ─────────────────


def test_a_refused_member_touches_nothing_outside_or_inside(tmp_path):
    from bpp.web.analyze_archive import _archive_folder, extract_archive_into_workdir

    work = tmp_path / "data"
    base = work / "extracted"
    base.mkdir(parents=True)
    exports = tmp_path / "export"
    exports.mkdir()
    (base / "other.jpg").write_bytes(b"an older flat-layout photo")
    os.link(base / "other.jpg", exports / "other.jpg")
    archive = _tar(tmp_path / "a.tar", ("first.jpg", b"NEW"), ("../other.jpg", b"NEW"))
    folder = base / _archive_folder(archive)
    folder.mkdir()
    (folder / "first.jpg").write_bytes(b"an earlier first")

    assert extract_archive_into_workdir(archive, str(work), lambda e: None) is None

    assert (base / "other.jpg").read_bytes() == b"an older flat-layout photo"
    assert (folder / "first.jpg").read_bytes() == b"an earlier first", "nothing moved in"
    assert os.listdir(work / ".unpacking") == [], "the unpacking folder is gone"


def test_a_file_named_with_a_trailing_slash_is_not_written_through(tmp_path):
    from bpp.web.analyze_archive import _archive_folder, extract_archive_into_workdir

    work = tmp_path / "data"
    archive = _tar(tmp_path / "a.tar", ("photo.jpg/", b"NEW"))
    folder = work / "extracted" / _archive_folder(archive)
    folder.mkdir(parents=True)
    (folder / "photo.jpg").write_bytes(b"an earlier photo")
    exports = tmp_path / "export"
    exports.mkdir()
    os.link(folder / "photo.jpg", exports / "photo.jpg")

    extract_archive_into_workdir(archive, str(work), lambda e: None)

    assert (exports / "photo.jpg").read_bytes() == b"an earlier photo"


def test_folder_times_from_the_archive_are_kept(tmp_path):
    from bpp.web.analyze_archive import extract_archive_into_workdir

    work = tmp_path / "data"
    archive = _tar(tmp_path / "a.tar", ("album", None, 123456789), ("album/a.jpg", b"x"))

    out = extract_archive_into_workdir(archive, str(work), lambda e: None)

    assert int(os.stat(os.path.join(out, "album")).st_mtime) == 123456789


def test_an_unfinished_unpacking_left_by_an_earlier_run_is_removed(tmp_path):
    from bpp.web.analyze_archive import extract_archive_into_workdir

    work = tmp_path / "data"
    (work / ".unpacking" / "run-old" / "x").mkdir(parents=True)
    archive = _tar(tmp_path / "a.tar", ("a.jpg", b"x"))

    extract_archive_into_workdir(archive, str(work), lambda e: None)

    assert not (work / ".unpacking" / "run-old").exists()


# ── a failed switch restores the old library unchecked ───────────────────


def test_a_failed_switch_does_not_carry_the_other_librarys_sharing_check(tmp_path, monkeypatch):
    from bpp.web.app import create_app

    app = create_app(library_path=str(tmp_path / "a"))
    ctx = app.extensions["bpp"]
    other = tmp_path / "b"
    other.mkdir()
    seen = []
    real = ctx.startup

    def startup():
        if not seen:
            ctx.share_state_checked = True  # the other library passed its check...
            seen.append(1)
            raise RuntimeError("...and then refused to open")
        seen.append(ctx.share_state_checked)  # what the restored library starts with
        real()

    monkeypatch.setattr(ctx, "startup", startup)
    with app.app_context(), pytest.raises(RuntimeError):
        ctx.switch_library(str(other))

    assert seen[1] is False, "devices wait for the restored library's own check"


# ── another spelling of a deleted photo's path ───────────────────────────


def test_a_deleted_photo_is_left_out_under_any_spelling_of_its_path(tmp_path):
    from bpp.db import analysis_leftovers as leftovers

    work = str(tmp_path)
    leftovers._note_pending(work, ["/folder/photo.jpg"])
    rows = [
        {"filepath": "/folder/./photo.jpg"},
        {"filepath": "/folder//photo.jpg"},
        {"filepath": "/folder/b.jpg"},
    ]

    assert leftovers.without_pending(rows, work) == [{"filepath": "/folder/b.jpg"}]

    with open(os.path.join(work, "analysis.json"), "w") as f:
        json.dump(rows, f)
    leftovers.apply_pending_forgets(work)
    with open(os.path.join(work, "analysis.json")) as f:
        assert [d["filepath"] for d in json.load(f)] == ["/folder/b.jpg"]


def test_the_command_line_reader_leaves_pending_deletes_out(tmp_path):
    from bpp.db import analysis_leftovers as leftovers
    from bpp.scoring.aggregate import load_analysis

    work = str(tmp_path)
    with open(os.path.join(work, "analysis.json"), "w") as f:
        json.dump([{"filepath": "/p/gone.jpg"}, {"filepath": "/p/b.jpg"}], f)
    leftovers._note_pending(work, ["/p/gone.jpg"])

    assert [d["filepath"] for d in load_analysis(work)] == ["/p/b.jpg"]


# ── companions are settled before the albums are synced ──────────────────


def test_a_companion_made_a_photo_again_at_start_up_is_in_all_photos(tmp_path):
    from bpp.db.connection import close_all_connections
    from bpp.db.library import ensure_library_dirs
    from bpp.db.photos import upsert_photo
    from bpp.web.app import create_app

    lib = str(tmp_path / "library")
    work = ensure_library_dirs(lib)["data"]  # as `bpp serve` opens it
    app = create_app(workdir=work, library_path=lib)
    ctx = app.extensions["bpp"]
    photo = tmp_path / "IMG_9_1.jpg"  # a real file: a missing one is in no album
    photo.write_bytes(b"x")
    with app.app_context():
        conn = ctx.get_conn()
        orphan = upsert_photo(conn, {"filepath": str(photo)})
        conn.execute("UPDATE photos SET is_live_photo_sidecar = 1 WHERE id = ?", (orphan,))
        conn.execute("DELETE FROM album_photos WHERE photo_id = ?", (orphan,))
        conn.commit()
    close_all_connections(new_epoch=True)

    app = create_app(workdir=work, library_path=lib)  # the next start
    ctx = app.extensions["bpp"]
    ctx.smart_album_backfill_done.wait(30)
    with app.app_context():
        conn = ctx.get_conn()
        side = conn.execute(
            "SELECT is_live_photo_sidecar FROM photos WHERE id = ?", (orphan,)
        ).fetchone()[0]
        albums = {
            r[0]
            for r in conn.execute(
                "SELECT a.album_type FROM album_photos ap JOIN albums a ON a.id = ap.album_id"
                " WHERE ap.photo_id = ?",
                (orphan,),
            )
        }
    assert side == 0
    assert "all" in albums, albums


# ── the restart notice belongs to this run's listener ────────────────────


def test_the_restart_notice_stays_after_a_library_switch(tmp_path):
    from unittest.mock import patch

    from bpp.web.app import create_app
    from bpp.web.share import set_lan_sharing_enabled

    app = create_app(library_path=str(tmp_path / "a"))
    app.config["TESTING"] = True
    ctx = app.extensions["bpp"]
    ctx.bound_host, ctx.bound_host_chosen_by_app = "127.0.0.1", True  # sharing was off at start
    other = tmp_path / "b"
    other.mkdir()
    with app.app_context():
        ctx.switch_library(str(other))
        set_lan_sharing_enabled(ctx.get_conn(), True)  # this one shares
    with (
        patch("bpp.web.share.detect_lan_ip", return_value="192.168.1.50"),
        app.test_client() as client,
    ):
        info = client.get("/api/v1/share/info").get_json()
    assert info["restart_required"] is True and info["share_url"] is None

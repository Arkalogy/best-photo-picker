"""What an independent review of the branch's last fixes found in them."""

from __future__ import annotations

import json
import os
import zipfile
from types import SimpleNamespace

import pytest


def _zip(path, *names: str) -> str:
    with zipfile.ZipFile(path, "w") as z:
        for name in names:
            z.writestr(name, b"FROM THE ARCHIVE")
    return str(path)


# ── a deleted photo whose removal from the analysis file is still waiting ─


def _library_with_a_pending_delete(tmp_path):
    from bpp.db import analysis_leftovers as leftovers
    from bpp.db.connection import init_db

    work = str(tmp_path)
    with open(os.path.join(work, "analysis.json"), "w") as f:
        json.dump([{"filepath": p, "aggregate_score": 0.5} for p in ("/p/gone.jpg", "/p/b.jpg")], f)
    assert leftovers._note_pending(work, ["/p/gone.jpg"])
    return work, init_db(os.path.join(work, "photopicker.db")), leftovers


def test_a_rewrite_that_keeps_failing_still_does_not_bring_the_photo_back(tmp_path, monkeypatch):
    from bpp.web.state_init_phases import import_from_legacy_caches

    work, conn, leftovers = _library_with_a_pending_delete(tmp_path)
    monkeypatch.setattr(leftovers, "_forget_in_json", lambda *a: False)  # can't be replaced

    import_from_legacy_caches(conn, work, "", 0)  # start-up, empty database

    assert [r[0] for r in conn.execute("SELECT filepath FROM photos")] == ["/p/b.jpg"]
    assert leftovers.pending_forgets(work) == {"/p/gone.jpg"}, "and it is tried again later"


def test_the_lazy_load_of_the_analysis_file_leaves_pending_deletes_out_too(tmp_path, monkeypatch):
    from bpp.web.app import create_app

    lib = str(tmp_path / "library")
    app = create_app(library_path=lib)
    ctx = app.extensions["bpp"]
    from bpp.db import analysis_leftovers as leftovers

    work = ctx.state["workdir"]
    with open(os.path.join(work, "analysis.json"), "w") as f:
        json.dump([{"filepath": p} for p in ("/p/gone.jpg", "/p/b.jpg")], f)
    leftovers._note_pending(work, ["/p/gone.jpg"])
    monkeypatch.setattr(leftovers, "_forget_in_json", lambda *a: False)
    ctx.invalidate_analysis()
    with app.app_context():
        from bpp.web.state_init import load_analysis_if_needed

        load_analysis_if_needed(ctx, kick_recovery=False)
        paths = [r[0] for r in ctx.get_conn().execute("SELECT filepath FROM photos")]
    assert paths == ["/p/b.jpg"]


# ── unpacking an archive again ───────────────────────────────────────────


def test_a_file_the_library_uses_at_both_places_is_not_removed(tmp_path):
    from bpp.db.connection import init_db
    from bpp.db.photos import upsert_photo
    from bpp.web.analyze_archive import _archive_folder, extract_archive_into_workdir

    work = tmp_path / "data"
    (work / "extracted").mkdir(parents=True)
    (work / "extracted" / "photo.jpg").write_bytes(b"FROM THE ARCHIVE")
    archive = _zip(tmp_path / "holiday.zip", "photo.jpg")
    new_place = work / "extracted" / _archive_folder(archive) / "photo.jpg"
    conn = init_db(str(work / "photopicker.db"))
    for path in (work / "extracted" / "photo.jpg", new_place):
        upsert_photo(conn, {"filepath": str(path)})

    extract_archive_into_workdir(archive, str(work), lambda e: None)

    assert new_place.read_bytes() == b"FROM THE ARCHIVE", "its row still has its file"


def test_a_hard_linked_file_the_archive_does_not_write_is_left_alone(tmp_path):
    from bpp.web.analyze_archive import _archive_folder, extract_archive_into_workdir

    work = tmp_path / "data"
    archive = _zip(tmp_path / "holiday.zip", "second.jpg")
    folder = work / "extracted" / _archive_folder(archive)
    folder.mkdir(parents=True)
    (folder / "first.jpg").write_bytes(b"an earlier member")
    (folder / "second.jpg").write_bytes(b"an earlier second")
    exports = tmp_path / "export"
    exports.mkdir()
    os.link(folder / "first.jpg", exports / "first.jpg")  # as a hardlink export makes
    os.link(folder / "second.jpg", exports / "second.jpg")

    extract_archive_into_workdir(archive, str(work), lambda e: None)

    assert (folder / "first.jpg").read_bytes() == b"an earlier member"
    assert (folder / "second.jpg").read_bytes() == b"FROM THE ARCHIVE"
    assert (exports / "second.jpg").read_bytes() == b"an earlier second", "not written through"


# ── the sharing recheck belongs to the library it was made on ────────────


def test_a_recheck_that_finishes_after_a_library_switch_changes_nothing(tmp_path, monkeypatch):
    import threading

    from bpp.db.connection import init_db
    from bpp.web import share, share_seal

    conn = init_db(str(tmp_path / "photopicker.db"))
    ctx = SimpleNamespace(
        lock=threading.RLock(),
        library_id="A",
        share_state_checked=False,
        share_check_waits_for_key=True,
    )

    def check_then_switch(_conn):
        ctx.library_id = "B"  # the owner switches while A is being checked
        ctx.share_state_checked = False
        ctx.share_check_waits_for_key = True  # B's own check is waiting for the key too
        return share_seal.SEALED

    monkeypatch.setattr(share_seal, "check_on_open", check_then_switch)
    share._check_sharing_state_again(ctx, conn, "A")  # "A": read before conn was taken

    assert not ctx.share_state_checked, "B waits for its own check"
    assert ctx.share_check_waits_for_key


def test_share_info_asks_for_a_restart_instead_of_showing_a_link_no_phone_can_open(tmp_path):
    from bpp.web.app import create_app
    from bpp.web.share import set_lan_sharing_enabled

    app = create_app(library_path=str(tmp_path / "library"))
    app.config["TESTING"] = True
    ctx = app.extensions["bpp"]
    from unittest.mock import patch

    with app.app_context():
        set_lan_sharing_enabled(ctx.get_conn(), True)
    ctx.bound_host = "127.0.0.1"
    ctx.bound_host_chosen_by_app = True  # chosen at start, when sharing was off
    with patch("bpp.web.share.detect_lan_ip", return_value="192.168.1.50"):
        with app.test_client() as client:
            info = client.get("/api/v1/share/info").get_json()
            qr = client.get("/api/v1/share/qr")
            fresh = client.post("/api/v1/share/revoke").get_json()
        assert info["restart_required"] is True
        assert info["enabled"] is False and info["share_url"] is None
        assert qr.status_code == 404, "no QR for a link no phone can open"
        assert fresh["share_url"] is None

        # Behind a proxy on this computer (the owner chose the address): served.
        ctx.bound_host_chosen_by_app = False
        with app.test_client() as client:
            info = client.get("/api/v1/share/info").get_json()
        assert info["restart_required"] is False and info["enabled"] is True


def test_share_info_says_when_the_key_file_cant_be_read(tmp_path, monkeypatch):
    from bpp.web import share_seal_store
    from bpp.web.app import create_app

    app = create_app(library_path=str(tmp_path / "library"))
    app.config["TESTING"] = True
    monkeypatch.setattr(share_seal_store, "unreadable", lambda: "Permission denied")
    with app.test_client() as client:
        info = client.get("/api/v1/share/info").get_json()
    assert info["key_unreadable"] is True and info["enabled"] is False


# ── companions brought back into view are in All Photos ──────────────────


# ── a bare MPEG-4 video (.m4v) is a video ────────────────────────────────


@pytest.mark.parametrize("code", [0xB0, 0xB3, 0xB5, 0xB6, 0xBA])
def test_mpeg_start_codes_are_video(tmp_path, code):
    from bpp.utils.video import has_video_header

    path = tmp_path / "clip.m4v"
    path.write_bytes(b"\x00\x00\x01" + bytes([code]) + b"\x01" * 40)
    assert has_video_header(str(path))
    other = tmp_path / "not.m4v"
    other.write_bytes(b"\x00\x00\x01\x00" + b"\x01" * 40)
    assert not has_video_header(str(other))


# ── a followed folder under another name for it ──────────────────────────


def test_a_followed_folder_reached_by_another_name_still_counts(tmp_path):
    from bpp.utils.path_validation import FolderSet

    trip, elsewhere = tmp_path / "Trip", tmp_path / "Ext"
    (elsewhere / "raw").mkdir(parents=True)
    trip.mkdir()
    (elsewhere / "raw" / "a.jpg").write_bytes(b"x")
    (trip / "linked").symlink_to(elsewhere / "raw", target_is_directory=True)
    (tmp_path / "alias").symlink_to(trip, target_is_directory=True)
    roots = FolderSet([str(trip)], followed=[str(trip)])

    assert roots.holds(str(trip / "linked" / "a.jpg"))
    assert not roots.holds(str(tmp_path / "Other" / "linked" / "a.jpg"))
    assert not roots.holds(str(tmp_path / "alias" / "linked" / "a.jpg")), (
        "a link to the followed folder from elsewhere is not that folder"
    )
    other_case = tmp_path / "TRIP"
    if os.path.exists(other_case):  # a volume that ignores case (the Mac default)
        assert roots.holds(str(other_case / "linked" / "a.jpg")), "the same folder"
        assert not roots.holds(str(other_case / "linked" / ".." / "secret.jpg"))


# ── the write-ahead log is emptied, or it is said that it was not ────────


def test_a_checkpoint_that_stays_blocked_is_reported(caplog, monkeypatch):
    from bpp.db import analysis_leftovers as leftovers

    class Busy:
        def execute(self, sql):
            return SimpleNamespace(fetchone=lambda: (1, 5, 0))

    monkeypatch.setattr("time.sleep", lambda s: None)
    with caplog.at_level("WARNING"):
        leftovers._settle(Busy())
    assert "could not be emptied" in caplog.text


# ── each member is detached where it is written, when it is written ──────


def test_a_member_named_through_dot_dot_detaches_only_where_it_is_written(tmp_path):
    from bpp.web.analyze_archive import _archive_folder, extract_archive_into_workdir

    work = tmp_path / "data"
    archive = _zip(tmp_path / "holiday.zip", "a/../b.jpg")  # zipfile writes this to a/b.jpg
    folder = work / "extracted" / _archive_folder(archive)
    (folder / "a").mkdir(parents=True)
    exports = tmp_path / "export"
    exports.mkdir()
    for name in ("b.jpg", os.path.join("a", "b.jpg")):
        (folder / name).write_bytes(b"an earlier " + name.encode())
        os.link(folder / name, exports / name.replace(os.sep, "_"))

    extract_archive_into_workdir(archive, str(work), lambda e: None)

    assert (folder / "b.jpg").read_bytes() == b"an earlier b.jpg", "not the one written: kept"
    assert (folder / "a" / "b.jpg").read_bytes() == b"FROM THE ARCHIVE"
    assert (exports / "a_b.jpg").read_bytes() == b"an earlier a/b.jpg", "not written through"


def test_a_tar_that_fails_halfway_has_detached_only_what_it_replaced(tmp_path):
    import io
    import tarfile

    from bpp.web.analyze_archive import _archive_folder, extract_archive_into_workdir

    work = tmp_path / "data"
    archive = tmp_path / "holiday.tar"
    with tarfile.open(archive, "w") as tf:
        for name in ("/first.jpg", "../escape.jpg", "third.jpg"):
            info = tarfile.TarInfo(name)
            info.size = 3
            tf.addfile(info, io.BytesIO(b"NEW"))
    folder = work / "extracted" / _archive_folder(str(archive))
    folder.mkdir(parents=True)
    exports = tmp_path / "export"
    exports.mkdir()
    for name in ("first.jpg", "third.jpg"):
        (folder / name).write_bytes(b"an earlier " + name.encode())
        os.link(folder / name, exports / name)

    assert extract_archive_into_workdir(str(archive), str(work), lambda e: None) is None

    assert (exports / "first.jpg").read_bytes() == b"an earlier first.jpg", "not written through"
    assert (folder / "third.jpg").read_bytes() == b"an earlier third.jpg", "never reached: kept"


# ── the legacy copy of the analysis file in the library root ─────────────


def test_the_copy_in_the_library_root_is_filtered_by_the_note_in_the_workdir(tmp_path):
    from bpp.db import analysis_leftovers as leftovers
    from bpp.db.connection import init_db
    from bpp.web.state_init_phases import import_from_legacy_caches

    root, work = tmp_path / "lib", tmp_path / "lib" / "data"
    work.mkdir(parents=True)
    with open(root / "analysis.json", "w") as f:
        json.dump([{"filepath": p} for p in ("/p/gone.jpg", "/p/b.jpg")], f)
    leftovers._note_pending(str(work), ["/p/gone.jpg"])
    conn = init_db(str(work / "photopicker.db"))
    real = leftovers._forget_in_json
    try:
        leftovers._forget_in_json = lambda *a: False  # the rewrite keeps failing
        import_from_legacy_caches(conn, str(work), str(root), 0)
    finally:
        leftovers._forget_in_json = real

    assert [r[0] for r in conn.execute("SELECT filepath FROM photos")] == ["/p/b.jpg"]

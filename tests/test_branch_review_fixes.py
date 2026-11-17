"""What a review of the whole hardening branch found in it."""

from __future__ import annotations

import os
import zipfile

import pytest


def _zip(path, *names: str) -> str:
    with zipfile.ZipFile(path, "w") as z:
        for name in names:
            z.writestr(name, b"FROM THE ARCHIVE")
    return str(path)


# ── archives are unpacked into real folders only ─────────────────────────


@pytest.mark.parametrize("linked", ["extracted", "folder", "member", "subfolder", "hard link"])
def test_unpacking_never_writes_through_a_link_left_in_the_library(tmp_path, linked):
    from bpp.web.analyze_archive import _archive_folder, extract_archive_into_workdir

    work = tmp_path / "data"
    work.mkdir()
    outside = tmp_path / "outside"
    (outside / "sub").mkdir(parents=True)
    for target in (outside / "photo.jpg", outside / "sub" / "photo.jpg"):
        target.write_bytes(b"OWNER ORIGINAL")
    archive = _zip(tmp_path / "holiday.zip", "photo.jpg", "sub/photo.jpg")
    folder = _archive_folder(archive)
    if linked == "extracted":
        (outside / folder).mkdir()
        (work / "extracted").symlink_to(outside, target_is_directory=True)
    else:
        (work / "extracted").mkdir()
        if linked == "folder":
            (work / "extracted" / folder).symlink_to(outside, target_is_directory=True)
        else:
            (work / "extracted" / folder).mkdir()
            if linked == "member":
                (work / "extracted" / folder / "photo.jpg").symlink_to(outside / "photo.jpg")
            elif linked == "hard link":
                os.link(outside / "photo.jpg", work / "extracted" / folder / "photo.jpg")
            else:
                (work / "extracted" / folder / "sub").symlink_to(
                    outside / "sub", target_is_directory=True
                )

    out = extract_archive_into_workdir(archive, str(work), lambda e: None)

    assert (outside / "photo.jpg").read_bytes() == b"OWNER ORIGINAL"
    assert (outside / "sub" / "photo.jpg").read_bytes() == b"OWNER ORIGINAL"
    assert out is not None
    assert os.path.realpath(out).startswith(os.path.realpath(str(work)) + os.sep)
    with open(os.path.join(out, "sub", "photo.jpg"), "rb") as f:
        assert f.read() == b"FROM THE ARCHIVE"


# ── a video is sent only when the file is one ────────────────────────────

_VIDEO_HEADS = {
    "a.mp4": b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 20,
    "a.mov": b"\x00\x00\x00\x08wide\x00\x00\x10\x00mdat" + b"\x00" * 20,
    "a.avi": b"RIFF\x00\x00\x00\x00AVI LIST" + b"\x00" * 20,
    "a.mkv": b"\x1a\x45\xdf\xa3" + b"\x00" * 30,
    "a.wmv": bytes.fromhex("3026b2758e66cf11a6d900aa0062ce6c") + b"\x00" * 20,
    "a.flv": b"FLV\x01\x05" + b"\x00" * 30,
    "fragmented.mp4": b"\x00\x00\x00\x18styp" + b"\x00" * 30,
    "big.avi": b"RF64\xff\xff\xff\xffAVI ds64" + b"\x00" * 20,
    "stream.mp4": b"\x47" + b"\x00" * 187 + b"\x47" + b"\x00" * 187,
    "program.avi": b"\x00\x00\x01\xba" + b"\x00" * 30,
}


@pytest.mark.parametrize("name", sorted(_VIDEO_HEADS))
def test_a_video_file_is_recognised_by_its_own_header(tmp_path, name):
    from bpp.utils.video import has_video_header

    path = tmp_path / name
    path.write_bytes(_VIDEO_HEADS[name])
    assert has_video_header(str(path))


@pytest.mark.parametrize("content", [b"", b"short", b"-----BEGIN OPENSSH PRIVATE KEY-----\n" * 3])
def test_other_content_under_a_video_name_is_not_a_video(tmp_path, content):
    from bpp.utils.video import has_video_header

    path = tmp_path / "movie.mp4"
    path.write_bytes(content)
    assert not has_video_header(str(path))
    assert not has_video_header(str(tmp_path / "missing.mp4"))


def test_the_video_route_does_not_send_a_file_that_is_not_a_video(tmp_path):
    from types import SimpleNamespace

    from flask import Flask

    from bpp.errors import NotFoundError
    from bpp.web.bp_media import bp
    from bpp.web.thumbnails import ThumbnailCache

    note = tmp_path / "private-note.txt"
    note.write_bytes(b"private, not a video " * 4)
    link = tmp_path / "movie.mp4"
    link.symlink_to(note)
    real = tmp_path / "real.mp4"
    real.write_bytes(_VIDEO_HEADS["a.mp4"])
    thumbs = ThumbnailCache(str(tmp_path / "thumbs"))
    thumbs.build_map_from_paths([str(link), str(real)])
    app = Flask(__name__)
    app.extensions["bpp"] = SimpleNamespace(thumbs=thumbs)
    app.register_blueprint(bp)
    app.register_error_handler(NotFoundError, lambda e: ("not found", 404))
    with app.test_client() as client:
        refused = client.get(f"/video/{thumbs.get_hash(str(link))}")
        assert refused.status_code == 404
        assert b"private" not in refused.data
        sent = client.get(f"/video/{thumbs.get_hash(str(real))}")
        assert sent.status_code == 200
        sent.close()


# ── "Enable anyway" raises the search-data limit, it does not remove it ──


def test_the_stored_override_is_bounded_by_this_computers_memory(tmp_path, monkeypatch):
    import numpy as np

    from bpp.db import clip
    from bpp.db.connection import init_db
    from bpp.db.photos import upsert_photo
    from bpp.db.settings import set_setting
    from bpp.web.clip_cap import clip_cap_status

    conn = init_db(str(tmp_path / "photopicker.db"))
    for i in range(4):
        pid = upsert_photo(conn, {"filepath": str(tmp_path / f"{i}.jpg")})
        clip.upsert_clip_embedding(conn, pid, np.ones(512, dtype=np.float32))
    set_setting(conn, clip.CLIP_MAX_OVERRIDE_KEY, clip.CLIP_MAX_OVERRIDE_BYPASS)
    monkeypatch.setattr(clip, "CLIP_EMBEDDING_MAX_ROWS", 2)
    monkeypatch.setattr("bpp.web.clip_cap.CLIP_EMBEDDING_MAX_ROWS", 2)

    monkeypatch.setattr(clip, "_rows_this_computer_holds", lambda: 3)
    monkeypatch.setattr("bpp.web.clip_cap._rows_this_computer_holds", lambda: 3)
    with pytest.raises(clip.ClipEmbeddingsTooLarge):
        clip.load_clip_matrix(conn)
    assert clip_cap_status(conn, 4)["clip_cap_status"] == "disabled_too_large"

    monkeypatch.setattr(clip, "_rows_this_computer_holds", lambda: 4)
    monkeypatch.setattr("bpp.web.clip_cap._rows_this_computer_holds", lambda: 4)
    assert len(clip.load_clip_matrix(conn)[0]) == 4
    assert clip_cap_status(conn, 4)["clip_cap_status"] == "enabled_override"


def test_the_memory_bound_is_never_below_the_default_limit():
    from bpp.db import clip

    assert clip._rows_this_computer_holds() >= clip.CLIP_EMBEDDING_MAX_ROWS


# ── a delete cut short does not come back at the next start ──────────────


def test_a_photo_deleted_just_before_a_crash_is_not_imported_again(tmp_path):
    import json
    from types import SimpleNamespace
    from unittest.mock import patch

    from bpp.db.connection import init_db
    from bpp.db.library import ensure_library_dirs
    from bpp.db.photos import upsert_photo
    from bpp.db.photos_lifecycle import permanent_delete_photos, soft_delete_photos
    from bpp.web import permanent_delete as pd
    from bpp.web.state_init_phases import import_from_legacy_caches

    dirs = ensure_library_dirs(str(tmp_path / "library"))
    work = dirs["data"]
    conn = init_db(os.path.join(work, "photopicker.db"))
    gone = os.path.join(dirs["photos"], "last.jpg")
    kept = os.path.join(dirs["photos"], "other.jpg")
    for path in (gone, kept):
        with open(path, "wb") as f:
            f.write(b"photo")
    gone_id = upsert_photo(conn, {"filepath": gone})
    soft_delete_photos(conn, [gone_id])
    results = os.path.join(work, "analysis.json")
    with open(results, "w") as f:
        json.dump([{"filepath": p, "aggregate_score": 0.8} for p in (gone, kept)], f)
    ctx = SimpleNamespace(
        state={"workdir": work, "library_path": dirs["root"]}, thumbs=None, dirs=dirs
    )
    crash = patch.object(pd, "reconcile", side_effect=SystemExit("the process ends here"))
    with crash, pytest.raises(SystemExit):
        pd.run_permanent_delete(
            ctx,
            conn,
            {gone: gone_id},
            {},
            [dirs["root"]],
            lambda ids: permanent_delete_photos(conn, ids),
        )
    assert conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0] == 0

    import_from_legacy_caches(conn, work, dirs["root"], 0)  # the next start

    paths = [r[0] for r in conn.execute("SELECT filepath FROM photos")]
    assert paths == [kept], paths
    with open(results) as f:
        assert [d["filepath"] for d in json.load(f)] == [kept]


# ── Clear Library leaves no copy behind ──────────────────────────────────


def test_clear_library_removes_pet_crops_and_every_copy_of_the_database(tmp_path):
    from bpp.db.connection import backup_db, close_all_connections
    from bpp.db.photos import upsert_photo
    from bpp.web.app import create_app

    lib = str(tmp_path / "library")
    app = create_app(library_path=lib)
    app.config["TESTING"] = True
    ctx = app.extensions["bpp"]
    from bpp.db.dialect import dialect

    with app.app_context():
        conn = ctx.get_conn()
        db_path = dialect.database_path(conn)
        data = os.path.dirname(db_path)
        for i in range(200):
            upsert_photo(
                conn, {"filepath": os.path.join(lib, "photos", f"private-holiday-{i}.jpg")}
            )
        conn.execute("UPDATE photos SET gps_lat = 37.12345")
        conn.commit()
    backup_db(db_path)
    older = (
        ".backup.prev",
        ".before-restore-20260101T000000Z",
        ".pre-face-reset-1",
        ".corrupted-1",
    )
    for suffix in older:
        with open(db_path + suffix, "wb") as f:
            f.write(b"private-holiday in an older copy")
    with open(db_path + ".restore-pending", "w") as f:
        f.write("")
    os.makedirs(ctx.dirs["pet_crops"], exist_ok=True)
    crop = os.path.join(ctx.dirs["pet_crops"], "pet_1.jpg")
    with open(crop, "wb") as f:
        f.write(b"crop")

    with app.test_client() as client:
        r = client.delete("/api/v1/library", json={"confirmation": "delete"})
    assert r.status_code == 200, r.get_json()
    close_all_connections(new_epoch=True)

    assert not os.path.exists(crop)
    names = sorted(n for n in os.listdir(data) if n.startswith("photopicker.db"))
    assert "photopicker.db.backup" in names and "photopicker.db.restore-pending" in names
    assert not [
        n for n in names if "prev" in n or "before" in n or "pre-face" in n or "corrupted" in n
    ]
    for name in names:
        with open(os.path.join(data, name), "rb") as f:
            assert b"private-holiday" not in f.read(), f"{name} still names the photos"


def test_the_start_up_purge_settles_companions_first(tmp_path):
    from bpp.web.app import create_app

    app = create_app(library_path=str(tmp_path / "library"))
    ctx = app.extensions["bpp"]
    with app.app_context():
        conn = ctx.get_conn()
        orphan = _photo(conn, "/x/IMG_9_1.HEIC", is_live_photo_sidecar=1)
        ctx.auto_purge()
        assert (
            conn.execute(
                "SELECT is_live_photo_sidecar FROM photos WHERE id = ?", (orphan,)
            ).fetchone()[0]
            == 0
        )


# ── companions an earlier version left behind ────────────────────────────


def _photo(conn, path, **cols):
    from bpp.db.photos import upsert_photo

    pid = upsert_photo(conn, {"filepath": path})
    for col, value in cols.items():
        conn.execute(f"UPDATE photos SET {col} = ? WHERE id = ?", (value, pid))
    conn.commit()
    return pid


def test_companions_left_out_of_the_bin_or_orphaned_are_settled_at_open(tmp_path):
    from bpp.db.connection import init_db
    from bpp.db.photos_lifecycle import settle_companions_of_binned, sidecars_of

    conn = init_db(str(tmp_path / "photopicker.db"))
    old = "2026-01-01 00:00:00"
    same, other = 0x0F0F0F0F0F0F0F0F, 0x70F0F0F0F0F0F0F0
    parent = _photo(conn, "/x/IMG_1.HEIC", deleted_at=old, phash=same, ahash=same)
    twin = _photo(
        conn,
        "/x/IMG_1_1.HEIC",
        is_live_photo_sidecar=1,
        live_photo_parent_id=parent,
        phash=same,
        ahash=same,
    )
    named_only = _photo(
        conn,
        "/x/IMG_1_2.HEIC",
        is_live_photo_sidecar=1,
        live_photo_parent_id=parent,
        phash=other,
        ahash=other,
    )
    orphan = _photo(conn, "/x/IMG_9_1.HEIC", is_live_photo_sidecar=1)
    live_parent = _photo(conn, "/x/IMG_5.HEIC", phash=same, ahash=same)
    untouched = _photo(
        conn, "/x/IMG_5_1.HEIC", is_live_photo_sidecar=1, live_photo_parent_id=live_parent
    )

    assert settle_companions_of_binned(conn) == 3

    def row(pid):
        return tuple(
            conn.execute(
                "SELECT is_live_photo_sidecar, live_photo_parent_id, deleted_at"
                " FROM photos WHERE id = ?",
                (pid,),
            ).fetchone()
        )

    assert row(twin) == (1, parent, old), "the same picture follows its photo into the bin"
    assert row(named_only) == (0, None, None), "a different picture comes back into view"
    assert row(orphan) == (0, None, None)
    assert row(untouched) == (1, live_parent, None)
    assert sidecars_of(conn, [parent]) == {"/x/IMG_1_1.HEIC": twin}
    assert settle_companions_of_binned(conn) == 0
    assert not conn.in_transaction, "an open transaction would block the next writer"


# ── a delete does not parse the analysis file it is not listed in ────────


def test_the_analysis_file_is_parsed_only_when_it_names_a_deleted_photo(tmp_path, monkeypatch):
    import json

    from bpp.db import analysis_leftovers as leftovers

    results = tmp_path / "analysis.json"
    listed = ["/p/a.jpg", '/p/caf\u00e9 "x".jpg', "/p/" + "n" * 300 + ".jpg"]
    results.write_text(json.dumps([{"filepath": p} for p in listed], ensure_ascii=False))
    parsed = []
    real = leftovers.load_json_file
    monkeypatch.setattr(
        leftovers, "load_json_file", lambda *a, **k: parsed.append(1) or real(*a, **k)
    )

    leftovers.forget_analysis(str(tmp_path), ["/p/not-listed.jpg", "/p/a.jp"])
    assert parsed == []

    for i, path in enumerate(listed, 1):
        leftovers.forget_analysis(str(tmp_path), [path])
        assert len(parsed) == i, path
    assert json.loads(results.read_text()) == []


def test_a_path_split_across_two_reads_is_still_found(tmp_path, monkeypatch):
    from bpp.db import analysis_leftovers as leftovers

    results = tmp_path / "analysis.json"
    needle = '"/p/needle.jpg"'
    results.write_text("x" * ((1 << 20) - 5) + needle + "y" * 50)
    assert leftovers._may_name_any(str(results), {"/p/needle.jpg"})
    assert not leftovers._may_name_any(str(results), {"/p/other.jpg"})


# ── an archive first unpacked by an earlier version ──────────────────────


def test_photos_already_unpacked_the_old_way_are_not_unpacked_twice(tmp_path):
    from bpp.db.connection import init_db
    from bpp.db.photos import upsert_photo
    from bpp.web.analyze_archive import extract_archive_into_workdir

    work = tmp_path / "data"
    (work / "extracted" / "sub").mkdir(parents=True)
    for name in ("photo.jpg", "never-imported.jpg"):
        (work / "extracted" / name).write_bytes(b"FROM THE ARCHIVE")
    (work / "extracted" / "sub" / "photo.jpg").write_bytes(b"a different picture")
    conn = init_db(str(work / "photopicker.db"))
    for known in ("photo.jpg", os.path.join("sub", "photo.jpg")):
        upsert_photo(conn, {"filepath": str(work / "extracted" / known)})
    archive = _zip(
        tmp_path / "holiday.zip", "photo.jpg", "sub/photo.jpg", "new.jpg", "never-imported.jpg"
    )

    out = extract_archive_into_workdir(archive, str(work), lambda e: None)

    assert sorted(os.listdir(out)) == ["never-imported.jpg", "new.jpg", "sub"], (
        "only a photo the library has is left out"
    )
    assert os.listdir(os.path.join(out, "sub")) == ["photo.jpg"], "same name, other content"
    assert (work / "extracted" / "photo.jpg").read_bytes() == b"FROM THE ARCHIVE"

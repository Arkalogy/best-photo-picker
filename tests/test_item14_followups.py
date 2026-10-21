"""Follow-ups from the review of the whole-codebase fixes."""

from __future__ import annotations

import os
import shutil

from PIL import Image


def test_a_users_own_data_folder_deeper_in_a_source_is_scanned(tmp_path):
    from bpp.io_scan import scan_images

    (tmp_path / "2024" / "data").mkdir(parents=True)
    (tmp_path / "2024" / "data" / "trip.jpg").write_bytes(b"x")
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "junk.jpg").write_bytes(b"x")
    found = scan_images(str(tmp_path), recursive=True, extensions=["jpg"])
    assert found == [str(tmp_path / "2024" / "data" / "trip.jpg")]


def test_auto_purge_sweeps_the_cache_before_the_thumbnail_map_exists(tmp_path, monkeypatch):
    from bpp.db.photos import upsert_photo
    from bpp.web.app import create_app
    from bpp.web.state import get_ctx
    from bpp.web.state_ops import auto_purge

    app = create_app(library_path=str(tmp_path / "lib"))
    with app.app_context():
        ctx = get_ctx()
        d = os.path.join(ctx.state["library_path"], "photos")
        os.makedirs(d, exist_ok=True)
        fp = os.path.join(d, "old.jpg")
        Image.new("RGB", (8, 8)).save(fp, "JPEG")
        conn = ctx.get_conn()
        pid = upsert_photo(conn, {"filepath": fp})
        conn.execute("UPDATE photos SET deleted_at=datetime('now', '-40 days') WHERE id=?", (pid,))
        conn.commit()
        h = ctx.thumbs.get_hash(fp)
        variant = os.path.join(ctx.dirs["thumbs"], f"{h}_full.jpg")
        with open(variant, "wb") as f:
            f.write(b"x")
        ctx.thumbs = None  # startup order: the purge runs before the map is built
        auto_purge(ctx)
        assert not os.path.exists(variant)
        assert ctx.thumbs is not None


def test_an_unreadable_exif_block_does_not_abort_a_processed_export(tmp_path, monkeypatch):
    from bpp.output.export_modes import _process_image

    src = str(tmp_path / "p.jpg")
    Image.new("RGB", (40, 30)).save(src, "JPEG")

    def broken(self):
        raise SyntaxError("not a TIFF file")

    monkeypatch.setattr(Image.Image, "getexif", broken)
    dest = _process_image(src, str(tmp_path / "o.jpg"), "jpeg", None, 85, keep_exif=True)
    assert os.path.getsize(dest) > 0


def _zip_export(tmp_path, **kw):
    from bpp.output.export import export_selected

    lib = tmp_path / "lib"
    lib.mkdir(exist_ok=True)
    src = lib / "a.jpg"
    Image.new("RGB", (8, 8)).save(src, "JPEG")
    item = {"filepath": str(src), "aggregate_score": 0.5}
    outdir = tmp_path / "out"
    return export_selected(
        [item], [item], str(outdir), mode="zip", library_path=str(lib), **kw
    ), outdir


def test_a_failed_zip_keeps_the_photos_in_a_visible_folder(tmp_path, monkeypatch):
    import bpp.output.export as ex

    def no_zip(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(ex, "bundle_selected_zip", no_zip)
    _result, outdir = _zip_export(tmp_path)
    names = sorted(os.listdir(outdir))
    assert not [n for n in names if n.startswith(".bundle-")], names
    visible = [n for n in names if n.startswith("selected-")]
    assert visible and any(
        n.endswith("a.jpg") for n in os.listdir(os.path.join(outdir, visible[0]))
    )


def test_no_staging_folder_survives_a_zip_export(tmp_path):
    _result, outdir = _zip_export(tmp_path)
    assert not [n for n in os.listdir(outdir) if n.startswith(".bundle-")]


def test_rename_entries_without_an_id_are_refused_before_touching_disk(tmp_path):
    from bpp.db.batch_rename import apply_rename
    from bpp.db.connection import init_db
    from bpp.db.photos import upsert_photo

    conn = init_db(str(tmp_path / "t.db"))
    fp = str(tmp_path / "a.jpg")
    Image.new("RGB", (4, 4)).save(fp, "JPEG")
    upsert_photo(conn, {"filepath": fp})
    new = str(tmp_path / "b.jpg")
    res = apply_rename(
        conn,
        [{"old_filepath": fp, "new_filepath": new, "changed": True}],
        library_path=str(tmp_path),
    )
    assert res[0]["success"] is False
    assert os.path.exists(fp) and not os.path.exists(new)


def test_rename_looks_the_owners_up_once(tmp_path):
    from bpp.db.batch_rename import apply_rename
    from bpp.db.connection import init_db
    from bpp.db.photos import upsert_photo

    conn = init_db(str(tmp_path / "t.db"))
    mapping = []
    for i in range(3):
        fp = str(tmp_path / f"p{i}.jpg")
        Image.new("RGB", (4, 4)).save(fp, "JPEG")
        pid = upsert_photo(conn, {"filepath": fp})
        mapping.append(
            {
                "id": pid,
                "old_filepath": fp,
                "new_filepath": str(tmp_path / f"r{i}.jpg"),
                "changed": True,
            }
        )
    seen: list[str] = []
    conn.set_trace_callback(seen.append)
    try:
        res = apply_rename(conn, mapping, library_path=str(tmp_path))
    finally:
        conn.set_trace_callback(None)
    assert all(r["success"] for r in res)
    lookups = [s for s in seen if "SELECT id, filepath FROM photos WHERE id IN" in s]
    assert len(lookups) == 1


def test_restore_survives_an_unreadable_backup_wal(tmp_path, monkeypatch):
    import sqlite3

    from bpp.db.backup import restore_from_backup_if_corrupt

    db_path = str(tmp_path / "lib.db")
    keep = sqlite3.connect(db_path + ".backup")
    keep.execute("PRAGMA journal_mode=WAL")
    keep.execute("CREATE TABLE t (v TEXT)")
    keep.commit()
    with open(db_path, "wb") as f:
        f.write(b"SQLite format 3\x00" + b"\xff" * 4096)
    real = shutil.copy2

    def copy2(src, dst, *a, **k):
        if str(src).endswith("-wal"):
            raise OSError(13, "Permission denied")
        return real(src, dst, *a, **k)

    monkeypatch.setattr(shutil, "copy2", copy2)
    assert restore_from_backup_if_corrupt(db_path) is not None
    keep.close()


def test_trim_invalidates_the_row_and_the_old_bytes_cache(tmp_path, monkeypatch):
    from bpp.constants import HASH_PREFIX_LEN
    from bpp.db.photos import upsert_photo
    from bpp.web.app import create_app
    from bpp.web.state import get_ctx

    app = create_app(library_path=str(tmp_path / "lib"))
    app.config["TESTING"] = True
    old_sha = "9" * 64
    with app.app_context():
        ctx = get_ctx()
        d = os.path.join(ctx.state["library_path"], "photos")
        os.makedirs(d, exist_ok=True)
        fp = os.path.join(d, "clip.mov")
        with open(fp, "wb") as f:
            f.write(b"old" * 10)
        conn = ctx.get_conn()
        upsert_photo(
            conn, {"filepath": fp, "sha256": old_sha, "is_video": 1, "video_duration": 10.0}
        )
        conn.commit()
        ctx.load_analysis_if_needed()
        ctx.thumbs.build_map_from_paths([fp])
        stale = os.path.join(ctx.thumbs.cache_dir, f"{old_sha[:HASH_PREFIX_LEN]}_inpainted.png")
        with open(stale, "wb") as f:
            f.write(b"x")

    def fake_trim(src, out, *, start, end):
        with open(out, "wb") as f:
            f.write(b"trimmed")
        return {"ok": True}

    monkeypatch.setattr("bpp.utils.video.ffmpeg_available", lambda: True)
    monkeypatch.setattr("bpp.utils.video.trim_video", fake_trim)
    monkeypatch.setattr("bpp.utils.video.extract_video_metadata", lambda p: {"duration": 2.5})
    with app.test_client() as c:
        assert (
            c.post("/api/v1/video/trim", json={"filepath": fp, "start": 0, "end": 2.5}).status_code
            == 200
        )
    with app.app_context():
        row = next(a for a in get_ctx().load_analysis_if_needed() if a["filepath"] == fp)
        assert row["video_duration"] == 2.5
    assert not os.path.exists(stale)

"""Delete, purge and clear remove every copy the app made, and say so
truthfully; a folder handed to someone else describes only what is in it.
"""

from __future__ import annotations

import json
import os
import zipfile

import pytest
from PIL import Image


@pytest.fixture
def conn(tmp_path):
    from bpp.db.connection import close_all_connections, get_db, init_db

    db = str(tmp_path / "photopicker.db")
    init_db(db)
    yield get_db(db)
    close_all_connections(new_epoch=True)


def _photo(conn, path, **cols):
    from bpp.db.photos import upsert_photo

    photo_id = upsert_photo(conn, {"filepath": str(path)})
    for col, value in {"missing": 0, **cols}.items():
        conn.execute(f"UPDATE photos SET {col} = ? WHERE id = ?", (value, photo_id))
    conn.commit()
    return photo_id


# ── purging a model's face data ──────────────────────────────────────────


def _builtin_model_id():
    from bpp.registry.model_registry import list_entries

    return next(e.id for e in list_entries() if not e.id.startswith("byom_"))


def _add_face(conn, photo_id, model_id):
    from bpp.db.face_embedding_safety import active_embedding_dim

    conn.execute(
        "INSERT INTO face_embeddings (photo_id, face_index, embedding, bbox_x, bbox_y, bbox_w,"
        " bbox_h, cluster_id, producing_model_id) VALUES (?, 0, ?, 0, 0, 9, 9, 1, ?)",
        (photo_id, b"\x00" * (active_embedding_dim(conn) * 4), model_id),
    )
    conn.commit()


def test_purging_a_built_in_models_face_data_really_removes_it(conn, tmp_path):
    from bpp.registry.removal import remove_model_with_derived_choice

    model_id = _builtin_model_id()
    _add_face(conn, _photo(conn, tmp_path / "a.jpg"), model_id)
    result = remove_model_with_derived_choice(model_id, purge_derived=True, conn=conn)
    conn.commit()
    assert result.purged is True
    assert result.entry_removed is False  # a built-in entry stays; its data does not
    assert conn.execute("SELECT COUNT(*) FROM face_embeddings").fetchone()[0] == 0


def test_declining_the_purge_of_a_built_in_claims_nothing(conn, tmp_path):
    from bpp.registry.removal import ModelRemovalError, remove_model_with_derived_choice

    model_id = _builtin_model_id()
    _add_face(conn, _photo(conn, tmp_path / "a.jpg"), model_id)
    with pytest.raises(ModelRemovalError) as refused:
        remove_model_with_derived_choice(model_id, purge_derived=False, conn=conn)
    conn.rollback()
    assert "purge ran" not in str(refused.value)
    assert conn.execute("SELECT COUNT(*) FROM face_embeddings").fetchone()[0] == 1


# ── archives ─────────────────────────────────────────────────────────────


def _zip(path, name):
    img = path.parent / name
    Image.new("RGB", (8, 8), "red").save(img, "JPEG")
    with zipfile.ZipFile(path, "w") as zf:
        zf.write(img, name)
    img.unlink()
    return str(path)


def test_each_archive_is_unpacked_into_its_own_folder(tmp_path):
    """The second archive's analysis must not pick up the first one's photos."""
    from bpp.io_scan import scan_images
    from bpp.web.analyze_archive import extract_archive_into_workdir

    work = str(tmp_path / "data")
    first = extract_archive_into_workdir(_zip(tmp_path / "one.zip", "a.jpg"), work, lambda e: None)
    second = extract_archive_into_workdir(_zip(tmp_path / "two.zip", "b.jpg"), work, lambda e: None)
    assert first != second
    assert os.path.dirname(first) == os.path.dirname(second) == os.path.join(work, "extracted")
    assert [os.path.basename(p) for p in scan_images(second, recursive=True)] == ["b.jpg"]
    # The same archive again, after a photo was added to it: the same
    # folder, so its photos are updated in place, not imported twice.
    _zip(tmp_path / "one.zip", "a.jpg")
    with zipfile.ZipFile(tmp_path / "one.zip", "a") as zf:
        zf.writestr("c.jpg", b"more")
    again = extract_archive_into_workdir(str(tmp_path / "one.zip"), work, lambda e: None)
    assert again == first
    assert sorted(os.listdir(first)) == ["a.jpg", "c.jpg"]


def _analysis_cache(work, paths):
    from bpp.db.connection import get_db

    cache = get_db(str(work / "analysis_cache.db"))
    cache.execute(
        "CREATE TABLE IF NOT EXISTS image_analysis (filepath TEXT PRIMARY KEY,"
        " file_size INTEGER, file_mtime REAL, result_json TEXT)"
    )
    cache.executemany(
        "INSERT OR REPLACE INTO image_analysis VALUES (?, 1, 1.0, '{}')", [(p,) for p in paths]
    )
    cache.commit()
    return cache


def test_clearing_the_library_removes_what_the_app_unpacked_and_cached(tmp_path):
    from bpp.db.analysis_leftovers import forget_all_analysis
    from bpp.db.connection import close_all_connections

    work = tmp_path / "data"
    (work / "extracted" / "one").mkdir(parents=True)
    (work / "extracted" / "one" / "a.jpg").write_bytes(b"x")
    (work / "analysis.json").write_text("[]")
    legacy = tmp_path / "lib"
    legacy.mkdir()
    (legacy / "analysis.json").write_text("[]")  # where older versions wrote it
    cache = _analysis_cache(work, [f"/lib/photos/SECRETNAME_{i}.jpg" for i in range(300)])
    try:
        forget_all_analysis(str(work), str(legacy))
        assert not (work / "extracted").exists()
        assert not (work / "analysis.json").exists()
        assert not (legacy / "analysis.json").exists()
        assert cache.execute("SELECT COUNT(*) FROM image_analysis").fetchone()[0] == 0
        # Gone from the file itself, not only from what a query returns.
        for name in os.listdir(work):
            assert b"SECRETNAME" not in (work / name).read_bytes(), name
    finally:
        close_all_connections(new_epoch=True)


def test_a_link_where_the_unpack_folder_should_be_is_not_followed(tmp_path):
    from bpp.db.analysis_leftovers import forget_all_analysis

    precious = tmp_path / "precious"
    precious.mkdir()
    (precious / "keep.txt").write_text("x")
    work = tmp_path / "data"
    work.mkdir()
    os.symlink(precious, work / "extracted")
    forget_all_analysis(str(work))
    assert (precious / "keep.txt").read_text() == "x"


# ── what a permanent delete leaves behind ────────────────────────────────


def test_deleted_photos_leave_the_analysis_stores(tmp_path, conn):
    from bpp.db.analysis_leftovers import forget_analysis

    work = tmp_path / "data"
    work.mkdir()
    gone, kept = "/lib/photos/gone.jpg", "/lib/photos/kept.jpg"
    (work / "analysis.json").write_text(
        json.dumps([{"filepath": gone, "gps_lat": 1.5}, {"filepath": kept, "gps_lat": 2.5}])
    )
    cache = _analysis_cache(work, [gone, kept])

    forget_analysis(str(work), [gone])

    assert [d["filepath"] for d in json.loads((work / "analysis.json").read_text())] == [kept]
    assert [r[0] for r in cache.execute("SELECT filepath FROM image_analysis")] == [kept]
    for name in os.listdir(work):
        assert b"gone.jpg" not in (work / name).read_bytes(), name


def test_only_photos_whose_rows_are_gone_are_forgotten(tmp_path, conn):
    """Decided by the database: a photo that could not be deleted keeps
    its analysis; one whose row is gone loses it even if its file is
    still waiting to be removed."""
    from bpp.db.analysis_leftovers import forget_deleted

    work = tmp_path / "data"
    work.mkdir()
    still_here = str(tmp_path / "still.jpg")
    deleted = str(tmp_path / "deleted.jpg")
    _photo(conn, still_here)
    (work / "analysis.json").write_text(
        json.dumps([{"filepath": still_here}, {"filepath": deleted}])
    )
    forget_deleted(conn, str(work), [still_here, deleted])
    left = [d["filepath"] for d in json.loads((work / "analysis.json").read_text())]
    assert left == [still_here]


def test_a_start_up_import_does_not_bring_deleted_photos_back(conn, tmp_path):
    """Photos exist, one was permanently deleted, and an older analysis
    file still lists it: its row must not be created again."""
    from bpp.db.migrate import import_from_analysis_json

    kept = tmp_path / "kept.jpg"
    _photo(conn, kept)
    stale = tmp_path / "analysis.json"
    stale.write_text(
        json.dumps(
            [
                {"filepath": str(kept), "aggregate_score": 0.5},
                {"filepath": str(tmp_path / "deleted.jpg"), "aggregate_score": 0.9},
            ]
        )
    )
    import_from_analysis_json(conn, str(stale), existing_only=True)
    paths = [r[0] for r in conn.execute("SELECT filepath FROM photos ORDER BY filepath")]
    assert paths == [str(kept)]
    score = conn.execute("SELECT aggregate_score FROM photos").fetchone()[0]
    assert score == 0.5  # the photo that is there still gets its score


def test_a_library_whose_photos_are_all_in_the_bin_is_not_refilled(conn, tmp_path):
    """No visible photo is not the same as an empty library."""
    from bpp.web.state_init_phases import import_from_legacy_caches

    _photo(conn, tmp_path / "binned.jpg", deleted_at="2024-01-01 10:00:00")
    work = tmp_path / "data"
    work.mkdir()
    (work / "analysis.json").write_text(
        json.dumps([{"filepath": str(tmp_path / "long_gone.jpg"), "aggregate_score": 0.9}])
    )
    import_from_legacy_caches(conn, str(work), "", 0)  # 0 visible photos
    paths = [r[0] for r in conn.execute("SELECT filepath FROM photos")]
    assert paths == [str(tmp_path / "binned.jpg")]


# ── Live Photo companions follow their photo ─────────────────────────────


def _companion(conn, tmp_path, *, same_picture):
    parent = _photo(conn, tmp_path / "IMG_1.HEIC", phash=1234, ahash=5678)
    side = _photo(
        conn,
        tmp_path / "IMG_1_1.HEIC",
        is_live_photo_sidecar=1,
        live_photo_parent_id=parent,
        phash=1234 if same_picture else 0x0F0F0F0F0F0F,
        ahash=5678 if same_picture else 0x00FF00FF00FF,
    )
    return parent, side


def _binned(conn, photo_id):
    row = conn.execute("SELECT deleted_at FROM photos WHERE id = ?", (photo_id,)).fetchone()
    return row is not None and row[0] is not None


def test_a_live_photos_hidden_companion_follows_it_to_the_bin_and_out(conn, tmp_path):
    from bpp.db.photos_lifecycle import (
        count_deleted_photos,
        get_deleted_photos,
        permanent_delete_photos,
        restore_photos,
        sidecars_of,
        soft_delete_photos,
    )

    parent, side = _companion(conn, tmp_path, same_picture=True)
    soft_delete_photos(conn, [parent])
    assert _binned(conn, parent) and _binned(conn, side)
    assert count_deleted_photos(conn) == 1  # the bin shows the photo, not its companion
    assert [p["id"] for p in get_deleted_photos(conn)] == [parent]

    restore_photos(conn, [parent])
    assert not _binned(conn, parent) and not _binned(conn, side)

    soft_delete_photos(conn, [parent])
    assert sidecars_of(conn, [parent]) == {str(tmp_path / "IMG_1_1.HEIC"): side}
    removed = permanent_delete_photos(conn, [parent, side])
    assert sorted(removed) == sorted([str(tmp_path / "IMG_1.HEIC"), str(tmp_path / "IMG_1_1.HEIC")])
    assert conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0] == 0


def test_a_file_only_named_like_a_companion_is_never_deleted_unseen(conn, tmp_path):
    """`beach_2.jpg` hidden as the companion of `beach.jpg` on its name
    alone may be another picture: deleting the photo brings it back
    into view; it does not go to the bin with it."""
    from bpp.db.photos_lifecycle import sidecars_of, soft_delete_photos

    parent, side = _companion(conn, tmp_path, same_picture=False)
    soft_delete_photos(conn, [parent])
    assert _binned(conn, parent) and not _binned(conn, side)
    row = conn.execute(
        "SELECT is_live_photo_sidecar, live_photo_parent_id FROM photos WHERE id = ?", (side,)
    ).fetchone()
    assert tuple(row) == (0, None)  # an ordinary photo again
    assert sidecars_of(conn, [parent]) == {}


def test_deleting_a_photo_for_good_takes_its_companion_and_counts_one(tmp_path):
    """Through the route: both files go, the analysis file forgets both,
    and the owner is told one photo was deleted."""
    from bpp.db.library import ensure_library_dirs
    from bpp.db.photos import upsert_photo
    from bpp.db.photos_lifecycle import soft_delete_photos
    from bpp.web.app import create_app

    lib = str(tmp_path / "lib")
    dirs = ensure_library_dirs(lib)
    app = create_app(workdir=dirs["data"], library_path=lib)
    app.config["TESTING"] = True
    ctx = app.extensions["bpp"]
    folder = os.path.join(dirs["photos"], "Trip")
    os.makedirs(folder)
    files = [os.path.join(folder, n) for n in ("IMG_1.jpg", "IMG_1_1.jpg")]
    for path in files:
        Image.new("RGB", (8, 8), "red").save(path, "JPEG")
    with open(os.path.join(dirs["data"], "analysis.json"), "w") as f:
        json.dump([{"filepath": p} for p in files], f)
    with app.app_context():
        conn = ctx.get_conn()
        parent = upsert_photo(conn, {"filepath": files[0]})
        side = upsert_photo(conn, {"filepath": files[1]})
        conn.execute("UPDATE photos SET phash = 7, ahash = 9 WHERE id IN (?, ?)", (parent, side))
        conn.execute(
            "UPDATE photos SET is_live_photo_sidecar = 1, live_photo_parent_id = ? WHERE id = ?",
            (parent, side),
        )
        conn.commit()
        soft_delete_photos(conn, [parent])
    with app.test_client() as c:
        reply = c.post(
            "/api/v1/photos/delete-permanent",
            json={"confirmation": "delete", "filepaths": [files[0]]},
        )
    body = reply.get_json()
    assert reply.status_code == 200, body
    assert body["count"] == 1
    assert not os.path.exists(files[0]) and not os.path.exists(files[1])
    with open(os.path.join(dirs["data"], "analysis.json")) as f:
        assert json.load(f) == []


# ── the report in an export folder ───────────────────────────────────────


def _export(tmp_path, config, **kw):
    from bpp.output.export import export_selected

    lib = tmp_path / "lib"
    (lib / "photos" / "Holiday").mkdir(parents=True)
    paths = []
    for name in ("picked.jpg", "other.jpg", "trashed.jpg"):
        Image.new("RGB", (8, 8), "red").save(lib / "photos" / "Holiday" / name, "JPEG")
        paths.append(str(lib / "photos" / "Holiday" / name))
    items = [{"filepath": p, "date": "2024-01-01", "aggregate_score": 0.5} for p in paths]
    items[2]["deleted_at"] = "2024-02-01 10:00:00"
    out = tmp_path / "export"
    export_selected(
        selected=[items[0]],
        analysis=items,
        outdir=str(out),
        mode="copy",
        gallery=False,
        config=config,
        library_path=str(lib),
        **kw,
    )
    return json.loads((out / "report.json").read_text()), (out / "report.json").read_text()


def test_the_report_carries_no_credentials_or_database_rows(tmp_path):
    report, text = _export(
        tmp_path,
        {
            "blur_weight": 0.3,
            "lan_share_token": "SECRETSECRETSECRETSECRETSECRETSECRET",
            "lan_share_seal": "abcdef",
            "some_row_a_library_added": "x",
        },
    )
    assert "SECRET" not in text and "lan_share" not in text
    assert "some_row_a_library_added" not in report["config"]
    assert report["config"]["blur_weight"] == 0.3


def test_the_report_does_not_name_photos_that_were_not_exported(tmp_path):
    report, text = _export(tmp_path, {})
    assert "other.jpg" not in text and "trashed.jpg" not in text
    assert report["total_skipped"] == 1  # the photo in the bin is not part of the count
    assert report["total_analyzed"] == report["total_selected"] + report["total_skipped"]
    assert "skipped" not in report

"""Values and entries that come from inside a library folder never steer
a delete or a write out of the folder they belong to: a face index read
from the database, and a batch folder that is really a link.
"""

from __future__ import annotations

import os
import sqlite3
from types import SimpleNamespace

import pytest

HASH = "a" * 32


def _face_ctx_conn(crop_dir, face_index):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE face_embeddings (id INTEGER PRIMARY KEY, face_index)")
    conn.execute("INSERT INTO face_embeddings VALUES (1, ?)", (face_index,))
    ctx = SimpleNamespace(
        thumbs=SimpleNamespace(get_hash=lambda fp: HASH), dirs={"face_crops": str(crop_dir)}
    )
    return ctx, conn


def test_a_face_index_holding_a_path_removes_nothing_outside_the_crop_folder(tmp_path):
    from bpp.web.bp_faces_bbox import _invalidate_face_crop

    crops = tmp_path / "cache" / "face_crops"
    crops.mkdir(parents=True)
    owner = tmp_path / "owner_pictures"
    owner.mkdir()
    (owner / "wedding.jpg").write_bytes(b"owner file")
    (crops / f"{HASH}_x").mkdir()  # makes the crafted pattern resolve
    index = "x/" + os.path.relpath(owner, crops / f"{HASH}_x") + "/"
    ctx, conn = _face_ctx_conn(crops, index)

    _invalidate_face_crop(ctx, conn, 1, "/lib/photos/p.jpg")

    assert (owner / "wedding.jpg").exists(), "a file outside the crop folder was deleted"


def test_the_crops_of_the_edited_face_still_go_and_others_stay(tmp_path):
    from bpp.web.bp_faces_bbox import _invalidate_face_crop

    crops = tmp_path / "face_crops"
    crops.mkdir()
    gone = [f"{HASH}_2_1_2_3_4.jpg", f"{HASH}_2.jpg"]
    kept = [f"{HASH}_3_1_2_3_4.jpg", f"{'b' * 32}_2_1_2_3_4.jpg", f"{HASH}_2_notes.txt"]
    for name in gone + kept:
        (crops / name).write_bytes(b"x")
    ctx, conn = _face_ctx_conn(crops, 2)

    _invalidate_face_crop(ctx, conn, 1, "/lib/photos/p.jpg")

    assert sorted(os.listdir(crops)) == sorted(kept)


def _half_copy(folder):
    path = folder / "IMG_1.jpg.tmp"
    path.write_bytes(b"half copy")
    return path


def test_the_stale_copy_sweep_does_not_enter_a_linked_batch_folder(tmp_path):
    from bpp.db.library_copy import remove_stale_import_tmp

    photos = tmp_path / "library" / "photos"
    (photos / "real").mkdir(parents=True)
    owner = tmp_path / "owner_downloads"
    owner.mkdir()
    outside_tmp = _half_copy(owner)
    inside_tmp = _half_copy(photos / "real")
    (photos / "Trip").symlink_to(owner, target_is_directory=True)

    removed = remove_stale_import_tmp(str(photos), older_than_s=0)

    assert outside_tmp.exists(), "a file in the owner's linked folder was deleted"
    assert not inside_tmp.exists()
    assert removed == 1


def test_an_import_into_a_batch_folder_that_is_a_link_is_refused(tmp_path):
    from bpp.db.connection import init_db
    from bpp.db.library import ensure_library_dirs, import_folder

    lib = tmp_path / "library"
    dirs = ensure_library_dirs(str(lib))
    owner = tmp_path / "owner_downloads"
    owner.mkdir()
    os.symlink(owner, os.path.join(dirs["photos"], "Trip"))
    source = tmp_path / "card"
    source.mkdir()
    (source / "IMG_1.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    conn = init_db(os.path.join(dirs["data"], "photopicker.db"))

    with pytest.raises(ValueError, match="link"):
        import_folder(conn, str(source), str(lib), batch_name="Trip")

    assert os.listdir(owner) == []
    conn.close()


def test_face_one_does_not_take_the_crops_of_faces_ten_and_up(tmp_path):
    from bpp.web.face_crop import remove_face_crops

    crops = tmp_path / "face_crops"
    crops.mkdir()
    kept = [f"{HASH}_10_1_2_3_4.jpg", f"{HASH}_11.jpg", f"{HASH}_1x.jpg"]
    for name in [f"{HASH}_1_1_2_3_4.jpg", f"{HASH}_1.jpg", *kept]:
        (crops / name).write_bytes(b"x")

    remove_face_crops(str(crops), HASH, 1)

    assert sorted(os.listdir(crops)) == sorted(kept)

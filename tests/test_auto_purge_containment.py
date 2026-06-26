"""TDD tests for M-1: auto_purge must check library path containment."""

from __future__ import annotations

from bpp.db.photos import soft_delete_photos, upsert_photo
from bpp.web.app import create_app


def test_auto_purge_skips_files_outside_library(tmp_path):
    """Files with paths outside the library must not be deleted by auto_purge."""
    lib = tmp_path / "library"
    lib.mkdir()
    (lib / "photos").mkdir()
    (lib / "data").mkdir()
    (lib / "cache").mkdir()
    (lib / "logs").mkdir()

    # Create a file OUTSIDE the library
    outside = tmp_path / "outside_file.jpg"
    outside.write_bytes(b"\xff\xd8\xff" + b"\x00" * 50)

    app = create_app(
        workdir=str(lib / "data"),
        input_dir=str(lib),
        library_path=str(lib),
    )
    app.config["TESTING"] = True
    ctx = app.extensions["bpp"]

    # Insert a photo record pointing outside the library, soft-delete it,
    # and backdate deleted_at so auto_purge picks it up.
    with app.app_context():
        conn = ctx.get_conn()
        pid = upsert_photo(
            conn,
            {
                "filepath": str(outside),
                "original_filename": "outside_file.jpg",
                "file_size": 53,
                "file_mtime": 1000.0,
            },
        )
        soft_delete_photos(conn, [pid])
        # Backdate to 60 days ago
        conn.execute(
            "UPDATE photos SET deleted_at = datetime('now', '-60 days') WHERE id=?",
            (pid,),
        )
        conn.commit()

        ctx.auto_purge()

        # File outside library must NOT be deleted
        assert outside.exists(), "auto_purge deleted a file outside the library!"


def test_auto_purge_deletes_files_inside_library(tmp_path):
    """Files inside the library should be deleted normally."""
    lib = tmp_path / "library"
    lib.mkdir()
    photos_dir = lib / "photos"
    photos_dir.mkdir()
    (lib / "data").mkdir()
    (lib / "cache").mkdir()
    (lib / "logs").mkdir()

    inside = photos_dir / "photo.jpg"
    inside.write_bytes(b"\xff\xd8\xff" + b"\x00" * 50)

    app = create_app(
        workdir=str(lib / "data"),
        input_dir=str(lib),
        library_path=str(lib),
    )
    app.config["TESTING"] = True
    ctx = app.extensions["bpp"]

    with app.app_context():
        conn = ctx.get_conn()
        pid = upsert_photo(
            conn,
            {
                "filepath": str(inside),
                "original_filename": "photo.jpg",
                "file_size": 53,
                "file_mtime": 1000.0,
            },
        )
        soft_delete_photos(conn, [pid])
        conn.execute(
            "UPDATE photos SET deleted_at = datetime('now', '-60 days') WHERE id=?",
            (pid,),
        )
        conn.commit()

        ctx.auto_purge()

        assert not inside.exists(), "auto_purge should have deleted file inside library"

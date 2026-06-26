"""TDD tests for H-8: batch_name path traversal sanitization."""

from __future__ import annotations

import os
import sqlite3

import pytest
from PIL import Image

from bpp.db.connection import init_db
from bpp.db.library import import_folder


@pytest.fixture()
def setup(tmp_path):
    """Create a library dir, source dir with an image, and a DB connection."""
    lib = tmp_path / "library"
    lib.mkdir()
    (lib / "photos").mkdir()
    (lib / "data").mkdir()
    (lib / "cache").mkdir()
    (lib / "logs").mkdir()

    src = tmp_path / "source"
    src.mkdir()
    Image.new("RGB", (10, 10), "red").save(str(src / "img.jpg"), "JPEG")

    db_path = str(lib / "data" / "photopicker.db")
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn, str(src), str(lib)


class TestBatchNameSanitization:
    def test_path_traversal_stripped(self, setup):
        """batch_name with ../ must not escape photos dir."""
        conn, src, lib = setup
        import_folder(conn, src, lib, batch_name="../../escape")
        photos_dir = os.path.join(lib, "photos")
        # Files must land inside photos/escape/, not ../../escape/
        assert os.path.isdir(os.path.join(photos_dir, "escape"))
        # Ensure nothing was created outside library
        assert not os.path.exists(os.path.join(lib, "..", "escape"))

    def test_absolute_path_stripped(self, setup):
        """batch_name with absolute path must be reduced to basename."""
        conn, src, lib = setup
        import_folder(conn, src, lib, batch_name="/tmp/evil")
        photos_dir = os.path.join(lib, "photos")
        assert os.path.isdir(os.path.join(photos_dir, "evil"))

    def test_normal_batch_name_unchanged(self, setup):
        """A simple batch name works as before."""
        conn, src, lib = setup
        import_folder(conn, src, lib, batch_name="my_import")
        photos_dir = os.path.join(lib, "photos")
        assert os.path.isdir(os.path.join(photos_dir, "my_import"))

    def test_slash_in_batch_name_stripped(self, setup):
        """batch_name with embedded slashes is reduced to final component."""
        conn, src, lib = setup
        import_folder(conn, src, lib, batch_name="foo/bar/baz")
        photos_dir = os.path.join(lib, "photos")
        assert os.path.isdir(os.path.join(photos_dir, "baz"))

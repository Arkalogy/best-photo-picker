"""Shared fixtures and helpers of the test_enhance.py suite (split for the 500-LOC cap)."""

from __future__ import annotations

import pytest
from PIL import Image

from bpp.db.connection import get_db, init_db
from bpp.db.photos import upsert_photo


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "test_enhance.db")
    init_db(path)
    return path


@pytest.fixture
def conn(db_path):
    return get_db(db_path)


def _make_photo(conn, tmp_path, name, size=(200, 200), color="gray"):
    f = tmp_path / name
    img = Image.new("RGB", size, color)
    img.save(str(f), "JPEG")
    photo = {"filepath": str(f)}
    pid = upsert_photo(conn, photo)
    return pid, str(f)


def _make_dark_photo(tmp_path, name):
    """Create a dark underexposed image."""
    f = tmp_path / name
    img = Image.new("RGB", (200, 200), (30, 25, 20))
    img.save(str(f), "JPEG")
    return str(f)


def _make_bright_photo(tmp_path, name):
    """Create an already well-exposed image."""
    f = tmp_path / name
    img = Image.new("RGB", (200, 200), (180, 170, 160))
    img.save(str(f), "JPEG")
    return str(f)


def _make_dull_photo(tmp_path, name):
    """Create a low-saturation grayish image."""
    f = tmp_path / name
    img = Image.new("RGB", (200, 200), (120, 118, 116))
    img.save(str(f), "JPEG")
    return str(f)

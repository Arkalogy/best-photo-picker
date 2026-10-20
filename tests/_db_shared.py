"""Shared fixtures and helpers of the test_db.py suite (split for the 500-LOC cap)."""

from __future__ import annotations

import pytest

from bpp.db.connection import get_db, init_db


@pytest.fixture
def db_path(tmp_path):
    """Create a temporary database and return its path."""
    path = str(tmp_path / "test.db")
    init_db(path)
    return path


@pytest.fixture
def conn(db_path):
    """Return a connection to the test database."""
    return get_db(db_path)


@pytest.fixture
def sample_image(tmp_path):
    """Create a small dummy image file and return its path."""
    img_path = tmp_path / "IMG_0001.jpg"
    img_path.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
    return str(img_path)


@pytest.fixture
def sample_photos(tmp_path):
    """Create multiple dummy image files and return their paths."""
    paths = []
    for i in range(5):
        p = tmp_path / f"IMG_{i:04d}.jpg"
        p.write_bytes(b"\xff\xd8\xff\xe0" + bytes([i]) * 100)
        paths.append(str(p))
    return paths

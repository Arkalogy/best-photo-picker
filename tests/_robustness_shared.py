"""Shared fixtures and helpers of the test_robustness.py suite (split for the 500-LOC cap)."""

from __future__ import annotations

import pytest

from bpp.db.connection import (
    close_all_connections,
    init_db,
)
from bpp.db.photos import (
    upsert_photo,
)


@pytest.fixture
def db_path(tmp_path):
    """Create a real SQLite DB and return its path."""
    p = str(tmp_path / "test.db")
    conn = init_db(p)
    # Insert some data so it's not empty
    upsert_photo(conn, {"filepath": "/fake/photo1.jpg", "sha256": "aaa"})
    upsert_photo(conn, {"filepath": "/fake/photo2.jpg", "sha256": "bbb"})
    conn.close()
    # Clear thread-local so next get_db creates a fresh connection
    close_all_connections()
    return p


@pytest.fixture
def conn(tmp_path):
    """Return a connection to a fresh test DB."""
    p = str(tmp_path / "test.db")
    c = init_db(p)
    yield c
    close_all_connections()

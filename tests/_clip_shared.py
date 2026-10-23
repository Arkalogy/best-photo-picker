"""Shared fixtures and helpers of the test_clip.py suite (split for the 500-LOC cap)."""

from __future__ import annotations

import sqlite3

import pytest

from bpp.db.schema import create_tables


@pytest.fixture()
def db():
    """In-memory SQLite DB with all tables."""
    conn = sqlite3.connect(":memory:")
    # Row factory matches the production get_db() pool. get_setting()
    # reads `row["value"]`, which needs this — without it the new cap-
    # override tests trip on raw tuple indexing in settings.py.
    conn.row_factory = sqlite3.Row
    create_tables(conn)
    # Insert test photos
    for i in range(5):
        conn.execute(
            "INSERT INTO photos (filepath, original_filename, file_size, file_mtime, date,"
            " date_day, date_month, aggregate_score)"
            " VALUES (?, ?, 1000, 1.0, ?, ?, ?, ?)",
            (
                f"/photo_{i}.jpg",
                f"photo_{i}.jpg",
                f"2024-01-01T12:00:0{i}",
                "2024-01-01",
                "2024-01",
                0.5 + i * 0.1,
            ),
        )
    conn.commit()
    yield conn
    conn.close()

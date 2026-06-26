"""Tests for get_library_stats — format_breakdown must use SQL, not O(N) Python."""

from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture
def mem_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE photos ("
        "id INTEGER PRIMARY KEY, filepath TEXT, file_size INTEGER, "
        "is_video INTEGER DEFAULT 0, is_raw INTEGER DEFAULT 0, deleted_at TEXT, "
        "is_live_photo_sidecar INTEGER DEFAULT 0"
        ")"
    )
    return conn


def _insert(conn, filepath, file_size=1024, is_video=0, is_raw=0, deleted=False):
    conn.execute(
        "INSERT INTO photos (filepath, file_size, is_video, is_raw, deleted_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (filepath, file_size, is_video, is_raw, "2024-01-01" if deleted else None),
    )
    conn.commit()


class TestFormatBreakdown:
    def test_counts_extensions_correctly(self, mem_conn):
        from bpp.db.stats import get_library_stats

        _insert(mem_conn, "/lib/a.jpg")
        _insert(mem_conn, "/lib/b.jpg")
        _insert(mem_conn, "/lib/c.PNG")
        _insert(mem_conn, "/lib/d.HEIC")

        stats = get_library_stats(mem_conn)
        bd = stats["format_breakdown"]
        assert bd.get(".jpg") == 2, f"expected 2 .jpg, got {bd}"
        assert bd.get(".png") == 1, f"expected 1 .png (lowercased), got {bd}"
        assert bd.get(".heic") == 1, f"expected 1 .heic (lowercased), got {bd}"

    def test_excludes_deleted_photos(self, mem_conn):
        from bpp.db.stats import get_library_stats

        _insert(mem_conn, "/lib/alive.jpg")
        _insert(mem_conn, "/lib/dead.jpg", deleted=True)

        stats = get_library_stats(mem_conn)
        assert stats["format_breakdown"].get(".jpg") == 1

    def test_no_python_loop_over_filepaths(self):
        """The format breakdown must NOT load all filepaths into Python.

        Source-scanning test: the implementation must not contain a query that
        fetches only 'filepath' without a GROUP BY — that's the O(N) pattern.
        """
        import inspect

        from bpp.db.stats import get_library_stats

        src = inspect.getsource(get_library_stats)
        # The old O(N) query was: SELECT filepath FROM photos WHERE deleted_at IS NULL
        # with no GROUP BY. Detect the anti-pattern directly.
        assert "SELECT filepath FROM photos" not in src, (
            "get_library_stats fetches all filepaths into Python for extension counting. "
            "Use SQL GROUP BY instead."
        )

    def test_empty_library(self, mem_conn):
        from bpp.db.stats import get_library_stats

        stats = get_library_stats(mem_conn)
        assert stats["total_count"] == 0
        assert stats["format_breakdown"] == {}


class TestSidecarsExcludedFromStats:
    """Library stats must not count hidden Live Photo sidecar rows.

    Regression (2026-06-12): the sidebar footer read "6,223 items" on a
    3,150-photo library — both stats queries counted sidecar duplicate
    placeholder rows.
    """

    def test_sidecars_do_not_inflate_counts_or_size(self, mem_conn):
        from bpp.db.stats import get_library_stats

        _insert(mem_conn, "/x/a.jpg", file_size=100)
        mem_conn.execute(
            "INSERT INTO photos (filepath, file_size, is_live_photo_sidecar) "
            "VALUES ('/x/a_1.jpg', 100, 1)"
        )
        mem_conn.commit()
        stats = get_library_stats(mem_conn)
        assert stats["total_count"] == 1, stats
        assert stats["total_size"] == 100, stats

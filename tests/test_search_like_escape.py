"""TDD tests for M-6: LIKE queries must escape % and _ wildcards."""

from __future__ import annotations

import sqlite3


def _escape_like(s: str) -> str:
    """Import the escape function from bp_search (will exist after fix)."""
    from bpp.web.bp_search import _escape_like

    return _escape_like(s)


def test_escape_like_percent():
    assert _escape_like("%") == r"\%"


def test_escape_like_underscore():
    assert _escape_like("_") == r"\_"


def test_escape_like_backslash():
    assert _escape_like(r"hello\world") == r"hello\\world"


def test_escape_like_normal_text():
    assert _escape_like("cats") == "cats"


def test_escape_like_mixed():
    assert _escape_like("100%_done") == r"100\%\_done"


def test_escaped_like_in_sql():
    """Verify the escaped pattern works correctly in SQLite."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (name TEXT)")
    conn.execute("INSERT INTO t VALUES ('normal.jpg')")
    conn.execute("INSERT INTO t VALUES ('100%_done.jpg')")
    conn.execute("INSERT INTO t VALUES ('x.jpg')")

    escaped = _escape_like("%")
    # Should only match the file containing literal '%'
    rows = conn.execute(
        r"SELECT name FROM t WHERE name LIKE ? ESCAPE '\'",
        (f"%{escaped}%",),
    ).fetchall()
    names = [r[0] for r in rows]
    assert names == ["100%_done.jpg"]

    escaped_u = _escape_like("_")
    rows = conn.execute(
        r"SELECT name FROM t WHERE name LIKE ? ESCAPE '\'",
        (f"%{escaped_u}%",),
    ).fetchall()
    names = [r[0] for r in rows]
    assert names == ["100%_done.jpg"]

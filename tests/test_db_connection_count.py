"""R5-M2: live DB-connection observability warning.

The thread-local connection model is intentional but unbounded —
Werkzeug's dev server creates one thread per request and each thread
opens one SQLite connection that lives for the thread's lifetime.
When the thread dies the connection is collected and its file
descriptors are released.

`_conn_count` tracks connections open *right now* (``get_db``
increments; a ``weakref.finalize`` callback decrements on collection
or explicit close), and we warn once the live count climbs into a
range that threatens the process's real ``RLIMIT_NOFILE``. The budget
is derived from the actual FD limit, not a hardcoded 256, so the
warning stays silent through normal request-thread churn and only
fires on a genuine connection leak. We never refuse to allocate —
failing requests is worse than burning FDs on a single-user local
app — but the warning points at the right diagnosis.
"""

from __future__ import annotations

import gc
import logging
import sqlite3
import threading

import pytest


@pytest.fixture
def _isolated_counter():
    """Reset the live counter + warn latch around each test, sweeping
    any connections a prior test left collectable first so their
    finalizers don't decrement mid-test."""
    from bpp.db import connection

    gc.collect()
    connection._reset_connection_count()
    yield
    gc.collect()
    connection._reset_connection_count()


@pytest.fixture
def _low_budget(monkeypatch):
    """Drop the warn budget to 3 so tests can stress the latch without
    opening hundreds of real connections."""
    from bpp.db import connection

    monkeypatch.setattr(connection, "_CONN_WARN_BUDGET", 3)


def _open_db_in_thread(db_path: str) -> sqlite3.Connection:
    """Open a connection from a fresh thread (so the thread-local
    cache-miss path runs and the counter increments), then return it.

    The connection outlives the thread because sqlite3 is opened with
    ``check_same_thread=False``; the *caller* must keep the returned
    reference to keep the connection counted as live."""
    from bpp.db.connection import get_db

    out: dict[str, sqlite3.Connection | None] = {"conn": None}

    def _run():
        out["conn"] = get_db(db_path)

    t = threading.Thread(target=_run)
    t.start()
    t.join()
    return out["conn"]  # type: ignore[return-value]


class TestConnectionCounter:
    def test_first_open_increments_counter(self, tmp_path, _isolated_counter):
        from bpp.db.connection import _connection_count

        db = str(tmp_path / "x.db")
        before = _connection_count()
        conn = _open_db_in_thread(db)  # hold the ref → stays live
        after = _connection_count()
        assert after == before + 1, (
            f"Expected counter to increment by 1, got before={before}, after={after}"
        )
        del conn
        gc.collect()

    def test_collected_connection_decrements_counter(self, tmp_path, _isolated_counter):
        """The core of live-counting: a connection from a now-dead
        request thread must DECREMENT the count when it's collected,
        so benign Werkzeug thread churn doesn't drift the count up
        forever and false-trigger the warning."""
        from bpp.db.connection import _connection_count

        db = str(tmp_path / "x.db")
        before = _connection_count()
        conn = _open_db_in_thread(db)
        assert _connection_count() == before + 1
        del conn
        gc.collect()
        assert _connection_count() == before, (
            "Expected the live count to return to baseline after the "
            f"connection was collected, got {_connection_count()} vs {before}"
        )

    def test_warn_fires_once_after_budget_crossed(
        self, tmp_path, caplog, _isolated_counter, _low_budget
    ):
        """Latched: fire the first time live connections cross the
        budget, not on every subsequent open — log spam defeats the
        breadcrumb."""
        db = str(tmp_path / "x.db")
        conns = []  # hold refs so all connections stay live
        with caplog.at_level(logging.WARNING, logger="bpp.db.connection"):
            for _ in range(5):  # budget is 3
                conns.append(_open_db_in_thread(db))

        warns = [
            r
            for r in caplog.records
            if r.name == "bpp.db.connection" and "connection count crossed" in r.message
        ]
        assert len(warns) == 1, (
            f"Expected exactly one budget warning, got {len(warns)}: {[r.message for r in warns]}"
        )
        # The message reports the current live count, not the budget.
        assert "now 4" in warns[0].message or "now 5" in warns[0].message

        conns.clear()
        gc.collect()

    def test_below_budget_no_warning(self, tmp_path, caplog, _isolated_counter, _low_budget):
        """Inverse: staying at or below the budget must not warn — the
        breadcrumb is for a genuine leak, not normal traffic."""
        db = str(tmp_path / "x.db")
        conns = []
        with caplog.at_level(logging.WARNING, logger="bpp.db.connection"):
            for _ in range(3):  # budget is 3, so 3 is fine
                conns.append(_open_db_in_thread(db))

        warns = [
            r
            for r in caplog.records
            if r.name == "bpp.db.connection" and "connection count crossed" in r.message
        ]
        assert not warns, (
            f"Did not expect any warnings at or below budget, got: {[r.message for r in warns]}"
        )

        conns.clear()
        gc.collect()

    def test_warn_rearms_after_dropping_below_half(
        self, tmp_path, caplog, _isolated_counter, _low_budget
    ):
        """Hysteresis: after the count falls back below half the budget
        the latch re-arms, so a *second* genuine spike warns again
        rather than being silently swallowed by the first latch."""
        db = str(tmp_path / "x.db")

        with caplog.at_level(logging.WARNING, logger="bpp.db.connection"):
            # First spike: cross the budget (3) → one warning.
            conns = [_open_db_in_thread(db) for _ in range(5)]
            # Drain back below half (budget // 2 == 1): release them
            # and collect so the finalizers run and re-arm the latch.
            conns.clear()
            gc.collect()
            # Second spike: cross the budget again → a second warning.
            conns = [_open_db_in_thread(db) for _ in range(5)]

        warns = [
            r
            for r in caplog.records
            if r.name == "bpp.db.connection" and "connection count crossed" in r.message
        ]
        assert len(warns) == 2, (
            f"Expected the warning to re-arm and fire twice, got {len(warns)}: "
            f"{[r.message for r in warns]}"
        )

        conns.clear()
        gc.collect()

    def test_close_all_connections_decrements(self, tmp_path, _isolated_counter):
        """Explicit close must decrement deterministically even while
        the caller still holds the connection reference — shutdown
        can't depend on GC timing."""
        from bpp.db import connection
        from bpp.db.connection import _connection_count, close_all_connections

        db = str(tmp_path / "x.db")

        # Open in main thread (so close_all_connections, called from
        # the same thread, can see and close it).
        conn = connection.get_db(db)
        assert conn is not None
        opened = _connection_count()
        assert opened >= 1

        close_all_connections()
        # NB: we still hold `conn`, yet the count must have dropped —
        # close_all_connections runs the finalizer explicitly rather
        # than waiting for the reference to go away.
        assert _connection_count() == opened - 1, (
            f"Expected counter to decrement by 1 after close, "
            f"got opened={opened}, after_close={_connection_count()}"
        )
        del conn
        gc.collect()

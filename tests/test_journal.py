"""Tests for the operation journal — generic crash-recovery breadcrumb
used by permanent_delete, face clustering, and CLIP extraction.

The journal table itself is small and the contract is small. These
tests pin: insert/complete/list semantics, kind-filtered queries,
recovery handler dispatch (only handlers registered for a kind run),
and graceful behavior on corrupt payloads.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def conn(tmp_path):
    from bpp.db.connection import get_db, init_db

    db_path = str(tmp_path / "j.db")
    init_db(db_path)
    return get_db(db_path)


@pytest.fixture(autouse=True)
def _reset_handlers():
    """Each test starts with an empty handler registry."""
    from bpp.db.journal import _reset_handlers_for_tests

    _reset_handlers_for_tests()
    yield
    _reset_handlers_for_tests()


class TestJournalLifecycle:
    def test_start_returns_id(self, conn):
        from bpp.db.journal import journal_start

        jid = journal_start(conn, "test_kind", {"foo": "bar"})
        assert jid > 0

    def test_pending_lists_entry(self, conn):
        from bpp.db.journal import journal_start, pending_journals

        jid = journal_start(conn, "test_kind", {"foo": 1})
        rows = pending_journals(conn)
        assert len(rows) == 1
        assert rows[0]["id"] == jid
        assert rows[0]["kind"] == "test_kind"
        assert rows[0]["payload"] == {"foo": 1}
        assert rows[0]["started_at"] > 0

    def test_complete_removes_from_pending(self, conn):
        from bpp.db.journal import journal_complete, journal_start, pending_journals

        jid = journal_start(conn, "test_kind", {})
        journal_complete(conn, jid)
        assert pending_journals(conn) == []

    def test_pending_filter_by_kind(self, conn):
        from bpp.db.journal import journal_start, pending_journals

        journal_start(conn, "kind_a", {})
        journal_start(conn, "kind_b", {})
        a = pending_journals(conn, kind="kind_a")
        b = pending_journals(conn, kind="kind_b")
        assert len(a) == 1 and a[0]["kind"] == "kind_a"
        assert len(b) == 1 and b[0]["kind"] == "kind_b"

    def test_pending_orders_by_started_at(self, conn):
        import time

        from bpp.db.journal import journal_start, pending_journals

        first = journal_start(conn, "k", {"i": 1})
        time.sleep(1.05)  # journal stores integer seconds — need full-second gap
        second = journal_start(conn, "k", {"i": 2})
        rows = pending_journals(conn)
        assert [r["id"] for r in rows] == [first, second]


class TestRecoveryHandlerDispatch:
    def test_handler_called_per_pending_entry(self, conn):
        from bpp.db.journal import (
            journal_start,
            recover_pending,
            register_recovery_handler,
        )

        seen: list[dict] = []

        def handler(_conn, payload):
            seen.append(payload)
            return True

        register_recovery_handler("test_kind", handler)
        journal_start(conn, "test_kind", {"a": 1})
        journal_start(conn, "test_kind", {"b": 2})

        result = recover_pending(conn)
        assert result == {"test_kind": 2}
        assert seen == [{"a": 1}, {"b": 2}]

    def test_handler_returning_false_leaves_entry(self, conn):
        from bpp.db.journal import (
            journal_start,
            pending_journals,
            recover_pending,
            register_recovery_handler,
        )

        register_recovery_handler("flaky", lambda _c, _p: False)
        journal_start(conn, "flaky", {})
        recover_pending(conn)
        # Entry should still be pending — handler said "couldn't recover"
        assert len(pending_journals(conn)) == 1

    def test_unregistered_kind_left_untouched(self, conn):
        from bpp.db.journal import (
            journal_start,
            pending_journals,
            recover_pending,
        )

        journal_start(conn, "no_handler_for_this", {"x": 1})
        result = recover_pending(conn)
        assert result == {}
        assert len(pending_journals(conn)) == 1

    def test_handler_exception_leaves_entry(self, conn):
        from bpp.db.journal import (
            journal_start,
            pending_journals,
            recover_pending,
            register_recovery_handler,
        )

        def boom(_c, _p):
            raise RuntimeError("simulated handler failure")

        register_recovery_handler("explodes", boom)
        journal_start(conn, "explodes", {})
        recover_pending(conn)
        # Entry survives so an operator can investigate
        assert len(pending_journals(conn)) == 1

    def test_register_same_handler_idempotent(self, conn):
        from bpp.db.journal import register_recovery_handler

        def h(_c, _p):
            return True

        register_recovery_handler("k", h)
        register_recovery_handler("k", h)  # no error

    def test_register_different_handler_for_same_kind_raises(self):
        from bpp.db.journal import register_recovery_handler

        register_recovery_handler("collide", lambda _c, _p: True)
        with pytest.raises(ValueError, match="already registered"):
            register_recovery_handler("collide", lambda _c, _p: False)


class TestCorruptPayload:
    def test_corrupt_json_returns_empty_dict(self, conn):
        from bpp.db.journal import pending_journals

        # Insert a row with malformed JSON directly
        conn.execute(
            "INSERT INTO operation_journal (kind, payload_json, started_at) VALUES (?, ?, ?)",
            ("test_kind", "{not json}", 12345),
        )
        conn.commit()
        rows = pending_journals(conn)
        # Entry surfaces, payload is empty dict (graceful) — recovery
        # handler can decide what to do with a missing payload.
        assert len(rows) == 1
        assert rows[0]["payload"] == {}

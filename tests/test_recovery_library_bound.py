"""P1.5 — recovery handlers refuse to fire after a library switch.

The audit identified a data-corruption window: ``WebAppState`` is mutated
in place by ``switch_library``; recovery handlers that close over ``ctx``
would happily fire with the new library's resolved paths even though the
pending journal entry described work for the old library.

:func:`bpp.db.journal.library_bound_recovery` is the wrapper that closes
that hole. The handler captures the library path at registration time
and refuses (returns ``False``, leaving the journal entry in place) if
the live ctx reports a different path at fire time.
"""

from __future__ import annotations

import sqlite3

import pytest

from bpp.db.journal import library_bound_recovery


@pytest.fixture
def fake_conn():
    """In-memory SQLite — the wrapper never touches it, but the
    signature requires one."""
    conn = sqlite3.connect(":memory:")
    yield conn
    conn.close()


class TestLibraryBoundRecovery:
    def test_fires_when_library_matches(self, fake_conn):
        """Same library at registration and fire time → handler runs."""
        ran: list[bool] = []

        def _inner(_conn, _payload):
            ran.append(True)
            return True

        guarded = library_bound_recovery(
            "/lib/A",
            _inner,
            library_path_getter=lambda: "/lib/A",
        )
        assert guarded(fake_conn, {}) is True
        assert ran == [True], "inner handler must run when library matches"

    def test_refuses_when_library_changed(self, fake_conn):
        """Library switched between registration and fire → handler refuses."""
        ran: list[bool] = []

        def _inner(_conn, _payload):
            ran.append(True)
            return True

        guarded = library_bound_recovery(
            "/lib/A",
            _inner,
            library_path_getter=lambda: "/lib/B",
        )
        assert guarded(fake_conn, {}) is False
        assert ran == [], "inner handler must NOT run after switch"

    def test_refuses_when_ctx_gone(self, fake_conn):
        """No live ctx (None) → still a refuse, not a crash."""

        def _inner(_conn, _payload):
            pytest.fail("inner handler must not run when ctx is gone")

        guarded = library_bound_recovery(
            "/lib/A",
            _inner,
            library_path_getter=lambda: None,
        )
        assert guarded(fake_conn, {}) is False

    def test_getter_is_re_read_each_call(self, fake_conn):
        """The getter is called per-fire, not once at construction.

        This is the load-bearing property: the wrapper must see the
        current ctx, not the ctx at construction time.
        """
        current_lib = ["/lib/A"]
        ran: list[bool] = []

        def _getter():
            return current_lib[0]

        def _inner(_conn, _payload):
            ran.append(True)
            return True

        guarded = library_bound_recovery("/lib/A", _inner, library_path_getter=_getter)
        # First fire: matches
        assert guarded(fake_conn, {}) is True
        # Switch happens
        current_lib[0] = "/lib/B"
        # Second fire: must refuse, even though prior fire succeeded
        assert guarded(fake_conn, {}) is False
        assert ran == [True], "second fire must NOT call inner"

    def test_propagates_inner_return_when_library_matches(self, fake_conn):
        """When the inner handler returns False (recovery failed),
        the wrapper passes that through — it doesn't accidentally
        return True just because the library check passed."""

        def _inner(_conn, _payload):
            return False

        guarded = library_bound_recovery("/lib/A", _inner, library_path_getter=lambda: "/lib/A")
        assert guarded(fake_conn, {}) is False


class TestRealHandlersGetWrapped:
    """When the state lifecycle wires handlers with a library_path,
    each registration site must produce a guarded callable, not the
    raw recover function. Belt-and-suspenders: the lifecycle plumbing
    is what makes the wrapper effective in production."""

    def test_face_extraction_retry_wraps_when_library_path_given(self):
        from bpp.db.journal import _RECOVERY_HANDLERS, _reset_handlers_for_tests
        from bpp.web.face_worker import register_face_extraction_retry_recovery

        _reset_handlers_for_tests()
        register_face_extraction_retry_recovery(library_path="/lib/A")
        handler = _RECOVERY_HANDLERS["face_extraction_retry"]
        # The guarded wrapper has its own qualname; the raw _recover
        # closure does not. This is the cheapest non-mocking check
        # that the wrapper is in place.
        assert handler.__name__ == "_guarded"
        _reset_handlers_for_tests()

    def test_face_extraction_retry_unwrapped_when_no_library_path(self):
        from bpp.db.journal import _RECOVERY_HANDLERS, _reset_handlers_for_tests
        from bpp.web.face_worker import register_face_extraction_retry_recovery

        _reset_handlers_for_tests()
        register_face_extraction_retry_recovery()  # legacy / back-compat path
        handler = _RECOVERY_HANDLERS["face_extraction_retry"]
        assert handler.__name__ == "_recover"
        _reset_handlers_for_tests()

    def test_permanent_delete_wraps_when_library_path_given(self):
        from bpp.db.journal import _RECOVERY_HANDLERS, _reset_handlers_for_tests
        from bpp.web.bp_photos_lifecycle import register_permanent_delete_recovery

        _reset_handlers_for_tests()
        ctx = object()  # opaque — wrapper only reads .paths.library_path
        register_permanent_delete_recovery(ctx, library_path="/lib/A")
        handler = _RECOVERY_HANDLERS["permanent_delete"]
        assert handler.__name__ == "_guarded"
        _reset_handlers_for_tests()

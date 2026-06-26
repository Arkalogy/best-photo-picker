"""P5b — Plugin protocol + lifecycle hooks.

Verifies:

* :func:`register_plugin` is idempotent on identity.
* ``fire_on_register`` / ``fire_on_library_open`` /
  ``fire_on_library_close`` / ``fire_on_shutdown`` call the matching
  method on every registered plugin that has it.
* Plugins that implement only some hooks (the duck-typed
  presence-only shape) don't crash the host.
* A misbehaving plugin's exception is logged + swallowed so other
  plugins still fire.
* ``fire_on_library_close`` / ``fire_on_shutdown`` fire in reverse
  registration order (LIFO mirrors context-manager semantics).
"""

from __future__ import annotations

import logging

import pytest

from bpp.plugin_protocol import (
    _reset_plugins_for_tests,
    fire_on_db_restore_if_pending,
    fire_on_library_close,
    fire_on_library_open,
    fire_on_register,
    fire_on_shutdown,
    note_db_restore,
    register_plugin,
)

# ── Stub plugins ──


class _RecordingPlugin:
    """All four hooks; record the call sequence with ordering metadata."""

    def __init__(self, label: str, log: list[tuple[str, str, tuple]]) -> None:
        self.label = label
        self.log = log

    def on_register(self, app):
        self.log.append((self.label, "on_register", (app,)))

    def on_library_open(self, ctx):
        self.log.append((self.label, "on_library_open", (ctx,)))

    def on_library_close(self, ctx):
        self.log.append((self.label, "on_library_close", (ctx,)))

    def on_db_restore(self, corrupted_sidecar_path):
        self.log.append((self.label, "on_db_restore", (corrupted_sidecar_path,)))

    def on_shutdown(self):
        self.log.append((self.label, "on_shutdown", ()))


class _RegisterOnlyPlugin:
    """Only implements on_register — exercises the duck-typed
    presence-only branch."""

    def __init__(self, log: list[str]) -> None:
        self.log = log

    def on_register(self, app):
        self.log.append("register-only")


class _RaisingPlugin:
    """Raises in every hook — exercises the per-plugin exception
    swallowing in :func:`_safe_fire`."""

    def on_register(self, app):
        raise RuntimeError("kaboom on_register")

    def on_db_restore(self, corrupted_sidecar_path):
        raise RuntimeError("kaboom on_db_restore")

    def on_library_open(self, ctx):
        raise RuntimeError("kaboom on_library_open")

    def on_library_close(self, ctx):
        raise RuntimeError("kaboom on_library_close")

    def on_shutdown(self):
        raise RuntimeError("kaboom on_shutdown")


@pytest.fixture(autouse=True)
def _reset():
    """Clear the global plugin list between tests."""
    _reset_plugins_for_tests()
    yield
    _reset_plugins_for_tests()


# ── Registration ──


class TestRegistration:
    def test_identity_idempotent(self):
        p = _RecordingPlugin("a", [])
        register_plugin(p)
        register_plugin(p)
        register_plugin(p)
        # Same instance registered three times — fires only once.
        log: list = []
        p.log = log
        fire_on_register(None)
        assert len(log) == 1

    def test_two_distinct_instances_both_registered(self):
        log: list = []
        register_plugin(_RecordingPlugin("a", log))
        register_plugin(_RecordingPlugin("b", log))
        fire_on_register(None)
        labels = [entry[0] for entry in log]
        assert labels == ["a", "b"]


# ── Hook firing ──


class TestFireHooks:
    def test_on_register_passes_app(self):
        log: list = []
        register_plugin(_RecordingPlugin("a", log))
        sentinel = object()
        fire_on_register(sentinel)
        assert log == [("a", "on_register", (sentinel,))]

    def test_on_library_open_passes_ctx(self):
        log: list = []
        register_plugin(_RecordingPlugin("a", log))
        ctx = object()
        fire_on_library_open(ctx)
        assert log == [("a", "on_library_open", (ctx,))]

    def test_on_library_close_fires_in_reverse_order(self):
        log: list = []
        register_plugin(_RecordingPlugin("first", log))
        register_plugin(_RecordingPlugin("second", log))
        register_plugin(_RecordingPlugin("third", log))
        fire_on_library_close(object())
        labels = [entry[0] for entry in log]
        # LIFO — third opened last, closes first.
        assert labels == ["third", "second", "first"]

    def test_on_shutdown_fires_in_reverse_order(self):
        log: list = []
        register_plugin(_RecordingPlugin("first", log))
        register_plugin(_RecordingPlugin("second", log))
        fire_on_shutdown()
        labels = [entry[0] for entry in log]
        assert labels == ["second", "first"]


# ── Partial implementations ──


class TestPartialPluginShapes:
    def test_plugin_with_only_one_hook_does_not_crash(self):
        register_log: list[str] = []
        register_plugin(_RegisterOnlyPlugin(register_log))
        fire_on_register(None)
        # All other hooks must be safe to call even though the plugin
        # implements only on_register.
        fire_on_library_open(object())
        fire_on_library_close(object())
        fire_on_shutdown()
        assert register_log == ["register-only"]

    def test_bare_object_with_no_hooks_is_valid(self):
        # A "presence-only" plugin (module imported for side effects;
        # no instance hooks) registered as an empty object is valid.
        register_plugin(object())
        # All four firings must complete cleanly.
        fire_on_register(None)
        fire_on_library_open(object())
        fire_on_library_close(object())
        fire_on_shutdown()


# ── Error isolation ──


class TestErrorIsolation:
    def test_raising_plugin_is_logged_not_propagated(self, caplog):
        good_log: list = []
        register_plugin(_RaisingPlugin())
        register_plugin(_RecordingPlugin("good", good_log))

        with caplog.at_level(logging.WARNING, logger="bpp.plugin_protocol"):
            fire_on_register(None)

        # Good plugin still fired.
        assert len(good_log) == 1
        # Raising plugin's exception was logged.
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("on_register" in r.getMessage() for r in warnings)

    def test_raising_close_doesnt_block_remaining_plugins(self):
        log: list = []
        register_plugin(_RecordingPlugin("a", log))
        register_plugin(_RaisingPlugin())
        register_plugin(_RecordingPlugin("c", log))
        fire_on_library_close(object())
        labels = [entry[0] for entry in log]
        # Reverse order: c first (fires), raiser swallowed, a still fires.
        assert labels == ["c", "a"]

    def test_failure_log_uses_qualified_class_name(self, caplog):
        """T4: ``_safe_fire`` used to log ``plugin`` directly, which
        falls back to ``__repr__``. Plugins that don't override
        ``__repr__`` (most don't) render as
        ``<tests.test_plugin_protocol._RaisingPlugin object at 0x...>``
        — the memory address is noise; the module-qualified class
        name is the actionable signal.

        The fix logs ``f'{module}.{Class}'`` so on-call can grep for
        the offending plugin without parsing pointer addresses.
        """
        register_plugin(_RaisingPlugin())
        with caplog.at_level(logging.WARNING, logger="bpp.plugin_protocol"):
            fire_on_register(None)
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        msgs = " ".join(r.getMessage() for r in warnings)
        assert "_RaisingPlugin" in msgs, (
            f"failure log must include the plugin's class name; got: {msgs!r}"
        )
        # The pointer-address noise from repr must NOT appear. (We don't
        # check absolute absence — the traceback may include one — but
        # the first line of the warning shouldn't.)
        first_line = warnings[0].getMessage()
        assert "object at 0x" not in first_line, (
            f"failure log line must use the class name, not repr's "
            f"<... object at 0x...> form; got: {first_line!r}"
        )


# ── on_db_restore (P-08) ──


class TestOnDbRestore:
    """Protection C's auto-restore fires a deferred signal because
    plugins aren't loaded yet at the time serve.py runs the restore.
    note_db_restore() stashes the corrupt-sidecar path;
    fire_on_db_restore_if_pending() drains and dispatches it after
    plugins have registered."""

    def test_pending_signal_fires_after_plugins_register(self):
        log: list = []
        register_plugin(_RecordingPlugin("a", log))
        register_plugin(_RecordingPlugin("b", log))
        # Note happens BEFORE plugins are registered in production —
        # the order here mirrors that even though pytest constructed
        # them in the opposite order.
        note_db_restore("/lib/photopicker.db.corrupted-1717024400")
        fire_on_db_restore_if_pending()
        calls = [(label, hook) for label, hook, _ in log]
        # Registration order: a then b.
        assert calls == [("a", "on_db_restore"), ("b", "on_db_restore")]
        # The sidecar path threaded through verbatim.
        sidecar_args = [args for _, hook, args in log if hook == "on_db_restore"]
        assert all(args == ("/lib/photopicker.db.corrupted-1717024400",) for args in sidecar_args)

    def test_no_signal_is_a_noop(self):
        log: list = []
        register_plugin(_RecordingPlugin("a", log))
        # No note_db_restore() — fire should be a silent no-op.
        fire_on_db_restore_if_pending()
        assert log == []

    def test_signal_is_one_shot(self):
        log: list = []
        register_plugin(_RecordingPlugin("a", log))
        note_db_restore("/lib/sidecar")
        fire_on_db_restore_if_pending()
        fire_on_db_restore_if_pending()
        # Second fire is a no-op — the signal is drained on first fire.
        calls = [(label, hook) for label, hook, _ in log]
        assert calls == [("a", "on_db_restore")]

    def test_raising_plugin_does_not_block_others(self, caplog):
        good_log: list = []
        register_plugin(_RaisingPlugin())
        register_plugin(_RecordingPlugin("good", good_log))
        note_db_restore("/lib/sidecar")
        with caplog.at_level(logging.WARNING, logger="bpp.plugin_protocol"):
            fire_on_db_restore_if_pending()
        # Good plugin still got its callback.
        assert len(good_log) == 1
        assert good_log[0][1] == "on_db_restore"
        # The raise was logged.
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("on_db_restore" in r.getMessage() for r in warnings)

    def test_partial_plugin_without_hook_is_skipped(self):
        """A plugin that doesn't implement on_db_restore is fine —
        the hook is optional. Verify the dispatcher silently skips it
        without raising AttributeError."""
        log: list = []
        register_plugin(_RegisterOnlyPlugin(log))  # only has on_register
        register_plugin(_RecordingPlugin("full", []))
        note_db_restore("/lib/sidecar")
        # Must not raise.
        fire_on_db_restore_if_pending()
        # The on_register-only plugin never recorded anything in log
        # (it only writes from on_register, which we didn't fire).
        assert log == []


# ── Protocol conformance ──


class TestProtocolConformance:
    def test_recording_plugin_satisfies_protocol_at_runtime(self):
        """:class:`Plugin` is runtime_checkable. A plugin with all four
        methods passes isinstance; one with none should also (because
        every method is optional from the protocol's perspective)."""
        from bpp.plugin_protocol import Plugin

        full = _RecordingPlugin("a", [])
        assert isinstance(full, Plugin)

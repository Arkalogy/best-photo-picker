"""Third-party plugin packages auto-load via entry-points — part 1 of 2."""

# Shared fixtures are imported by name and re-bound as test parameters.
# ruff: noqa: F811

from __future__ import annotations

import importlib.metadata

from tests._plugin_loader_shared import (  # noqa: F401
    _FakeEP,
    _patch_entry_points,
    _reset_loaded,
    plugins_enabled,
)


class TestLoadPluginEntryPoints:
    def test_loads_registered_plugin(self, monkeypatch, plugins_enabled):
        from bpp.config_schema import (
            ConfigField,
            register_field,
            validate_value,
        )
        from bpp.plugins import load_plugin_entry_points

        called = {"count": 0}

        def plugin_main():
            called["count"] += 1
            register_field(ConfigField(key="r10_plugin_demo", type=int))

        _patch_entry_points(
            monkeypatch,
            [_FakeEP("demo", "demo_pkg:setup", plugin_main)],
        )

        loaded = load_plugin_entry_points()
        assert loaded == 1
        assert called["count"] == 1
        # Round-trip via validate_value confirms registration landed.
        assert validate_value("r10_plugin_demo", "42") == 42

    def test_idempotent_on_repeat_calls(self, monkeypatch, plugins_enabled):
        from bpp.plugins import load_plugin_entry_points

        called = {"count": 0}

        def plugin_main():
            called["count"] += 1

        _patch_entry_points(
            monkeypatch,
            [_FakeEP("demo", "demo_pkg:setup", plugin_main)],
        )

        load_plugin_entry_points()
        load_plugin_entry_points()
        load_plugin_entry_points()
        assert called["count"] == 1, (
            "Plugin setup should run exactly once per process even if "
            "the loader is called multiple times (web app + CLI both "
            "invoke it)"
        )

    def test_failed_plugin_does_not_abort_others(self, monkeypatch, caplog, plugins_enabled):
        """A broken plugin must log and the loader must continue with
        the next plugin. Aborting startup on a third-party bug would
        be a denial-of-service vector for any plugin in the venv."""
        from bpp.plugins import load_plugin_entry_points

        good_called = {"count": 0}

        def good_plugin():
            good_called["count"] += 1

        def bad_plugin():
            raise RuntimeError("plugin crashed during setup")

        _patch_entry_points(
            monkeypatch,
            [
                _FakeEP("bad", "bad_pkg:setup", bad_plugin),
                _FakeEP("good", "good_pkg:setup", good_plugin),
            ],
        )

        with caplog.at_level("WARNING", logger="bpp.plugins"):
            loaded = load_plugin_entry_points()

        assert loaded == 2, "loader counts the bad plugin too (one-shot guard)"
        assert good_called["count"] == 1, "good plugin must still run"
        msgs = " ".join(rec.message for rec in caplog.records)
        assert "bad" in msgs and "setup function raised" in msgs

    def test_load_failure_does_not_abort_others(self, monkeypatch, caplog, plugins_enabled):
        """If `ep.load()` itself raises (broken import in the plugin
        package), warn and skip — same contract."""
        from bpp.plugins import load_plugin_entry_points

        good_called = {"count": 0}

        def good_plugin():
            good_called["count"] += 1

        class _ImportFailingEP:
            name = "broken_import"
            value = "broken_pkg:setup"

            def load(self):
                raise ImportError("missing transitive dep")

        _patch_entry_points(
            monkeypatch,
            [_ImportFailingEP(), _FakeEP("good", "good_pkg:setup", good_plugin)],
        )

        with caplog.at_level("WARNING", logger="bpp.plugins"):
            load_plugin_entry_points()

        assert good_called["count"] == 1
        msgs = " ".join(rec.message for rec in caplog.records)
        assert "failed to import" in msgs

    def test_no_plugins_is_a_quiet_noop(self, monkeypatch, plugins_enabled):
        from bpp.plugins import load_plugin_entry_points

        _patch_entry_points(monkeypatch, [])

        # Should not raise, should not log warnings, returns 0.
        assert load_plugin_entry_points() == 0

    def test_metadata_unreadable_is_quiet(self, monkeypatch, plugins_enabled):
        """A corrupt install can make `entry_points()` itself raise.
        The loader must catch and continue — startup never aborts."""
        from bpp.plugins import load_plugin_entry_points

        def boom():
            raise RuntimeError("metadata is corrupt")

        monkeypatch.setattr(importlib.metadata, "entry_points", boom)

        # Just shouldn't raise.
        result = load_plugin_entry_points()
        assert result == 0


class TestPluginsDisabledByDefault:
    """Third-party plugins must be opt-in via
    BPP_ENABLE_PLUGINS=1. Default-on lets any pip-installed package
    declaring `bpp.plugins` execute arbitrary `setup()` code at
    process start — strictly worse for an OSS distribution where
    users may install community plugins from various sources."""

    def test_env_var_unset_skips_loading(self, monkeypatch, caplog):
        """No env var → loader is a no-op even if entry-points exist."""
        from bpp.plugins import load_plugin_entry_points

        # Ensure env var is unset (autouse `_reset_loaded` doesn't
        # touch env vars).
        monkeypatch.delenv("BPP_ENABLE_PLUGINS", raising=False)

        called = {"count": 0}

        def plugin_main():
            called["count"] += 1

        _patch_entry_points(
            monkeypatch,
            [_FakeEP("demo", "demo_pkg:setup", plugin_main)],
        )

        with caplog.at_level("INFO", logger="bpp.plugins"):
            loaded = load_plugin_entry_points()
        assert loaded == 0
        assert called["count"] == 0, "Plugins must NOT load when BPP_ENABLE_PLUGINS is unset"

        # And the operator gets a one-shot INFO breadcrumb so they
        # know why a plugin they installed didn't load.
        msgs = " ".join(rec.message for rec in caplog.records)
        assert "BPP_ENABLE_PLUGINS" in msgs

    def test_env_var_falsy_values_skip_loading(self, monkeypatch):
        from bpp.plugins import load_plugin_entry_points

        called = {"count": 0}

        def plugin_main():
            called["count"] += 1

        _patch_entry_points(
            monkeypatch,
            [_FakeEP("demo", "demo_pkg:setup", plugin_main)],
        )

        for falsy in ("0", "false", "no", "off", "", "anything-else"):
            monkeypatch.setenv("BPP_ENABLE_PLUGINS", falsy)
            assert load_plugin_entry_points() == 0
            assert called["count"] == 0, f"BPP_ENABLE_PLUGINS={falsy!r} must NOT enable plugins"

    def test_env_var_truthy_values_enable(self, monkeypatch):
        from bpp.plugins import _reset_for_tests, load_plugin_entry_points

        called = {"count": 0}

        def plugin_main():
            called["count"] += 1

        _patch_entry_points(
            monkeypatch,
            [_FakeEP("demo", "demo_pkg:setup", plugin_main)],
        )

        for truthy in ("1", "true", "TRUE", "yes", "on", "  1  "):
            _reset_for_tests()
            called["count"] = 0
            monkeypatch.setenv("BPP_ENABLE_PLUGINS", truthy)
            assert load_plugin_entry_points() == 1
            assert called["count"] == 1

    def test_disabled_log_emitted_at_most_once(self, monkeypatch, caplog):
        """Three startup paths call the loader (web app + two CLI).
        With plugins disabled, we don't want the INFO breadcrumb to
        triplicate."""
        from bpp.plugins import load_plugin_entry_points

        monkeypatch.delenv("BPP_ENABLE_PLUGINS", raising=False)

        with caplog.at_level("INFO", logger="bpp.plugins"):
            load_plugin_entry_points()
            load_plugin_entry_points()
            load_plugin_entry_points()

        breadcrumbs = [rec for rec in caplog.records if "BPP_ENABLE_PLUGINS" in rec.message]
        assert len(breadcrumbs) == 1, (
            f"Expected exactly one disabled-plugins breadcrumb; got {len(breadcrumbs)}"
        )


class TestLockReleasedDuringSetup:
    """`_loaded_lock` must NOT cover plugin `target()`
    invocation. A plugin whose setup function takes 5 seconds would
    otherwise serialize every other concurrent loader call for the
    full duration."""

    def test_setup_runs_outside_lock(self, monkeypatch, plugins_enabled):
        from bpp import plugins as plugins_mod

        # Prove the lock isn't held by attempting to acquire it from
        # inside the plugin's setup function. If the loader holds
        # `_loaded_lock` while invoking target(), this acquire would
        # block forever (single-threaded test).
        acquired_during_setup = {"ok": False}

        def plugin_main():
            # blocking=False so a held lock fails fast rather than
            # deadlocking the test.
            got_it = plugins_mod._loaded_lock.acquire(blocking=False)
            if got_it:
                acquired_during_setup["ok"] = True
                plugins_mod._loaded_lock.release()

        _patch_entry_points(
            monkeypatch,
            [_FakeEP("demo", "demo_pkg:setup", plugin_main)],
        )

        plugins_mod.load_plugin_entry_points()
        assert acquired_during_setup["ok"], (
            "_loaded_lock was held during plugin setup() — slow plugins "
            "block other loader calls. Capture target under lock, then "
            "release before invoking."
        )


class TestNonCallableTargetLog:
    """A plugin whose target isn't a callable (relying on
    import-time side effects) used to log only at DEBUG. Promote to
    INFO so plugin authors trying to debug "did my entry land?" see
    a breadcrumb without --debug."""

    def test_non_callable_target_logs_at_info(self, monkeypatch, caplog, plugins_enabled):
        from bpp.plugins import load_plugin_entry_points

        _patch_entry_points(
            monkeypatch,
            [_FakeEP("ssEffect", "demo_pkg:NOT_CALLABLE", "i am not callable")],
        )

        with caplog.at_level("INFO", logger="bpp.plugins"):
            load_plugin_entry_points()

        info_msgs = [rec.message for rec in caplog.records if rec.levelname == "INFO"]
        assert any("import-time side effects" in m for m in info_msgs), (
            "Non-callable target should log at INFO level; got: " + str(info_msgs)
        )


class TestReadPluginMetadata:
    """Loader reads optional module-level metadata before invoking
    setup() so logs surface a friendly plugin label and the version
    contract can refuse a plugin built against a future bpp."""

    def test_reads_module_level_metadata(self, monkeypatch):
        """Plugin metadata lives on the MODULE that defines the target
        callable. The loader looks up `target.__module__` in
        `sys.modules` to find it."""
        import sys
        import types

        from bpp.plugins import _read_plugin_metadata

        fake_module = types.ModuleType("fake_plugin_module_for_test")
        fake_module.__plugin_name__ = "my-plugin"
        fake_module.__plugin_version__ = "2.5.1"
        fake_module.__bpp_version_required__ = ">=0.1,<1.0"
        sys.modules["fake_plugin_module_for_test"] = fake_module
        monkeypatch.setattr(sys, "modules", sys.modules)  # ensure cleanup via monkeypatch

        def target():
            pass

        target.__module__ = "fake_plugin_module_for_test"

        meta = _read_plugin_metadata(target, "demo")
        assert meta == {
            "__plugin_name__": "my-plugin",
            "__plugin_version__": "2.5.1",
            "__bpp_version_required__": ">=0.1,<1.0",
        }

        del sys.modules["fake_plugin_module_for_test"]

    def test_falls_back_to_target_attrs(self):
        """If a plugin author put metadata on the function itself
        (e.g. the entry-point IS the module's setup attribute), the
        loader still picks it up."""
        from bpp.plugins import _read_plugin_metadata

        def target():
            pass

        target.__module__ = "module_that_does_not_exist_xyz"
        target.__plugin_name__ = "fn-attr-plugin"
        target.__plugin_version__ = "0.0.1"

        meta = _read_plugin_metadata(target, "demo")
        assert meta["__plugin_name__"] == "fn-attr-plugin"
        assert meta["__plugin_version__"] == "0.0.1"

    def test_missing_metadata_returns_empty_dict(self):
        """Most plugins won't declare metadata. That's fine — the
        loader falls back to the entry-point id for log lines."""
        from bpp.plugins import _read_plugin_metadata

        def target():
            pass

        meta = _read_plugin_metadata(target, "demo")
        assert meta == {}

    def test_non_string_metadata_ignored(self):
        """A plugin author might typo a version as an int. Skip
        non-string values rather than crash on `f' v{value}'`."""
        from bpp.plugins import _read_plugin_metadata

        def target():
            pass

        target.__module__ = "module_that_does_not_exist_xyz"
        target.__plugin_name__ = 42  # wrong type
        target.__plugin_version__ = ""  # empty string

        meta = _read_plugin_metadata(target, "demo")
        assert meta == {}


class TestCheckBppVersionRequirement:
    """Version-spec parsing on the loader side. The check returns
    False ONLY when the spec parses cleanly AND bpp.__version__
    doesn't satisfy it. Anything ambiguous (empty, unparseable,
    invalid bpp version) returns True so the plugin gets a chance
    to load."""

    def test_empty_spec_returns_true(self):
        from bpp.plugins import _check_bpp_version_requirement

        assert _check_bpp_version_requirement("", "demo") is True

    def test_satisfied_spec_returns_true(self, monkeypatch):
        from bpp import plugins as plugins_mod

        monkeypatch.setattr(plugins_mod, "_BPP_VERSION", "0.5.0")
        assert plugins_mod._check_bpp_version_requirement(">=0.1,<1.0", "demo") is True

    def test_unsatisfied_spec_returns_false(self, monkeypatch):
        from bpp import plugins as plugins_mod

        monkeypatch.setattr(plugins_mod, "_BPP_VERSION", "2.0.0")
        assert plugins_mod._check_bpp_version_requirement(">=0.1,<1.0", "demo") is False

    def test_unparseable_spec_returns_true_with_warning(self, caplog, monkeypatch):
        """A typo'd spec shouldn't block the plugin — log a warning
        and load it anyway. Plugin authors fix typos on their own time;
        an end user shouldn't get their plugin disabled by one."""
        from bpp import plugins as plugins_mod

        monkeypatch.setattr(plugins_mod, "_BPP_VERSION", "0.5.0")
        with caplog.at_level("WARNING", logger="bpp.plugins"):
            ok = plugins_mod._check_bpp_version_requirement("not a real spec", "demo")
        assert ok is True
        assert any("unparseable" in rec.message for rec in caplog.records)

    def test_invalid_bpp_version_returns_true(self, monkeypatch):
        """If `bpp.__version__` somehow isn't valid PEP-440 (e.g. a
        local dev build), don't refuse plugins — log debug and load."""
        from bpp import plugins as plugins_mod

        monkeypatch.setattr(plugins_mod, "_BPP_VERSION", "git-abcdef")
        assert plugins_mod._check_bpp_version_requirement(">=0.1", "demo") is True

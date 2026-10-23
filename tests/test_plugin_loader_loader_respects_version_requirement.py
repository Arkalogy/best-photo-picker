"""Third-party plugin packages auto-load via entry-points — part 2 of 2:
TestLoaderRespectsVersionRequirement, TestLoaderWiredAtStartup."""

# Shared fixtures are imported by name and re-bound as test parameters.
# ruff: noqa: F811

from __future__ import annotations

from tests._plugin_loader_shared import (  # noqa: F401
    _FakeEP,
    _patch_entry_points,
    _reset_loaded,
    plugins_enabled,
)


class TestLoaderRespectsVersionRequirement:
    """End-to-end: a plugin whose `__bpp_version_required__` doesn't
    match the running bpp is skipped with a warning, and setup() never
    runs. The entry is still marked processed so the loader doesn't
    retry on the next call."""

    def test_mismatched_version_skips_setup(self, monkeypatch, caplog, plugins_enabled):
        from bpp import plugins as plugins_mod

        called = {"count": 0}

        def plugin_main():
            called["count"] += 1

        plugin_main.__plugin_name__ = "future-plugin"
        plugin_main.__plugin_version__ = "9.9.9"
        plugin_main.__bpp_version_required__ = ">=99.0"  # impossible
        # Make `target.__module__` point at something that won't
        # accidentally have its own metadata.
        plugin_main.__module__ = "module_that_does_not_exist_xyz"

        monkeypatch.setattr(plugins_mod, "_BPP_VERSION", "0.5.0")
        _patch_entry_points(
            monkeypatch,
            [_FakeEP("demo", "demo_pkg:setup", plugin_main)],
        )

        with caplog.at_level("WARNING", logger="bpp.plugins"):
            loaded = plugins_mod.load_plugin_entry_points()

        assert loaded == 1, (
            "loader counts the skipped plugin (one-shot guard prevents retry on the next call)"
        )
        assert called["count"] == 0, (
            "setup() must NOT run when bpp version is outside the plugin's declared compat range"
        )
        msgs = " ".join(rec.message for rec in caplog.records)
        assert "doesn't satisfy it" in msgs
        assert ">=99.0" in msgs

    def test_matched_version_runs_setup(self, monkeypatch, plugins_enabled):
        from bpp import plugins as plugins_mod

        called = {"count": 0}

        def plugin_main():
            called["count"] += 1

        plugin_main.__plugin_name__ = "current-plugin"
        plugin_main.__bpp_version_required__ = ">=0.1,<1.0"
        plugin_main.__module__ = "module_that_does_not_exist_xyz"

        monkeypatch.setattr(plugins_mod, "_BPP_VERSION", "0.5.0")
        _patch_entry_points(
            monkeypatch,
            [_FakeEP("demo", "demo_pkg:setup", plugin_main)],
        )

        loaded = plugins_mod.load_plugin_entry_points()
        assert loaded == 1
        assert called["count"] == 1

    def test_friendly_label_appears_in_log(self, monkeypatch, caplog, plugins_enabled):
        """`__plugin_name__` + `__plugin_version__` should show up in
        the success log line, not the raw entry-point id."""
        from bpp import plugins as plugins_mod

        def plugin_main():
            pass

        plugin_main.__plugin_name__ = "labelled-plugin"
        plugin_main.__plugin_version__ = "1.2.3"
        plugin_main.__module__ = "module_that_does_not_exist_xyz"

        _patch_entry_points(
            monkeypatch,
            [_FakeEP("demo", "demo_pkg:setup", plugin_main)],
        )

        with caplog.at_level("INFO", logger="bpp.plugins"):
            plugins_mod.load_plugin_entry_points()

        msgs = " ".join(rec.message for rec in caplog.records)
        assert "labelled-plugin" in msgs
        assert "v1.2.3" in msgs


class TestLoaderWiredAtStartup:
    """Source-scan to confirm the loader is invoked from the two
    documented startup paths (web app + CLI)."""

    def test_create_app_calls_loader(self):
        from pathlib import Path

        src = Path("bpp/web/app.py").read_text()
        assert "load_plugin_entry_points" in src, (
            "create_app must call load_plugin_entry_points so plugin "
            "registrations land before WebAppState is built"
        )

    def test_cli_pick_calls_loader(self):
        # bpp/commands.py became bpp/commands/ package during the v0.1 split.
        from pathlib import Path

        src = Path("bpp/commands/pick.py").read_text()
        assert "load_plugins_for_cli" in src  # wraps load_plugin_entry_points

    def test_cli_analyze_calls_loader(self):
        from pathlib import Path

        # Loader must fire in both analyze and pick CLI entry points.
        analyze_src = Path("bpp/commands/analyze.py").read_text()
        pick_src = Path("bpp/commands/pick.py").read_text()
        assert "load_plugins_for_cli" in analyze_src
        assert "load_plugins_for_cli" in pick_src

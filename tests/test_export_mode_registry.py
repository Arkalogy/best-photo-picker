"""Public ExportModeRegistry / register_export_mode() — plugin contract tests."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from bpp.output.export import export_selected
from bpp.output.export_modes import (
    _EXPORT_MODES,
    ExportModeRegistry,
    register_export_mode,
)


@pytest.fixture(autouse=True)
def _reset_plugin_modes():
    """Drop plugin-registered modes before AND after each test."""
    ExportModeRegistry._reset_for_tests()
    yield
    ExportModeRegistry._reset_for_tests()


def _noop_handler(src: str, dest: str) -> None:
    pass


class TestExportModeRegistry:
    def test_builtins_present(self):
        names = set(ExportModeRegistry.names())
        assert {"copy", "hardlink", "symlink", "zip"} <= names

    def test_builtins_marked_is_builtin(self):
        for name in ("copy", "hardlink", "symlink", "zip"):
            mode = ExportModeRegistry.get(name)
            assert mode is not None and mode.is_builtin

    def test_legacy_export_modes_dict_mirrors_registry(self):
        for name in ("copy", "hardlink", "symlink"):
            assert name in _EXPORT_MODES


class TestRegisterExportMode:
    def test_happy_path(self):
        register_export_mode("myplugin_demo", _noop_handler, description="Demo")
        m = ExportModeRegistry.get("myplugin_demo")
        assert m is not None
        assert m.handler is _noop_handler
        assert m.description == "Demo"
        assert not m.is_builtin

    def test_idempotent_reregister_same_handler(self):
        register_export_mode("myplugin_demo", _noop_handler)
        register_export_mode("myplugin_demo", _noop_handler)
        # No exception, single entry
        assert ExportModeRegistry.get("myplugin_demo").handler is _noop_handler

    def test_reserved_builtin_name_rejected(self):
        for name in ("copy", "hardlink", "symlink"):
            with pytest.raises(ValueError, match="reserved"):
                register_export_mode(name, _noop_handler)

    def test_reserved_builtin_can_be_overridden_with_replace(self):
        # Pin the override path — used by tests/replace=True only.
        original = ExportModeRegistry.get("copy")
        try:
            register_export_mode("copy", _noop_handler, replace=True)
            assert ExportModeRegistry.get("copy").handler is _noop_handler
        finally:
            # Restore so other tests that depend on the real copy
            # handler don't break.
            ExportModeRegistry.register(original, replace=True)

    def test_collision_without_replace_rejected(self):
        register_export_mode("myplugin_demo", _noop_handler)

        def other(src: str, dest: str) -> None:
            pass

        with pytest.raises(ValueError, match="already registered"):
            register_export_mode("myplugin_demo", other)

    def test_replace_overrides_plugin_mode(self):
        register_export_mode("myplugin_demo", _noop_handler)

        def other(src: str, dest: str) -> None:
            pass

        register_export_mode("myplugin_demo", other, replace=True)
        assert ExportModeRegistry.get("myplugin_demo").handler is other

    def test_reset_for_tests_keeps_builtins(self):
        register_export_mode("myplugin_demo", _noop_handler)
        ExportModeRegistry._reset_for_tests()
        # Plugin mode gone
        assert ExportModeRegistry.get("myplugin_demo") is None
        # Built-ins remain
        assert set(ExportModeRegistry.names()) == {"copy", "hardlink", "symlink", "zip"}


class TestPluginModeFlowsThroughExport:
    def test_plugin_mode_dispatches_in_export_loop(self, tmp_path: Path):
        """End-to-end: a plugin-registered mode actually runs when the
        export pipeline is invoked with mode='myplugin_sidecar'."""
        called_with: list[tuple[str, str]] = []

        def sidecar_handler(src: str, dest: str) -> None:
            called_with.append((src, dest))
            # Do a real copy so the manifest path resolution works.
            import shutil

            shutil.copy2(src, dest)
            with open(dest + ".sidecar", "w") as f:
                json.dump({"src": src}, f)

        register_export_mode("myplugin_sidecar", sidecar_handler)

        # Build a tiny synthetic photo on disk
        src = tmp_path / "input.jpg"
        # Minimal JPEG: just write some bytes; export doesn't decode for
        # non-copy modes.
        src.write_bytes(b"\xff\xd8\xff\xd9")  # JPEG SOI + EOI

        outdir = str(tmp_path / "out")
        item = {"filepath": str(src), "aggregate_score": 0.9}

        exported, failed = export_selected(
            [item],
            [item],
            outdir,
            mode="myplugin_sidecar",
            fmt="original",
        )
        assert exported == 1, failed
        # Handler was invoked once with (src, dest)
        assert len(called_with) == 1
        invoked_src, invoked_dest = called_with[0]
        assert invoked_src == str(src)
        # The sidecar exists next to the destination
        assert os.path.isfile(invoked_dest + ".sidecar")

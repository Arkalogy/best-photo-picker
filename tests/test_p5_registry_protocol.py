"""P5 — every plugin-target registry conforms to the shared protocol.

Gate test for the unification deliverable: WorkerRegistry,
SmartAlbumRegistry, ExportModeRegistry, DedupeStrategyRegistry all
expose ``register / get / keys / _reset_for_tests``. A future PR that
adds a fifth registry to the plugin surface adds it to
``PLUGIN_TARGET_REGISTRIES`` and inherits the conformance check for
free.
"""

from __future__ import annotations

import pytest

from bpp.plugins.registry_protocol import (
    PluginRegistryLike,
    each_registry,
    plugin_target_registries,
)


class TestRegistryProtocolConformance:
    def test_every_registry_implements_register(self):
        for name, reg in each_registry():
            assert callable(getattr(reg, "register", None)), (
                f"{name} must expose a register classmethod"
            )

    def test_every_registry_implements_get(self):
        for name, reg in each_registry():
            assert callable(getattr(reg, "get", None)), f"{name} must expose a get classmethod"

    def test_every_registry_implements_keys(self):
        for name, reg in each_registry():
            assert callable(getattr(reg, "keys", None)) or callable(getattr(reg, "all", None)), (
                f"{name} must expose keys() or all() for enumeration"
            )

    def test_every_registry_implements_reset_for_tests(self):
        for name, reg in each_registry():
            assert callable(getattr(reg, "_reset_for_tests", None)), (
                f"{name} must expose _reset_for_tests for test isolation"
            )

    @pytest.mark.parametrize(
        "name,reg",
        plugin_target_registries(),
        ids=[name for name, _ in plugin_target_registries()],
    )
    def test_runtime_isinstance_check_against_protocol(self, name, reg):
        """isinstance(cls, PluginRegistryLike) passes — confirms the
        runtime_checkable Protocol matches every registry's classmethod
        surface. A future regression that drops one of the four named
        methods fails here loudly."""
        assert isinstance(reg, PluginRegistryLike), (
            f"{name} no longer matches the PluginRegistryLike protocol — "
            f"a register/get/keys/_reset_for_tests method went missing"
        )

    def test_canonical_list_is_non_empty(self):
        assert len(plugin_target_registries()) >= 4, (
            "the plugin-target list collapsed below the four registries P5 unified"
        )

    def test_registry_names_are_unique(self):
        names = [n for n, _ in plugin_target_registries()]
        assert len(names) == len(set(names))

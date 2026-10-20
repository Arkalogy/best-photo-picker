"""A plugin can give its model the picker's Download → Use → Uninstall
lifecycle. The catalog loaders (on-disk check, ensure, remove) were a
literal dict of three built-ins inside a function."""

from __future__ import annotations

import pytest


@pytest.fixture
def plugin_loader():
    from bpp.web import catalog_loaders

    calls: list[str] = []
    catalog_loaders.register_catalog_loader(
        "myplugin_big_model",
        is_on_disk=lambda: False,
        ensure_weights=lambda: calls.append("ensure") or "/tmp/x",
        remove_weights=lambda: calls.append("remove") or 1,
    )
    yield calls
    catalog_loaders._reset_for_tests()


def test_a_registered_loader_joins_the_builtins(plugin_loader):
    from bpp.web.catalog_loaders import catalog_loaders

    loaders = catalog_loaders()
    assert {"insightface_buffalo_s", "nudenet_320n", "lama_inpaint_research"} <= set(loaders)
    assert "myplugin_big_model" in loaders
    assert loaders["myplugin_big_model"][0]() is False


def test_a_builtin_id_cannot_be_taken_over():
    from bpp.web.catalog_loaders import register_catalog_loader

    with pytest.raises(ValueError):
        register_catalog_loader(
            "nudenet_320n",
            is_on_disk=lambda: True,
            ensure_weights=lambda: "",
            remove_weights=lambda: 0,
        )

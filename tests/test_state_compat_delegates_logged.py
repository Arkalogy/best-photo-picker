"""Every legacy ``WebAppState`` delegate announces itself once.

``docs/architecture-notes.md`` says the property delegates in
``state_compat.py`` log a one-shot WARNING on first access. Only
``_workers`` did; the cache and analysis-store delegates were silent, so
nothing pushed their call sites to migrate, and the memory debug
endpoint still read the face-cluster map through one of them.
"""

from __future__ import annotations

import logging

import pytest

LEGACY = [
    "_workers",
    "clip_cache",
    "_face_cluster_map",
    "_face_cluster_map_lock",
    "_edited_ids",
    "_auto_enhanced_ids",
    "phash_ready",
    "_phash_generation",
    "_compute_thread",
    "_warm_thread",
    "_cancel_warm",
]


@pytest.fixture
def app(tmp_path):
    from bpp.web.app import create_app

    app = create_app(library_path=str(tmp_path / "lib"))
    app.config["TESTING"] = True
    return app


@pytest.mark.parametrize("name", LEGACY)
def test_each_delegate_warns_once_on_first_access(app, caplog, name):
    from bpp.web._deprecated_attr import _reset_logged_for_tests

    _reset_logged_for_tests()
    ctx = app.extensions["bpp"]
    caplog.set_level(logging.WARNING, logger="bpp")
    getattr(ctx, name)
    getattr(ctx, name)
    hits = [r for r in caplog.records if "deprecated" in r.getMessage() and name in r.getMessage()]
    assert len(hits) == 1, caplog.text


def test_a_legacy_write_warns_too(app, caplog):
    from bpp.web._deprecated_attr import _reset_logged_for_tests

    _reset_logged_for_tests()
    ctx = app.extensions["bpp"]
    caplog.set_level(logging.WARNING, logger="bpp")
    ctx._phash_generation = ctx.analysis_store.phash_generation
    assert [r for r in caplog.records if "_phash_generation" in r.getMessage()], caplog.text


def test_the_memory_debug_endpoint_uses_no_legacy_path(app, caplog):
    from bpp.web._deprecated_attr import _reset_logged_for_tests

    _reset_logged_for_tests()
    caplog.set_level(logging.WARNING, logger="bpp")
    with app.test_client() as c:
        assert c.get("/api/v1/debug/memory").status_code == 200
    assert not [r for r in caplog.records if "deprecated" in r.getMessage()], caplog.text

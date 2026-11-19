"""Settings stored in a library don't configure this computer.

The settings table travels with the library folder. Values there may tune
how a library is scored, inside the limits the app declares; they may not
choose the server's TLS setup, its proxy trust, how many worker processes
start, or buffer sizes past the declared limits.
"""

from __future__ import annotations

import os

import pytest

from bpp.config import DEFAULTS


@pytest.fixture
def ctx(tmp_path):
    from bpp.web.app import create_app

    app = create_app(workdir=str(tmp_path), library_path=str(tmp_path))
    with app.app_context():
        yield app.extensions["bpp"]


def _store(ctx, **rows):
    conn = ctx.get_conn()
    for key, value in rows.items():
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
    conn.commit()


def test_a_stored_ssl_context_does_not_reach_the_server(ctx):
    from bpp.commands.serve import _ssl_context

    _store(ctx, ssl_context="x")

    assert ctx.config.get("ssl_context") is None
    assert _ssl_context(ctx.config) is None
    assert "ssl_context" not in dict(ctx.config)


def test_a_stored_proxy_switch_is_ignored(ctx):
    _store(ctx, behind_proxy="true")

    assert not ctx.config.get("behind_proxy")


@pytest.mark.parametrize("value", ["x", ["only-one"], 5, {"a": 1}])
def test_an_unusable_ssl_setting_means_plain_http_not_a_crash(value):
    from bpp.commands.serve import _ssl_context

    assert _ssl_context({"ssl_context": value}) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), ("adhoc", "adhoc"), (["c.pem", "k.pem"], ("c.pem", "k.pem"))],
)
def test_the_documented_ssl_settings_still_work(value, expected):
    from bpp.commands.serve import _ssl_context

    assert _ssl_context({"ssl_context": value}) == expected


def test_stored_worker_settings_do_not_choose_how_many_processes_start(ctx):
    from bpp.web.analyze_face_extract import _config_uses_process_pool
    from bpp.web.face_extraction_phase5 import _pool_for

    _store(ctx, _face_extract_workers="100000", _face_extract_pool="process")
    run_config = dict(ctx.config)

    assert _pool_for(run_config)[1] == 1
    assert _config_uses_process_pool(run_config) is False


@pytest.mark.parametrize(
    ("asked", "expected"), [("0", 1), ("-3", 1), ("abc", 1), (None, 1), (2, 2)]
)
def test_the_worker_count_from_the_owners_config_is_floored(asked, expected):
    from bpp.web.face_extraction_phase5 import _pool_for

    if expected > (os.cpu_count() or 1):
        pytest.skip("single-core machine")
    assert _pool_for({"_face_extract_workers": asked})[1] == expected


def test_the_worker_count_from_the_owners_config_is_capped():
    from bpp.web.face_extraction_phase5 import MAX_FACE_WORKERS, _pool_for

    n = _pool_for({"_face_extract_workers": 100000, "_face_extract_pool": "process"})[1]
    assert 1 <= n <= min(MAX_FACE_WORKERS, os.cpu_count() or 1)


def test_a_stored_value_outside_the_declared_limits_is_not_used(ctx):
    _store(ctx, pet_input_size="100000")
    assert ctx.config.get("pet_input_size") == DEFAULTS["pet_input_size"]

    _store(ctx, pet_input_size="not a number")
    assert ctx.config.get("pet_input_size") == DEFAULTS["pet_input_size"]


def test_a_stored_value_inside_the_declared_limits_is_used(ctx):
    _store(ctx, pet_input_size="1280")
    assert ctx.config.get("pet_input_size") == 1280


def test_the_analysis_config_takes_stored_tuning_only_inside_the_limits(ctx):
    from bpp.web.bp_analysis import run_config_for_analyze

    _store(ctx, max_long_side="99999999", face_detection_confidence="7")
    merged = run_config_for_analyze(ctx, ctx.get_conn())
    assert merged["max_long_side"] == DEFAULTS["max_long_side"]
    assert merged["face_detection_confidence"] == DEFAULTS["face_detection_confidence"]

    _store(ctx, face_detection_confidence="0.6")
    merged = run_config_for_analyze(ctx, ctx.get_conn())
    assert merged["face_detection_confidence"] == pytest.approx(0.6)


@pytest.mark.parametrize("stored", ["nan", "inf", "-inf"])
def test_a_stored_value_that_is_not_a_finite_number_is_not_used(ctx, stored):
    _store(ctx, face_detection_confidence=stored)

    assert ctx.config.get("face_detection_confidence") == DEFAULTS["face_detection_confidence"]


def test_settings_read_straight_from_the_database_get_the_same_check(ctx):
    from bpp.constants import SENSITIVE_NUDITY_THRESHOLD
    from bpp.db.groups import group_min_photos
    from bpp.db.settings import resolve_sensitive_threshold

    conn = ctx.get_conn()
    _store(ctx, sensitive_nudity_threshold="1e9", group_min_photos="100000")
    assert resolve_sensitive_threshold(conn) == SENSITIVE_NUDITY_THRESHOLD
    assert group_min_photos(conn) == DEFAULTS["group_min_photos"]

    _store(ctx, sensitive_nudity_threshold="0.4", group_min_photos="5")
    assert resolve_sensitive_threshold(conn) == pytest.approx(0.4)
    assert group_min_photos(conn) == 5


def test_an_ignored_machine_setting_is_said_in_the_log(ctx, caplog):
    import logging

    from bpp import config_resolver

    config_resolver._warned_rejections.clear()
    _store(ctx, _face_extract_workers="4")
    with caplog.at_level(logging.WARNING):
        dict(ctx.config)

    assert any("_face_extract_workers" in r.getMessage() for r in caplog.records)

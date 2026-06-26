"""Tests for the layered Config resolver.

Pins:
- Precedence: DB > YAML > DEFAULTS > caller default.
- Type coercion: DB strings come back as the type DEFAULTS implies.
- Dict-compat surface: get/[]/in/iter/as_dict all work like a dict.
- set() persists to DB so subsequent reads see the new value.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def conn(tmp_path):
    from bpp.db.connection import get_db, init_db

    db_path = str(tmp_path / "c.db")
    init_db(db_path)
    return get_db(db_path)


@pytest.fixture
def make_conn(conn):
    """Wrap a connection as a getter (matching WebAppState.get_conn)."""
    return lambda: conn


# ── Precedence ──────────────────────────────────────────────────────


class TestPrecedence:
    def test_defaults_only_when_no_overlay(self, make_conn):
        from bpp.config_resolver import Config

        cfg = Config({}, get_conn=make_conn)
        # face_cluster_threshold is in DEFAULTS at 0.80
        assert cfg.get("face_cluster_threshold") == 0.80

    def test_yaml_overrides_defaults(self, make_conn):
        from bpp.config_resolver import Config

        cfg = Config({"face_cluster_threshold": 0.5}, get_conn=make_conn)
        assert cfg.get("face_cluster_threshold") == 0.5

    def test_db_overrides_yaml(self, conn, make_conn):
        from bpp.config_resolver import Config
        from bpp.db.settings import set_setting

        cfg = Config({"face_cluster_threshold": 0.5}, get_conn=make_conn)
        set_setting(conn, "face_cluster_threshold", "0.9")
        assert cfg.get("face_cluster_threshold") == 0.9

    def test_db_overrides_defaults_when_no_yaml(self, conn, make_conn):
        from bpp.config_resolver import Config
        from bpp.db.settings import set_setting

        cfg = Config({}, get_conn=make_conn)
        set_setting(conn, "face_cluster_threshold", "0.42")
        assert cfg.get("face_cluster_threshold") == 0.42

    def test_caller_default_when_unknown_everywhere(self, make_conn):
        from bpp.config_resolver import Config

        cfg = Config({}, get_conn=make_conn)
        assert cfg.get("totally_unknown_key", "fallback") == "fallback"
        assert cfg.get("totally_unknown_key") is None


# ── Type coercion ───────────────────────────────────────────────────


class TestTypeCoercion:
    def test_db_string_coerced_to_float(self, conn, make_conn):
        from bpp.config_resolver import Config
        from bpp.db.settings import set_setting

        cfg = Config({}, get_conn=make_conn)
        set_setting(conn, "face_cluster_threshold", "0.9")
        result = cfg.get("face_cluster_threshold")
        assert result == 0.9
        assert isinstance(result, float)

    def test_db_string_coerced_to_int(self, conn, make_conn):
        from bpp.config_resolver import Config
        from bpp.db.settings import set_setting

        cfg = Config({}, get_conn=make_conn)
        set_setting(conn, "max_long_side", "2048")
        result = cfg.get("max_long_side")
        assert result == 2048
        assert isinstance(result, int)

    def test_db_string_coerced_to_bool(self, conn, make_conn):
        from bpp.config_resolver import Config
        from bpp.db.settings import set_setting

        cfg = Config({}, get_conn=make_conn)
        # follow_symlinks is bool in DEFAULTS
        set_setting(conn, "follow_symlinks", "true")
        assert cfg.get("follow_symlinks") is True
        set_setting(conn, "follow_symlinks", "false")
        assert cfg.get("follow_symlinks") is False

    def test_db_invalid_int_falls_back_to_zero(self, conn, make_conn):
        from bpp.config_resolver import Config
        from bpp.db.settings import set_setting

        cfg = Config({}, get_conn=make_conn)
        set_setting(conn, "max_long_side", "not-an-int")
        assert cfg.get("max_long_side") == 0


# ── Dict-compat surface ─────────────────────────────────────────────


class TestDictCompat:
    def test_getitem_returns_value(self, make_conn):
        from bpp.config_resolver import Config

        cfg = Config({}, get_conn=make_conn)
        assert cfg["face_cluster_threshold"] == 0.80

    def test_getitem_raises_keyerror_on_unknown(self, make_conn):
        from bpp.config_resolver import Config

        cfg = Config({}, get_conn=make_conn)
        with pytest.raises(KeyError):
            cfg["totally_unknown_key"]

    def test_in_operator(self, make_conn):
        from bpp.config_resolver import Config

        cfg = Config({"my_yaml_key": 1}, get_conn=make_conn)
        assert "face_cluster_threshold" in cfg  # in DEFAULTS
        assert "my_yaml_key" in cfg  # in YAML
        assert "totally_unknown_key" not in cfg

    def test_iter_yields_union_of_keys(self, conn, make_conn):
        from bpp.config_resolver import Config
        from bpp.db.settings import set_setting

        set_setting(conn, "my_db_key", "x")
        cfg = Config({"my_yaml_key": 1}, get_conn=make_conn)
        keys = set(iter(cfg))
        assert "my_db_key" in keys
        assert "my_yaml_key" in keys
        assert "face_cluster_threshold" in keys  # DEFAULTS

    def test_as_dict_returns_resolved_view(self, conn, make_conn):
        from bpp.config_resolver import Config
        from bpp.db.settings import set_setting

        cfg = Config({}, get_conn=make_conn)
        set_setting(conn, "face_cluster_threshold", "0.42")
        d = cfg.as_dict()
        assert d["face_cluster_threshold"] == 0.42
        # Type-coerced
        assert isinstance(d["face_cluster_threshold"], float)


# ── Mutation ────────────────────────────────────────────────────────


class TestSet:
    def test_set_persists_to_db(self, conn, make_conn):
        from bpp.config_resolver import Config
        from bpp.db.settings import get_setting

        cfg = Config({}, get_conn=make_conn)
        cfg.set("face_cluster_threshold", 0.42)
        # Stored as string in DB
        assert get_setting(conn, "face_cluster_threshold") == "0.42"
        # Read back through Config returns float
        assert cfg.get("face_cluster_threshold") == 0.42

    def test_set_without_db_raises(self):
        from bpp.config_resolver import Config

        cfg = Config({}, get_conn=None)
        with pytest.raises(RuntimeError, match="without a DB connection"):
            cfg.set("k", 1)


# ── No-DB usage (CLI commands) ──────────────────────────────────────


class TestNoDB:
    def test_get_falls_through_to_yaml_then_defaults(self):
        from bpp.config_resolver import Config

        cfg = Config({"max_long_side": 2048}, get_conn=None)
        assert cfg.get("max_long_side") == 2048
        # Falls through to DEFAULTS for unset keys
        assert cfg.get("face_cluster_threshold") == 0.80

    def test_no_db_no_crash(self):
        """Calling .get on a Config without a connection must not raise
        — just falls through to YAML/DEFAULTS."""
        from bpp.config_resolver import Config

        cfg = Config({}, get_conn=None)
        cfg.get("face_cluster_threshold")  # no exception

"""Public DedupeStrategyRegistry / register_dedupe_strategy() — plugin tests."""

from __future__ import annotations

import pytest

from bpp.dedupe.strategy import (
    DedupeStrategy,
    DedupeStrategyRegistry,
    register_dedupe_strategy,
    resolve_strategy,
)


@pytest.fixture(autouse=True)
def _reset_plugin_strategies():
    """Drop plugin-registered strategies before AND after each test."""
    DedupeStrategyRegistry._reset_for_tests()
    yield
    DedupeStrategyRegistry._reset_for_tests()


def _noop_fn(items, _config, **_kw):
    out = []
    for item in items:
        item["cluster_size"] = 1
        out.append(item)
    return out


class TestDedupeStrategyRegistry:
    def test_builtins_present(self):
        names = set(DedupeStrategyRegistry.names())
        assert {"hash", "clip", "none"} <= names

    def test_builtins_marked_is_builtin(self):
        for name in ("hash", "clip", "none"):
            s = DedupeStrategyRegistry.get(name)
            assert s is not None and s.is_builtin

    def test_clip_strategy_requires_embeddings_flag(self):
        clip = DedupeStrategyRegistry.get("clip")
        assert clip is not None and clip.requires_clip_embeddings


class TestRegisterDedupeStrategy:
    def test_happy_path(self):
        register_dedupe_strategy(
            DedupeStrategy(
                name="myplugin_demo",
                dedupe_fn=_noop_fn,
                description="Demo",
            )
        )
        s = DedupeStrategyRegistry.get("myplugin_demo")
        assert s is not None and s.dedupe_fn is _noop_fn
        assert not s.is_builtin

    def test_idempotent_same_def(self):
        sd = DedupeStrategy(name="myplugin_demo", dedupe_fn=_noop_fn)
        register_dedupe_strategy(sd)
        register_dedupe_strategy(sd)
        assert DedupeStrategyRegistry.get("myplugin_demo") is not None

    def test_reserved_builtin_name_rejected(self):
        for name in ("hash", "clip", "none"):
            with pytest.raises(ValueError, match="reserved"):
                register_dedupe_strategy(DedupeStrategy(name=name, dedupe_fn=_noop_fn))

    def test_collision_without_replace_rejected(self):
        register_dedupe_strategy(DedupeStrategy(name="myplugin_demo", dedupe_fn=_noop_fn))

        def other(items, _c, **_kw):
            return items

        with pytest.raises(ValueError, match="already registered"):
            register_dedupe_strategy(DedupeStrategy(name="myplugin_demo", dedupe_fn=other))

    def test_replace_overrides(self):
        register_dedupe_strategy(DedupeStrategy(name="myplugin_demo", dedupe_fn=_noop_fn))

        def other(items, _c, **_kw):
            return items

        register_dedupe_strategy(
            DedupeStrategy(name="myplugin_demo", dedupe_fn=other),
            replace=True,
        )
        assert DedupeStrategyRegistry.get("myplugin_demo").dedupe_fn is other

    def test_reset_preserves_builtins(self):
        register_dedupe_strategy(DedupeStrategy(name="myplugin_demo", dedupe_fn=_noop_fn))
        DedupeStrategyRegistry._reset_for_tests()
        assert DedupeStrategyRegistry.get("myplugin_demo") is None
        assert set(DedupeStrategyRegistry.names()) == {"hash", "clip", "none"}


class TestResolveStrategy:
    def test_none_name_returns_none(self):
        assert resolve_strategy(None, have_clip_embeddings=False) is None
        assert resolve_strategy("", have_clip_embeddings=True) is None

    def test_unknown_name_falls_back(self, caplog):
        result = resolve_strategy("does_not_exist", have_clip_embeddings=True)
        assert result is None
        assert any("not registered" in r.message for r in caplog.records)

    def test_clip_without_embeddings_falls_back(self, caplog):
        result = resolve_strategy("clip", have_clip_embeddings=False)
        assert result is None
        assert any("requires CLIP embeddings" in r.message for r in caplog.records)

    def test_clip_with_embeddings_resolved(self):
        s = resolve_strategy("clip", have_clip_embeddings=True)
        assert s is not None and s.name == "clip"

    def test_plugin_strategy_resolved(self):
        register_dedupe_strategy(DedupeStrategy(name="myplugin_demo", dedupe_fn=_noop_fn))
        s = resolve_strategy("myplugin_demo", have_clip_embeddings=False)
        assert s is not None and s.name == "myplugin_demo"


class TestBuiltinStrategiesRoundTrip:
    """End-to-end: invoke each built-in strategy through the registry."""

    def _photos(self):
        return [
            {
                "filepath": "/tmp/a.jpg",
                "phash": 1234,
                "ahash": 5678,
                "aggregate_score": 0.5,
                "date": "2026-01-01T00:00:00",
                "date_month": "2026-01",
            },
            {
                "filepath": "/tmp/b.jpg",
                "phash": 1234,  # same hash → near-duplicate
                "ahash": 5678,
                "aggregate_score": 0.7,
                "date": "2026-01-01T00:00:10",
                "date_month": "2026-01",
            },
            {
                "filepath": "/tmp/c.jpg",
                "phash": 9999,
                "ahash": 8888,
                "aggregate_score": 0.4,
                "date": "2026-01-02T00:00:00",
                "date_month": "2026-01",
            },
        ]

    def test_hash_strategy_dedupes_near_duplicates(self):
        s = DedupeStrategyRegistry.get("hash")
        out = s.dedupe_fn(self._photos(), {})
        # a and b have identical hashes — at minimum they collapse
        # to a single representative. Exact output count depends on
        # the global second-pass which may fold further; assert only
        # that dedup happened (output < input) and the highest-scoring
        # representative survived.
        assert 1 <= len(out) < 3
        kept_paths = {it["filepath"] for it in out}
        # Highest-scoring photo (b, score=0.7) must be a representative
        # — both passes prefer high-score reps.
        assert "/tmp/b.jpg" in kept_paths

    def test_none_strategy_keeps_all(self):
        s = DedupeStrategyRegistry.get("none")
        photos = self._photos()
        out = s.dedupe_fn(photos, {})
        assert len(out) == 3
        assert all(it.get("cluster_size") == 1 for it in out)

    def test_clip_strategy_raises_without_embeddings(self):
        s = DedupeStrategyRegistry.get("clip")
        with pytest.raises(ValueError, match="requires clip_embeddings"):
            s.dedupe_fn(self._photos(), {})


class TestPluginStrategyOverridesRecompute:
    """Plugin strategy registered + chosen via config overrides bpp's
    auto-pick in the recompute pipeline."""

    def test_plugin_strategy_invoked(self, monkeypatch):
        called_with: list[tuple[int, dict]] = []

        def my_strategy(items, config, **kwargs):
            called_with.append((len(items), dict(kwargs)))
            for item in items:
                item["cluster_size"] = 1
            return items

        register_dedupe_strategy(
            DedupeStrategy(
                name="myplugin_test_override",
                dedupe_fn=my_strategy,
                description="Test override",
            )
        )

        # Resolve through the helper as recompute does
        s = resolve_strategy("myplugin_test_override", have_clip_embeddings=False)
        assert s is not None and s.name == "myplugin_test_override"

        items = [{"filepath": "/a", "aggregate_score": 0.5}]
        out = s.dedupe_fn(items, {"k": 50}, clip_embeddings=None)
        assert called_with == [(1, {"clip_embeddings": None})]
        assert out[0]["cluster_size"] == 1

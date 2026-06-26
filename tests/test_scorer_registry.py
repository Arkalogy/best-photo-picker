"""Ensure scorer registry is complete and consistent with DB schema."""

from __future__ import annotations

from bpp.scoring.registry import (
    _SCORER_REGISTRY,
    get_api_score_fields,
    get_score_db_columns,
    get_video_avg_keys,
    get_weight_defaults,
    get_weight_keys,
    get_weighted_scorers,
)


def test_all_weighted_scorers_have_defaults():
    """Every weighted scorer must have a positive default weight."""
    for s in get_weighted_scorers():
        assert s.weight_key is not None
        assert s.default_weight > 0, f"{s.key}: default_weight must be > 0"


def test_weight_defaults_match_registry():
    """get_weight_defaults() must return one entry per weighted scorer."""
    defaults = get_weight_defaults()
    weighted = get_weighted_scorers()
    assert len(defaults) == len(weighted)
    for s in weighted:
        assert s.weight_key in defaults
        assert defaults[s.weight_key] == s.default_weight


def test_db_columns_include_aggregate():
    """DB columns must end with aggregate_score."""
    cols = get_score_db_columns()
    assert "aggregate_score" in cols


def test_api_fields_include_aggregate():
    """API fields must include aggregate_score."""
    fields = get_api_score_fields()
    assert "aggregate_score" in fields


def test_video_avg_keys_subset_of_db_columns():
    """Every video averaging key must be a valid DB column."""
    db_cols = set(get_score_db_columns())
    for key in get_video_avg_keys():
        assert key in db_cols, f"video_avg_key '{key}' not in DB columns"


def test_api_fields_subset_of_db_columns():
    """Every API field must be a valid DB column."""
    db_cols = set(get_score_db_columns())
    for field_name in get_api_score_fields():
        assert field_name in db_cols, f"API field '{field_name}' not in DB columns"


def test_weight_keys_returns_tuple():
    """get_weight_keys() returns a tuple of strings."""
    keys = get_weight_keys()
    assert isinstance(keys, tuple)
    assert all(isinstance(k, str) for k in keys)


def test_expected_scorers_present():
    """All known scorers must be in the registry."""
    expected = {"blur", "exposure", "face", "composition", "skin", "nudity", "pets"}
    actual = set(_SCORER_REGISTRY.keys())
    missing = expected - actual
    assert not missing, f"Missing scorers: {missing}"


def test_no_duplicate_db_columns():
    """No two scorers should produce the same DB column."""
    seen: dict[str, str] = {}
    for s in _SCORER_REGISTRY.values():
        for col in s.db_columns:
            assert col not in seen, f"DB column '{col}' claimed by both '{seen[col]}' and '{s.key}'"
            seen[col] = s.key


# ---------------------------------------------------------------------------
# Plugin scorer registration (E3)
# ---------------------------------------------------------------------------


import pytest  # noqa: E402

from bpp.scoring.aggregate import compute_aggregate  # noqa: E402
from bpp.scoring.registry import (  # noqa: E402
    ScorerDef,
    _reset_plugin_scorers_for_tests,
    register_scorer,
    run_optional_scorers,
)


@pytest.fixture(autouse=False)
def reset_plugin_scorers():
    """Drop every plugin-registered scorer before AND after the test, so
    no test leaks state into the rest of the suite."""
    _reset_plugin_scorers_for_tests()
    yield
    _reset_plugin_scorers_for_tests()


def _example_score_fn(_img, _filepath, _config):
    return {"myplugin_demo_score": 0.7}


class TestRegisterScorer:
    def test_happy_path(self, reset_plugin_scorers):
        register_scorer(
            ScorerDef(
                key="myplugin_demo",
                optional=True,
                score_fn=_example_score_fn,
                api_fields={"myplugin_demo_score": 0},
            )
        )
        assert "myplugin_demo" in _SCORER_REGISTRY

    def test_idempotent_reregister_same_def(self, reset_plugin_scorers):
        sd = ScorerDef(
            key="myplugin_demo",
            optional=True,
            score_fn=_example_score_fn,
            api_fields={"myplugin_demo_score": 0},
        )
        register_scorer(sd)
        register_scorer(sd)
        assert "myplugin_demo" in _SCORER_REGISTRY

    def test_reserved_builtin_key_rejected(self, reset_plugin_scorers):
        for reserved in ("blur", "exposure", "face", "composition", "skin", "nudity", "pets"):
            with pytest.raises(ValueError, match="reserved"):
                register_scorer(ScorerDef(key=reserved, optional=True, score_fn=_example_score_fn))

    def test_non_optional_rejected(self, reset_plugin_scorers):
        with pytest.raises(ValueError, match="optional=True"):
            register_scorer(ScorerDef(key="myp_x", score_fn=_example_score_fn))

    def test_missing_score_fn_rejected(self, reset_plugin_scorers):
        with pytest.raises(ValueError, match="score_fn"):
            register_scorer(ScorerDef(key="myp_x", optional=True))

    def test_db_columns_rejected(self, reset_plugin_scorers):
        with pytest.raises(ValueError, match="db_columns"):
            register_scorer(
                ScorerDef(
                    key="myp_x",
                    optional=True,
                    score_fn=_example_score_fn,
                    db_columns=("foo_score",),
                )
            )

    def test_collision_without_replace_rejected(self, reset_plugin_scorers):
        register_scorer(ScorerDef(key="myp_x", optional=True, score_fn=_example_score_fn))
        # Different score_fn → distinct ScorerDef → collision
        with pytest.raises(ValueError, match="already registered"):
            register_scorer(
                ScorerDef(
                    key="myp_x",
                    optional=True,
                    score_fn=lambda i, f, c: {"a": 1},
                )
            )

    def test_replace_overrides(self, reset_plugin_scorers):
        register_scorer(ScorerDef(key="myp_x", optional=True, score_fn=_example_score_fn))
        new_fn = lambda i, f, c: {"a": 2}  # noqa: E731
        register_scorer(
            ScorerDef(key="myp_x", optional=True, score_fn=new_fn),
            replace=True,
        )
        assert _SCORER_REGISTRY["myp_x"].score_fn is new_fn


class TestPluginScorerFlowsThroughPipeline:
    def test_run_optional_scorers_invokes_plugin_scorer(self, reset_plugin_scorers):
        register_scorer(
            ScorerDef(
                key="myp_demo",
                optional=True,
                toggle_key="model_myp_demo",
                score_fn=_example_score_fn,
                api_fields={"myplugin_demo_score": 0},
            )
        )
        result = run_optional_scorers(
            img=None,
            filepath="/fake.jpg",
            model_toggles={
                "model_myp_demo": True,
                "model_nudity": False,
                "model_pets": False,
            },
            config={},
        )
        assert result.get("myplugin_demo_score") == 0.7

    def test_toggle_off_skips_plugin_scorer(self, reset_plugin_scorers):
        register_scorer(
            ScorerDef(
                key="myp_demo",
                optional=True,
                toggle_key="model_myp_demo",
                score_fn=_example_score_fn,
                api_fields={"myplugin_demo_score": 0},
            )
        )
        result = run_optional_scorers(
            img=None,
            filepath="/fake.jpg",
            model_toggles={
                "model_myp_demo": False,
                "model_nudity": False,
                "model_pets": False,
            },
            config={},
        )
        assert "myplugin_demo_score" not in result

    def test_weighted_plugin_scorer_changes_aggregate(self, reset_plugin_scorers):
        register_scorer(
            ScorerDef(
                key="myp_demo",
                weight_key="myp_demo_weight",
                default_weight=0.05,
                aggregate_default=0.5,
                optional=True,
                toggle_key="model_myp_demo",
                score_fn=_example_score_fn,
                api_fields={"myp_demo_score": 0},
            )
        )
        photo = {
            "blur_score": 0.7,
            "exposure_score": 0.5,
            "face_score": 0.6,
            "composition_score": 0.5,
            "myp_demo_score": 0.8,
        }
        compute_aggregate([photo], {})
        # Without plugin: (0.30*.7 + 0.20*.5 + 0.35*.6 + 0.15*.5) / 1.0 = 0.595
        # With plugin: (0.595 + 0.05*0.8) / 1.05 = 0.635 / 1.05 = 0.604762
        assert abs(photo["aggregate_score"] - 0.604762) < 1e-5

    def test_plugin_scorer_with_zero_weight_no_aggregate_effect(self, reset_plugin_scorers):
        """default_weight=0 → plugin scorer runs but doesn't shift aggregate."""
        register_scorer(
            ScorerDef(
                key="myp_demo",
                weight_key="myp_demo_weight",
                default_weight=0.0,
                aggregate_default=0.5,
                optional=True,
                toggle_key="model_myp_demo",
                score_fn=_example_score_fn,
                api_fields={"myp_demo_score": 0},
            )
        )
        photo_with = {
            "blur_score": 0.7,
            "exposure_score": 0.5,
            "face_score": 0.6,
            "composition_score": 0.5,
            "myp_demo_score": 0.99,  # extreme value
        }
        compute_aggregate([photo_with], {})
        # No effect — weight is 0
        assert abs(photo_with["aggregate_score"] - 0.595) < 1e-5

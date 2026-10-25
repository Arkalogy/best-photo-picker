"""A plugin model switched off reaches the scorer dispatch as off, from the
Analyze endpoint's parsed value and from the raw stored setting alike."""

import numpy as np
import pytest

from bpp.config import DEFAULTS
from bpp.constants import RUN_TOGGLE_KEYS
from bpp.scoring.aggregate import process_one_settings
from bpp.scoring.registry import (
    ScorerDef,
    _reset_plugin_scorers_for_tests,
    register_scorer,
    run_optional_scorers,
)
from bpp.scoring.toggles import toggles_from_config


@pytest.mark.parametrize("stored_off", [False, "false"])
def test_scoring_handoff_honours_plugin_off(stored_off):
    key = "model_toggle_probe"
    calls = []
    register_scorer(
        ScorerDef(
            key="toggle_probe",
            optional=True,
            toggle_key=key,
            score_fn=lambda image, filepath, config: (
                calls.append(filepath) or {"toggle_probe_score": 1.0}
            ),
            api_fields={"toggle_probe_score": 0},
        )
    )
    try:
        # False is the Analyze endpoint's parsed value; "false" is the
        # live Config/import path's raw plugin setting. Neither may run.
        config = {**DEFAULTS, **{k: False for k in RUN_TOGGLE_KEYS}, key: stored_off}
        assert toggles_from_config(config)[key] is False
        _, _, handed_to_process_one, thresholds = process_one_settings(config)
        run_optional_scorers(
            np.zeros((4, 4, 3), dtype=np.uint8),
            "/synthetic/p.jpg",
            handed_to_process_one,
            thresholds,
        )
        assert calls == [], "the plugin's Off switch was dropped, so the dispatcher defaulted it on"
    finally:
        _reset_plugin_scorers_for_tests()

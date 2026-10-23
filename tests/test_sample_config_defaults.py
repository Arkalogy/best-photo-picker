"""sample_config.yaml says "All values shown are defaults" — keep that true.

Every commented-out ``# key: value`` line must match ``bpp.config.DEFAULTS``
so the sample can't drift from the code (it once documented
``time_window_seconds: 15`` while the real default was 30).
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from bpp.config import DEFAULTS

SAMPLE = Path(__file__).resolve().parent.parent / "sample_config.yaml"
_LINE = re.compile(r"^#\s*([a-z_]+):\s*([^#\s]+)")


def _sample_values() -> dict[str, object]:
    out: dict[str, object] = {}
    for line in SAMPLE.read_text().splitlines():
        m = _LINE.match(line)
        if m:
            out[m.group(1)] = yaml.safe_load(m.group(2))
    return out


def test_sample_lists_some_keys():
    assert len(_sample_values()) >= 10


def test_every_sample_value_matches_code_default():
    mismatches = {
        key: (value, DEFAULTS.get(key, "<missing from DEFAULTS>"))
        for key, value in _sample_values().items()
        if DEFAULTS.get(key, object()) != value
    }
    assert not mismatches, f"sample_config.yaml vs DEFAULTS (sample, code): {mismatches}"

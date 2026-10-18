"""A config missing the dedup keys dedups exactly like the real defaults.

The cluster code used to carry its own fallbacks (10 / 8) — twice as
strict as the app's defaults (20 / 10) — so any caller handing it a
partial config (a plugin, the strategy hook, a test) silently dropped
good photos as "duplicates". One defaults table now.
"""

from __future__ import annotations

import copy

from bpp.config import DEFAULTS
from bpp.dedupe.cluster import deduplicate
from bpp.dedupe.common import within_time_window


def _photo(name: str, phash: int, date: str, score: float) -> dict:
    return {
        "filepath": f"/lib/{name}.jpg",
        "phash": phash,
        "ahash": None,  # dHash only: the distance is exactly the bit count
        "date": date,
        "aggregate_score": score,
    }


# 15 bits apart, 5 seconds apart: a burst pair under the real default (20),
# two photos under the old fallback (10).
BURST = [
    _photo("a", 0, "2024-06-01 12:00:00", 0.9),
    _photo("b", (1 << 15) - 1, "2024-06-01 12:00:05", 0.5),
]
# 9 bits apart, days apart: one photo under the real global default (10),
# two under the old fallback (8).
RETAKE = [
    _photo("c", 0, "2024-06-01 12:00:00", 0.9),
    _photo("d", (1 << 9) - 1, "2024-06-09 12:00:00", 0.5),
]


def test_fixture_dates_parse() -> None:
    assert within_time_window(BURST[0]["date"], BURST[1]["date"], 30)


def test_partial_config_uses_the_real_burst_default() -> None:
    assert len(deduplicate(copy.deepcopy(BURST), config={})) == 1


def test_partial_config_uses_the_real_global_default() -> None:
    assert len(deduplicate(copy.deepcopy(RETAKE), config={})) == 1


def test_partial_and_full_configs_agree() -> None:
    for photos in (BURST, RETAKE):
        partial = deduplicate(copy.deepcopy(photos), config={})
        full = deduplicate(copy.deepcopy(photos), config=dict(DEFAULTS))
        assert [p["filepath"] for p in partial] == [p["filepath"] for p in full]

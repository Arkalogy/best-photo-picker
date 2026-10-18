"""A recompute override is clamped to ITS key's range, not a blanket 0-10."""

from __future__ import annotations

import pytest

from bpp.web.state_helpers import clamp_override


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("hash_distance_threshold", 20, 20),  # the default itself used to become 10
        ("hash_distance_threshold", 100, 64),
        ("time_window_seconds", 30, 30),
        ("time_window_seconds", -5, 0),
        ("max_per_day", 12, 12),
        ("selection_similarity_threshold", 2, 1.0),
        ("face_selection_boost", 0.15, 0.15),
        ("blur_weight", 50, 10.0),  # scoring weights keep the weight range
        ("blur_weight", 0.5, 0.5),
    ],
)
def test_clamped_to_the_keys_own_range(key, value, expected) -> None:
    assert clamp_override(key, value) == expected


def test_int_keys_come_back_as_ints_truncated_like_settings() -> None:
    assert clamp_override("hash_distance_threshold", "20") == 20
    assert clamp_override("hash_distance_threshold", 20.9) == 20


@pytest.mark.parametrize("bad", [None, "abc", [], {}])
def test_a_value_that_is_not_a_number_is_a_400_not_a_500(bad) -> None:
    from bpp.errors import ValidationError

    with pytest.raises(ValidationError, match="hash_distance_threshold must be a number"):
        clamp_override("hash_distance_threshold", bad)

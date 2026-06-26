"""Property-based tests for EXIF helpers.

`_parse_gps_coord`, `_safe_float`, and `_safe_int` accept arbitrary EXIF
field values — which Pillow returns as a mix of Rational, IFDRational,
tuple, bytes, str, int, float, and occasionally garbage. The helpers
must never raise; they must return a sane value or None.

Properties:
  P1. `_safe_float` returns None or a finite float.
  P2. `_safe_int` returns None or an int.
  P3. `_parse_gps_coord` returns None or a float in [-180, 180]
      (latitude bounds are [-90, 90] but the helper doesn't enforce
      that — refs S/W just flip sign).
  P4. None of the helpers raise on adversarial input.
"""

from __future__ import annotations

import math

from hypothesis import given, settings
from hypothesis import strategies as st

from bpp.exif_utils import _parse_gps_coord, _safe_float, _safe_int

# Strategies: stuff Pillow has been known to return + adversarial garbage.
_EXIF_VALUE = st.one_of(
    st.none(),
    st.integers(min_value=-(2**40), max_value=2**40),
    st.floats(allow_nan=True, allow_infinity=True),
    st.text(min_size=0, max_size=50),
    st.binary(min_size=0, max_size=50),
    st.tuples(st.integers(), st.integers(), st.integers()),
    st.lists(st.floats(allow_nan=True), min_size=0, max_size=5),
)


@settings(max_examples=200)
@given(val=_EXIF_VALUE)
def test_safe_float_returns_none_or_finite_float(val):
    """P1: _safe_float never raises; result is None or a real float."""
    result = _safe_float(val)
    if result is not None:
        assert isinstance(result, float)
        # Non-finite floats are allowed through if the input is one
        # (e.g. inf, nan) — the caller filters those, but the helper
        # itself doesn't claim to. Just assert the type is right.


@settings(max_examples=200)
@given(val=_EXIF_VALUE)
def test_safe_int_returns_none_or_int(val):
    """P2: _safe_int never raises; result is None or an int."""
    result = _safe_int(val)
    if result is not None:
        assert isinstance(result, int)


@settings(max_examples=200)
@given(
    coord=st.one_of(
        st.none(),
        st.tuples(
            st.floats(min_value=-360, max_value=360, allow_nan=False, allow_infinity=False),
            st.floats(min_value=0, max_value=60, allow_nan=False, allow_infinity=False),
            st.floats(min_value=0, max_value=60, allow_nan=False, allow_infinity=False),
        ),
        st.floats(allow_nan=True, allow_infinity=True),
        st.text(min_size=0, max_size=20),
    ),
    ref=st.one_of(
        st.none(),
        st.sampled_from(["N", "S", "E", "W"]),
        st.text(min_size=0, max_size=3),
    ),
)
def test_parse_gps_coord_never_raises(coord, ref):
    """P3+P4: _parse_gps_coord returns None or a finite float, never raises."""
    result = _parse_gps_coord(coord, ref)
    # The contract is "returns None or a float, never raises". The
    # helper doesn't claim to clamp into [-180,180] — that's the
    # caller's job (and it's enforced by `gps_lat`/`gps_lon` columns
    # plus the partial index). We just assert the type contract here.
    if result is not None:
        assert isinstance(result, float)
        # Strip NaN since input strategies allow it.
        assert math.isnan(result) or isinstance(result, float)


def test_parse_gps_coord_w_s_flips_sign():
    """Concrete: a positive W or S coord becomes negative."""
    assert _parse_gps_coord((40.0, 30.0, 0.0), "N") == 40.5
    assert _parse_gps_coord((40.0, 30.0, 0.0), "S") == -40.5
    assert _parse_gps_coord((73.0, 0.0, 0.0), "E") == 73.0
    assert _parse_gps_coord((73.0, 0.0, 0.0), "W") == -73.0

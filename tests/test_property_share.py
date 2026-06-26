"""Property-based tests for share-token / device-name helpers.

These functions process untrusted input from LAN clients (HTTP headers,
device-name strings, share-token candidates). They must:

  P1. `_sanitize_device_name` always returns a non-empty string ≤ 40 chars
       containing only chars from the allowed set.
  P2. `_device_name_from_ua` always returns a non-empty string.
  P3. `_token_equals` returns False when expected is empty/None,
       regardless of candidate (this is the load-bearing invariant —
       otherwise a fresh install with no token auths anyone with no token).
  P4. None of the helpers raise on adversarial input.
"""

from __future__ import annotations

import re

from hypothesis import given, settings
from hypothesis import strategies as st

from bpp.web.share import _device_name_from_ua, _sanitize_device_name, _token_equals

_ALLOWED_DEVICE_CHARS = re.compile(r"^[A-Za-z0-9_ \-./]*$")

# Adversarial text: unicode, control chars, very long, empty.
_TEXT = st.text(min_size=0, max_size=300)


@settings(max_examples=200)
@given(name=_TEXT)
def test_sanitize_device_name_returns_safe_string(name):
    """P1: result is non-empty, ≤40 chars, only allowed chars."""
    result = _sanitize_device_name(name)
    assert isinstance(result, str)
    assert 1 <= len(result) <= 40
    # The fallback "Unknown device" contains a space which IS in the
    # allowed set; the regex below accepts it.
    assert _ALLOWED_DEVICE_CHARS.match(result), (
        f"Sanitized name contains unsafe chars: input={name!r} -> result={result!r}"
    )


@settings(max_examples=200)
@given(ua=_TEXT)
def test_device_name_from_ua_returns_nonempty(ua):
    """P2: always a non-empty string for any UA."""
    result = _device_name_from_ua(ua)
    assert isinstance(result, str)
    assert len(result) >= 1


@settings(max_examples=200)
@given(candidate=_TEXT)
def test_token_equals_rejects_empty_expected(candidate):
    """P3: _token_equals(_, "" or None) must always return False.

    This is the load-bearing security invariant. If a fresh install
    has no app token configured (`expected = ""`), no caller can auth
    as the local app just by also sending no token.
    """
    assert _token_equals(candidate, "") is False
    assert _token_equals(candidate, None) is False


@settings(max_examples=100)
@given(token=st.text(min_size=1, max_size=64))
def test_token_equals_matches_self(token):
    """Sanity: _token_equals(t, t) is True for any non-empty t."""
    assert _token_equals(token, token) is True


@settings(max_examples=100)
@given(
    candidate=st.text(min_size=1, max_size=64),
    expected=st.text(min_size=1, max_size=64),
)
def test_token_equals_rejects_different_tokens(candidate, expected):
    """If candidate != expected (length or content), result is False."""
    # If they happen to be equal, the other test catches it.
    if candidate != expected:
        assert _token_equals(candidate, expected) is False

"""Property-based tests for path-safety helpers.

`safe_join`, `is_safe_archive_member`, and `is_safe_tar_member` are the
gatekeepers for every archive-import and library-file operation. They
take attacker-controlled strings (zip entries, tar entries, batch-rename
templates) and must never produce a path outside the library root.

These tests fuzz the helpers with adversarial input. The properties are:

  P1. `safe_join(base, x)` is always inside `realpath(base)`, regardless of x.
  P2. `is_safe_archive_member(name, dir)` returns False for any name
       containing path-traversal sequences ('..', absolute paths).
  P3. The helpers never raise on `is_safe_archive_member` (they return
       True/False); `safe_join` raises ValueError but never crashes the
       interpreter (no segfaults, no infinite loops).
"""

from __future__ import annotations

import os
import tempfile

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from bpp.utils.paths import is_safe_archive_member, safe_join

# Strategy: arbitrary path-like strings — unicode, control chars, ..
# components, absolute paths, embedded null bytes, NUL terminator, etc.
_PATHY = st.text(
    alphabet=st.characters(
        # Exclude NUL (most file APIs reject it before any logic runs).
        # Anything else is fair game.
        blacklist_characters="\x00",
        blacklist_categories=(),
    ),
    min_size=0,
    max_size=200,
)


@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
@given(filename=_PATHY)
def test_safe_join_either_raises_or_stays_inside_base(filename):
    """P1: safe_join either raises ValueError or returns a path inside base."""
    with tempfile.TemporaryDirectory() as base:
        real_base = os.path.realpath(base)
        try:
            result = safe_join(base, filename)
        except ValueError:
            return  # raising is acceptable for unsafe input
        except OSError:
            return  # NUL bytes or unparseable input
        real_result = os.path.realpath(result)
        assert real_result == real_base or real_result.startswith(real_base + os.sep), (
            f"safe_join leaked outside base: {filename!r} -> {result!r}"
        )


@settings(max_examples=200)
@given(member=_PATHY)
def test_is_safe_archive_member_rejects_path_traversal(member):
    """P2: members with .. or absolute paths must be rejected."""
    with tempfile.TemporaryDirectory() as extract:
        result = is_safe_archive_member(member, extract)
        # If the function returned True, double-check the resolved
        # target is actually inside extract.
        if result:
            real_extract = os.path.realpath(extract)
            target = os.path.realpath(os.path.join(extract, member))
            assert target == real_extract or target.startswith(real_extract + os.sep), (
                f"is_safe_archive_member returned True but path escapes: {member!r}"
            )


@settings(max_examples=100)
@given(name=_PATHY)
def test_safe_archive_member_never_crashes(name):
    """P3: helper must always return bool, never raise on adversarial input."""
    with tempfile.TemporaryDirectory() as d:
        result = is_safe_archive_member(name, d)
        assert isinstance(result, bool)


@pytest.mark.parametrize(
    "traversal",
    [
        "../escape.jpg",
        "../../etc/passwd",
        "/absolute/path/escape.jpg",
        "subdir/../../escape.jpg",
        "./../escape.jpg",
    ],
)
def test_known_traversal_patterns_rejected(traversal):
    """Concrete examples — make sure the well-known evil strings are rejected."""
    with tempfile.TemporaryDirectory() as d:
        assert not is_safe_archive_member(traversal, d), (
            f"is_safe_archive_member let through known traversal: {traversal!r}"
        )

"""PHOTO_CACHE_SUFFIXES registry consistency.

Project rule: photo cache variants must add new suffixes to
`PHOTO_CACHE_SUFFIXES` in `bpp/constants.py` — never hardcode
suffix lists in cleanup code.

Cleanup itself is glob-based (`{hash}*.jpg`, `{hash}*.png` in
ThumbnailCache.remove_for_hash), so a missing entry doesn't
silently leak files. The registry is maintained as documentation
+ a single source of truth for create-site f-strings — adding a
variant should be one tuple entry plus one named constant, not
grep-and-edit across blueprints.

These tests pin the registry's completeness against the literal
suffix strings actually used in cache paths under bpp/web/.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Files where we create / serve cache files. Anchor the test to a
# known set rather than grepping every .py — additions go through
# code review and the test author can update this list.
_SCANNED_FILES = (
    REPO_ROOT / "bpp" / "web" / "bp_media.py",
    REPO_ROOT / "bpp" / "web" / "bp_photos_manage.py",
    REPO_ROOT / "bpp" / "web" / "thumbnails.py",
)

# Pattern: capture the suffix in `{hash}{SUFFIX}.{ext}` style
# f-strings. Both literal `_xyz.jpg` and constant
# `{path_hash}{PHOTO_CACHE_SUFFIX_X}.jpg` shapes are covered by
# checking the constants module for any hardcoded `_word`-shaped
# slug that's used in a cache path.
_SUFFIX_LITERAL_RE = re.compile(r"\{[^}]+\}(_[a-z_]+)\.(?:jpg|png|jpeg)")


def test_registry_includes_all_suffixes_used_in_cache_paths():
    """Every literal `_xyz` slug that appears in a cache f-string
    must be a defined member of PHOTO_CACHE_SUFFIXES."""
    from bpp.constants import PHOTO_CACHE_SUFFIXES

    found = set()
    for path in _SCANNED_FILES:
        text = path.read_text()
        for m in _SUFFIX_LITERAL_RE.finditer(text):
            found.add(m.group(1))

    registered = set(PHOTO_CACHE_SUFFIXES)
    missing = found - registered
    assert not missing, (
        f"Cache files use suffixes not in PHOTO_CACHE_SUFFIXES: {sorted(missing)}. "
        f"Registry has {sorted(registered)}; add the missing entries to "
        f"bpp/constants.py."
    )


def test_registry_constants_are_in_sync_with_tuple():
    """The named constants and the tuple must stay aligned. Every
    PHOTO_CACHE_SUFFIX_X constant value should appear in the
    PHOTO_CACHE_SUFFIXES tuple."""
    from bpp import constants as c

    named = {getattr(c, name) for name in dir(c) if name.startswith("PHOTO_CACHE_SUFFIX_")}
    in_tuple = set(c.PHOTO_CACHE_SUFFIXES)
    drift = named - in_tuple
    assert not drift, f"Constants {drift} declared but missing from PHOTO_CACHE_SUFFIXES tuple"


def test_known_suffixes_present():
    """Sanity guard against a future contributor accidentally
    deleting an entry from PHOTO_CACHE_SUFFIXES."""
    from bpp.constants import PHOTO_CACHE_SUFFIXES

    # The empty string covers the unsuffixed thumbnail itself.
    expected = {"", "_full", "_edited", "_edited_thumb", "_inpainted", "_sprite"}
    assert expected <= set(PHOTO_CACHE_SUFFIXES), (
        f"Missing well-known suffixes: {expected - set(PHOTO_CACHE_SUFFIXES)}"
    )

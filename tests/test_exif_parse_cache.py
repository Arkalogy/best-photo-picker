"""R8-M8: build_photo_dict caches the parsed EXIF on the item dict.

The `WebAppState.state["analysis"]` list is held across requests,
so `build_photo_dict` is invoked against the SAME item dicts on
every /api/v1/photos page render, every recompute response, every
album-photos render. Without a cache, each invocation re-parsed
the `exif_json` JSON text — up to 5000 parses per page at the
recompute cap, multiplied across requests.

The cache stashes the parsed dict on `item["_exif_parsed"]` after
the first parse. Subsequent calls skip the parse and read from
the cached dict.

This test pins three things:
  1. First call parses; second call doesn't (verified via spy).
  2. The cached value matches the on-disk JSON text's parsed
     shape (same answer either way).
  3. Cache key is local to the item dict — two items with the
     same `exif_json` payload don't share state.
"""

from __future__ import annotations

import json

from bpp.web.photo_dict import build_photo_dict


def test_first_call_parses_subsequent_calls_skip(monkeypatch):
    """A spy on `safe_json_loads` counts parses across two calls
    against the same item. The second call must use the cache."""
    from bpp.web import photo_dict as pd

    parse_count = [0]
    real_loads = pd.safe_json_loads

    def _spy(text, default=None, *, context=""):
        parse_count[0] += 1
        return real_loads(text, default, context=context)

    monkeypatch.setattr(pd, "safe_json_loads", _spy)

    item = {
        "filepath": "/tmp/x.jpg",
        "exif_json": json.dumps({"camera_make": "Canon", "camera_model": "EOS R5", "iso": 400}),
    }

    build_photo_dict(item, None)
    assert parse_count[0] == 1, "First call must parse the JSON exactly once"

    build_photo_dict(item, None)
    assert parse_count[0] == 1, "Second call against the SAME item must hit the cache, not re-parse"


def test_cached_value_matches_fresh_parse():
    """The cached dict must equal what a fresh parse would produce
    — caching can't change the response shape."""
    item = {
        "filepath": "/tmp/x.jpg",
        "exif_json": json.dumps({"camera_make": "Canon", "iso": 400}),
    }

    first = build_photo_dict(item, None)
    second = build_photo_dict(item, None)

    assert first["exif"] == {"camera_make": "Canon", "iso": 400}
    assert first["exif"] == second["exif"]


def test_cache_is_per_item_not_shared(monkeypatch):
    """Two different items with similar payloads must each parse
    once — the cache key is the item dict itself, not the raw
    JSON content."""
    from bpp.web import photo_dict as pd

    parse_count = [0]
    real_loads = pd.safe_json_loads

    def _spy(text, default=None, *, context=""):
        parse_count[0] += 1
        return real_loads(text, default, context=context)

    monkeypatch.setattr(pd, "safe_json_loads", _spy)

    payload = json.dumps({"camera_make": "Canon"})
    item_a = {"filepath": "/tmp/a.jpg", "exif_json": payload}
    item_b = {"filepath": "/tmp/b.jpg", "exif_json": payload}

    build_photo_dict(item_a, None)
    build_photo_dict(item_b, None)
    assert parse_count[0] == 2, (
        "Two independent items must each parse — caching is per-item, "
        "not a global memoization keyed on payload"
    )

    # And another call against item_a hits the cache
    build_photo_dict(item_a, None)
    assert parse_count[0] == 2


def test_already_dict_exif_skips_parse_and_cache(monkeypatch):
    """When `exif_json` is already a dict (legacy code path that
    feeds parsed data straight in), neither parse nor cache write
    should fire."""
    from bpp.web import photo_dict as pd

    parse_count = [0]
    real_loads = pd.safe_json_loads

    def _spy(text, default=None, *, context=""):
        parse_count[0] += 1
        return real_loads(text, default, context=context)

    monkeypatch.setattr(pd, "safe_json_loads", _spy)

    item = {"filepath": "/tmp/x.jpg", "exif_json": {"camera_make": "Canon"}}
    out = build_photo_dict(item, None)
    assert parse_count[0] == 0, "Already-parsed dict must not be re-parsed"
    assert out["exif"] == {"camera_make": "Canon"}
    # No cache write — the source was already a dict, nothing to cache
    assert "_exif_parsed" not in item

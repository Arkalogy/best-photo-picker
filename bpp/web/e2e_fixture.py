"""Is the active library the Playwright suite's synthetic fixture? The
sentinel at its root lets the e2e mutation helpers and the destructive
rate-limit bypass tell it from a real library. Lives outside the
endpoint files so the auth boundary can import it.
"""

from __future__ import annotations

import os

from bpp.web.state import get_ctx

_E2E_FIXTURE_SENTINEL = ".bpp-e2e-fixture"
_e2e_fixture_cache: dict[str, bool] = {}


def is_e2e_fixture_library() -> bool:
    """True when the active ``--library`` has the e2e sentinel at its root.

    Consumed by (a) the ``/api/v1/_diag/is_e2e_fixture`` diagnostic the
    mutation helpers gate on, and (b) the destructive-endpoint rate-limit
    bypass in ``app.py``. The Playwright suite fires far more mutations
    per minute than any human, so without the bypass later mutating specs
    hit the 60/min limiter and 429. A real user library never carries the
    sentinel, so production rate limits are untouched.

    ``ctx.dirs['root']`` is the actual ``--library`` path. ``state``'s
    ``library_path`` can fall back to a default user library under TESTING,
    so don't trust it here — the wrong default would silently green-light
    mutations (or a limiter bypass) against the user's real photos.
    """

    ctx = get_ctx()
    library_root = (ctx.dirs or {}).get("root") or ""
    if not library_root:
        return False
    cached = _e2e_fixture_cache.get(library_root)
    if cached is None:
        cached = os.path.exists(os.path.join(library_root, _E2E_FIXTURE_SENTINEL))
        _e2e_fixture_cache[library_root] = cached
    return cached

"""API versioning contract: every /api/* route lives under /api/v1/.

Why this test exists: when this codebase shipped the v1 prefix, the
"contract" we wrote down in docs/API.md said `/api/v1/...` is the
ONLY supported shape — there are no unversioned aliases. A future
contributor adding a route the old way (`@bp.get("/api/photos/foo")`)
would silently break the contract: the route would still work, but
clients written against the documented v1 surface would 404 against
that endpoint.

This test walks the live Flask url_map and asserts that every route
starting with `/api/` is also under `/api/v1/`. It catches:

  - new endpoints declared without the version prefix
  - legacy endpoints that escaped the mass rename
  - typos like `/apiv1/` or `/api/v2/` (until v2 is intentionally added)
"""

from __future__ import annotations

import re

import pytest


@pytest.fixture()
def app():
    """Real app with all blueprints registered — that's what we audit."""
    from bpp.web.app import create_app

    return create_app()


def _api_routes(app):
    """All registered routes that start with `/api/`, normalized."""
    routes = []
    for rule in app.url_map.iter_rules():
        if rule.rule.startswith("/api/"):
            routes.append(rule.rule)
    return routes


def test_no_unversioned_api_routes(app):
    """Every `/api/...` route must be under `/api/v1/`. No aliases."""
    offenders = [r for r in _api_routes(app) if not r.startswith("/api/v1/")]
    assert offenders == [], (
        f"Found {len(offenders)} unversioned /api/ route(s) that escaped the "
        f"v1 prefix. Add the `/api/v1/` segment in the route declaration:\n"
        + "\n".join(f"  - {r}" for r in offenders)
    )


def test_at_least_one_v1_route_exists(app):
    """Sanity: the v1 prefix isn't empty — if it is, the previous test
    is vacuously true and we'd never catch a regression."""
    v1_routes = [r for r in _api_routes(app) if r.startswith("/api/v1/")]
    # We have ~150+ endpoints; pick a low floor so we catch a "all routes
    # were accidentally deleted" failure mode without making the test
    # brittle against intentional removals.
    assert len(v1_routes) > 50, (
        f"Only {len(v1_routes)} /api/v1/ routes registered — did blueprint "
        "registration break? Expected dozens of endpoints."
    )


def test_no_v2_or_higher_routes_yet(app):
    """Until we explicitly cut a v2, the only API version is v1.
    A surprise `/api/v2/` route means someone misread the contract."""
    pattern = re.compile(r"^/api/v(\d+)/")
    seen_versions = set()
    for r in _api_routes(app):
        m = pattern.match(r)
        if m:
            seen_versions.add(int(m.group(1)))
    assert seen_versions == {1}, (
        f"Expected only API v1 routes; found versions {sorted(seen_versions)}. "
        "If you intend to add v2, update this test and docs/API.md's versioning "
        "section together."
    )

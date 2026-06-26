"""Route registration regression tests.

Ensures all API routes remain registered after blueprint refactoring.
Also verifies EventSource cleanup patterns in JS source files.
"""

from __future__ import annotations

import os
import re

import pytest


@pytest.fixture()
def app():
    """Create test app instance."""
    from bpp.web.app import create_app

    return create_app()


# ── H7: Route registration ──────────────────────────────────────────────────


# All routes that must exist in the app — if any are missing after refactoring,
# the split broke something.
EXPECTED_PHOTOS_ROUTES = [
    ("GET", "/api/v1/photos"),
    ("GET", "/api/v1/photos/timeline"),
    ("GET", "/api/v1/photos/map"),
    ("GET", "/api/v1/photos/preview"),
    ("POST", "/api/v1/recompute"),
    ("POST", "/api/v1/optimize"),
    ("POST", "/api/v1/export"),
    ("POST", "/api/v1/open-folder"),
    ("POST", "/api/v1/override"),
    ("POST", "/api/v1/favorite"),
    ("POST", "/api/v1/batch/override"),
    ("POST", "/api/v1/batch/favorite"),
    ("GET", "/api/v1/overrides"),
    ("POST", "/api/v1/photos/delete"),
    ("POST", "/api/v1/photos/restore"),
    ("POST", "/api/v1/photos/delete-permanent"),
    ("GET", "/api/v1/photos/deleted"),
    ("POST", "/api/v1/photos/hide"),
    ("POST", "/api/v1/photos/unhide"),
    ("GET", "/api/v1/photos/hidden"),
    ("POST", "/api/v1/photos/enhance"),
    ("POST", "/api/v1/photos/reset-edits"),
    ("GET", "/api/v1/photos/edits"),
    ("POST", "/api/v1/photos/save-edits"),
    ("POST", "/api/v1/batch/rename/preview"),
    ("POST", "/api/v1/batch/rename/apply"),
    ("POST", "/api/v1/photos/<int:photo_id>/date"),
    ("GET", "/api/v1/inpaint/status"),
    ("POST", "/api/v1/photos/<int:photo_id>/inpaint"),
]

EXPECTED_FACES_ROUTES = [
    ("POST", "/api/v1/faces/extract"),
    ("POST", "/api/v1/faces/retry"),
    ("GET", "/api/v1/faces/extract/progress"),
    ("GET", "/api/v1/faces/clusters"),
    ("POST", "/api/v1/faces/avatar"),
    ("GET", "/api/v1/faces/cluster/<int:cluster_id>"),
    ("POST", "/api/v1/faces/merge"),
    ("POST", "/api/v1/faces/dismiss"),
    ("POST", "/api/v1/faces/recluster"),
    ("GET", "/api/v1/faces/photo/<path_hash>"),
    ("GET", "/api/v1/faces/crop/<path_hash>/<int:face_index>"),
    ("POST", "/api/v1/faces/tag"),
    ("DELETE", "/api/v1/faces/tag"),
    ("POST", "/api/v1/faces/reassign"),
    ("GET", "/api/v1/groups"),
    ("POST", "/api/v1/clip/extract"),
    ("GET", "/api/v1/clip/progress"),
    ("GET", "/api/v1/dedup/feedback/stats"),
]

# Coverage for the bp_share blueprint endpoints. Without explicit
# coverage here, a future blueprint refactor could silently drop a
# share/pair endpoint and the existing route_registration tests
# would not notice (they only enumerated photos + faces).
EXPECTED_SHARE_ROUTES = [
    ("GET", "/api/v1/share/info"),
    ("POST", "/api/v1/share/toggle"),
    ("POST", "/api/v1/share/revoke"),
    ("GET", "/api/v1/share/pair/status"),
    ("POST", "/api/v1/share/pair/request"),
    ("GET", "/api/v1/share/devices"),
    ("POST", "/api/v1/share/devices/<int:device_id>/approve"),
    ("POST", "/api/v1/share/devices/<int:device_id>/revoke"),
    ("GET", "/api/v1/share/qr"),
]


def _get_registered_routes(app) -> set[tuple[str, str]]:
    """Extract (method, rule) pairs from a Flask app."""
    routes = set()
    for rule in app.url_map.iter_rules():
        for method in rule.methods:
            if method in ("HEAD", "OPTIONS"):
                continue
            routes.add((method, rule.rule))
    return routes


def test_all_photos_routes_registered(app):
    """Every photos blueprint route must be registered in the app."""
    registered = _get_registered_routes(app)
    missing = []
    for method, path in EXPECTED_PHOTOS_ROUTES:
        if (method, path) not in registered:
            missing.append(f"{method} {path}")
    assert not missing, f"Missing photos routes: {missing}"


def test_all_faces_routes_registered(app):
    """Every faces blueprint route must be registered in the app."""
    registered = _get_registered_routes(app)
    missing = []
    for method, path in EXPECTED_FACES_ROUTES:
        if (method, path) not in registered:
            missing.append(f"{method} {path}")
    assert not missing, f"Missing faces routes: {missing}"


def test_all_share_routes_registered(app):
    """Every share blueprint route must be registered. This guard
    ensures the next blueprint refactor can't silently drop a share
    route."""
    registered = _get_registered_routes(app)
    missing = []
    for method, path in EXPECTED_SHARE_ROUTES:
        if (method, path) not in registered:
            missing.append(f"{method} {path}")
    assert not missing, f"Missing share routes: {missing}"


# ── H8: EventSource onerror ──────────────────────────────────────────────────


_JS_DIR = os.path.join(os.path.dirname(__file__), "..", "bpp", "web", "static", "js")


def _find_eventsource_blocks(filepath: str) -> list[tuple[str, int]]:
    """Find EventSource variable names and their line numbers."""
    results = []
    with open(filepath) as f:
        for i, line in enumerate(f, 1):
            pat = r"(?:const|let|var)\s+(\w+)\s*=\s*(?:new\s+EventSource|authEventSource)\("
            m = re.search(pat, line)
            if m:
                results.append((m.group(1), i))
    return results


def _file_has_onerror(filepath: str, var_name: str) -> bool:
    """Check that the EventSource variable has an onerror handler."""
    with open(filepath) as f:
        content = f.read()
    # Match var_name.onerror
    return f"{var_name}.onerror" in content


# Discover EventSource users dynamically across the live JS tree
# (modules/*.mjs + any remaining classic files) rather than a
# hardcoded list of `.js` paths that no longer exist after the
# ES-module migration. Skip-on-missing was masking the regression.
def _all_js_module_files() -> list[str]:
    """Return every .js / .mjs source under bpp/web/static/js/ that
    actually exists. Discovered at test-collection time so the list
    can't go stale silently."""
    out: list[str] = []
    for root, _dirs, files in os.walk(_JS_DIR):
        for name in files:
            if name.endswith((".js", ".mjs")):
                # Skip vendored files if any (none today, but future-proof)
                if "/vendor/" in root or "\\vendor\\" in root:
                    continue
                out.append(os.path.join(root, name))
    return out


def _files_with_eventsource() -> list[str]:
    """Filter to JS files that actually instantiate an EventSource —
    that's the population we care about for the onerror check."""
    out = []
    for path in _all_js_module_files():
        with open(path) as f:
            content = f.read()
        if "new EventSource(" in content or "authEventSource(" in content:
            out.append(path)
    return out


# D-09: factory files that legitimately wrap/return an EventSource
# without binding it to a local variable. The CALLER of the factory
# is responsible for the .onerror handler; nothing the factory file
# does affects the contract. New entries here MUST be reviewed.
_EVENTSOURCE_FACTORY_ALLOWLIST = frozenset(
    {
        # api-client.mjs's authEventSource(url) returns
        # `new EventSource(url + ...)` directly. Callers (analysis,
        # faces, clip, import-worker, etc.) bind it to a variable and
        # set .onerror themselves — those calls show up in their own
        # files and are checked there.
        "modules/api-client.mjs",
    }
)


def test_every_eventsource_has_onerror():
    """Every EventSource instance in the live JS tree must have an
    .onerror handler. Without it the browser silently auto-reconnects
    on disconnect, leaking connections and leaving the user staring
    at a frozen progress bar.

    Discovers files dynamically (no hardcoded `.js` list that goes
    stale after the ES-module migration). Files that contain
    `new EventSource(` but where the var-binding regex finds NOTHING
    are flagged as scanner blind spots unless they're in the
    factory allowlist (D-09)."""
    files = _files_with_eventsource()
    assert files, (
        "No EventSource usages found anywhere in the JS tree — the "
        "scan is broken (or the project genuinely has no SSE consumers, "
        "in which case this floor assertion needs to go)."
    )
    failures = []
    for path in files:
        rel = os.path.relpath(path, os.path.join(_JS_DIR, "..", "..", ".."))
        # Allowlist key is the path relative to bpp/web/static/js/
        # itself, normalized to forward-slash. e.g.
        # "modules/api-client.mjs".
        allowlist_key = os.path.relpath(path, _JS_DIR).replace("\\", "/")
        es_vars = _find_eventsource_blocks(path)
        if not es_vars:
            # File has the substring but the var-binding regex didn't
            # match. Two shapes:
            #   (a) factory: `return new EventSource(...)` — caller
            #       binds the result. OK if file is allowlisted.
            #   (b) genuine blind spot: var assigned via destructuring,
            #       passed to a callback, etc. — flag as scanner gap.
            if allowlist_key in _EVENTSOURCE_FACTORY_ALLOWLIST:
                continue
            failures.append(
                f"{rel} — contains `new EventSource(`/`authEventSource(` "
                "but the var-binding scanner found no instance. Either "
                "rewrite the call to bind the EventSource to a local "
                "variable so .onerror coverage can be verified, or add "
                "the file to _EVENTSOURCE_FACTORY_ALLOWLIST after a "
                "review confirming callers handle .onerror."
            )
            continue
        for var_name, line_no in es_vars:
            if not _file_has_onerror(path, var_name):
                failures.append(
                    f"{rel}:{line_no} — EventSource '{var_name}' missing .onerror handler"
                )
    assert not failures, (
        "EventSource without onerror handler — the browser will "
        "auto-reconnect indefinitely on failure, leaking connections:\n"
        + "\n".join(f"  - {f}" for f in failures)
    )

"""Regression: every API route handler has a substantive docstring.

Why this exists: the OSS contribution surface is `docs/API.md` plus
the source code. A new contributor reading `bp_albums.py` to figure
out what `api_album_recompute` does shouldn't have to reverse-
engineer the function body — a 2-3 line docstring at the top of the
handler is the cheapest possible reference. This test locks the
floor so a future contributor can't add a new endpoint without one.

Strategy: parse every `bpp/web/bp_*.py` file with `ast`, find every
function decorated with `@bp.get/post/put/delete/route`, and assert
the function has a docstring of at least MIN_LEN characters of
non-trivial prose.

The 30-character floor is deliberately low — it allows a one-line
docstring if the endpoint is genuinely simple ("Return X by id."),
but rejects empty strings and one-word stubs.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = REPO_ROOT / "bpp" / "web"

# A handler with fewer than this many chars of docstring fails the
# test. Calibrated so "List all albums." (16 chars) fails but
# "Return the active library's metadata block." (43 chars) passes.
MIN_LEN = 30

# Decorator method names that mark a function as a route handler.
_ROUTE_METHODS = frozenset({"get", "post", "put", "delete", "patch", "route"})


def _is_route_decorator(dec: ast.expr) -> bool:
    """True if `dec` is `@bp.get(...)` / `@bp.post(...)` / etc."""
    target = dec.func if isinstance(dec, ast.Call) else dec
    return (
        isinstance(target, ast.Attribute)
        and target.attr in _ROUTE_METHODS
        and isinstance(target.value, ast.Name)
        and target.value.id == "bp"
    )


def _route_handlers() -> list[tuple[str, int, str, str]]:
    """Return (file_relpath, lineno, function_name, docstring) for
    every route handler in bpp/web/bp_*.py."""
    out: list[tuple[str, int, str, str]] = []
    for py in sorted(WEB_DIR.glob("bp_*.py")):
        text = py.read_text(encoding="utf-8")
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if not any(_is_route_decorator(d) for d in node.decorator_list):
                continue
            ds = ast.get_docstring(node) or ""
            out.append(
                (
                    str(py.relative_to(REPO_ROOT)),
                    node.lineno,
                    node.name,
                    ds.strip(),
                )
            )
    return out


def test_every_route_has_substantive_docstring():
    """Every @bp.get/post/put/delete handler must have a docstring of
    at least MIN_LEN characters."""
    offenders: list[str] = []
    for relpath, lineno, fname, ds in _route_handlers():
        if len(ds) < MIN_LEN:
            offenders.append(
                f"{relpath}:{lineno} {fname}() — "
                f"docstring is {len(ds)} chars (need >={MIN_LEN}): {ds!r}"
            )
    assert not offenders, (
        f"Found {len(offenders)} API route handler(s) with missing or "
        f"too-short docstrings (contract: every endpoint has 2-4 "
        f"lines covering what it returns / does, non-obvious params, "
        f"and side effects).\n\n" + "\n".join(f"  - {o}" for o in offenders)
    )


def test_handlers_were_actually_found():
    """Sanity floor: bpp/web has dozens of route handlers. If this
    drops below the floor, the AST scan probably broke (e.g., the
    decorator pattern changed) and the previous test would be
    vacuously green."""
    handlers = _route_handlers()
    assert len(handlers) >= 100, (
        f"AST scan found only {len(handlers)} route handlers — expected "
        "100+ across all blueprints. Either a blueprint was removed or "
        "the decorator detection broke."
    )


def test_api_md_does_not_advertise_removed_propagated_field():
    """R12-L1: `docs/API.md` previously documented a `propagated`
    field in `/api/v1/faces/merge` and `/api/v1/faces/reassign`
    response examples. Both handlers actually return only
    `{"status": "...", "albums": [...]}` — `propagated` was a
    leftover from a feature that never shipped. A user copying the
    documented JSON shape into a client would silently ignore a
    non-existent field; worse, an automated test or schema
    validator would mismatch reality.

    Source-scan: `propagated` must NOT appear in API.md for the
    face merge / reassign sections. If a future contributor
    legitimately adds the field back to the API, both this test
    AND the corresponding handler need to change together —
    keeping the docs and code in sync."""
    api_md = (REPO_ROOT / "docs" / "API.md").read_text(encoding="utf-8")
    # Cheap source-scan: the JSON example block for these endpoints
    # is the only place the word `propagated` appeared. Keep the
    # rule scoped to that token.
    assert "propagated" not in api_md, (
        "docs/API.md mentions `propagated` — both face merge and "
        "reassign handlers return only {status, albums}. Either remove "
        "the field from the docs OR add it to the handler responses, "
        "but not document a field that doesn't exist in code."
    )


def test_no_duplicate_handler_names():
    """Two handlers with the same function name in different files
    would cause silent route shadowing if both decorators registered
    the same path. Catch the typo before it produces a 404."""
    seen: dict[str, str] = {}
    duplicates: list[str] = []
    for relpath, _lineno, fname, _ds in _route_handlers():
        if fname in seen and seen[fname] != relpath:
            duplicates.append(f"{fname}() in both {seen[fname]} and {relpath}")
        else:
            seen[fname] = relpath
    assert not duplicates, (
        f"Found {len(duplicates)} duplicate route handler name(s):\n"
        + "\n".join(f"  - {d}" for d in duplicates)
    )

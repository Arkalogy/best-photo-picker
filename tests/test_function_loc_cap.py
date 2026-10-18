"""150-LOC cap on functions, the file cap's sibling (see test_loc_cap.py).

A function past 150 lines is several steps that lost their names. The
cap makes that mechanical:

- Any function NOT in BASELINE must be <= 150 lines.
- BASELINE functions (over cap when the gate landed, 2026-10-18) are
  ratcheted: they may never GROW past their recorded size, and the
  moment one is split under the cap it must be REMOVED from the list
  (shrink-only — a function that left can't come back).

This covers the Python package; the page modules have the same gate in
``tests-js/function-loc-cap.test.mjs`` (a parsed scan, so it runs where
the JS toolchain is). When this fails because a function grew, split
it; do not raise a number.
"""

from __future__ import annotations

import ast
import functools
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAP = 150

# (file, function) -> size when measured. Shrink-only; never raise one.
BASELINE: dict[tuple[str, str], int] = {
    ("bpp/config_schema.py", "_register_builtins"): 247,
    ("bpp/scoring/model_manifest.py", "all_models"): 197,
    ("bpp/cli.py", "build_parser"): 195,
    ("bpp/commands/db_restore.py", "do_db_restore_backup"): 189,
    ("bpp/db/library.py", "import_folder"): 187,
    ("bpp/db/face_identity_remap.py", "_remap_names_and_tags"): 180,
    ("bpp/db/backup.py", "backup_db"): 178,
    ("bpp/db/live_photo.py", "split_scan_for_confirmed_sidecars"): 164,
    ("bpp/commands/pick.py", "do_pick"): 155,
}

Hit = tuple[str, str, int, int]  # file, name, line, loc


@functools.cache
def _functions() -> tuple[Hit, ...]:
    hits: list[Hit] = []
    for p in sorted((ROOT / "bpp").rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(ROOT).as_posix()
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.end_lineno:
                hits.append((rel, node.name, node.lineno, node.end_lineno - node.lineno + 1))
    return tuple(hits)


def test_no_function_over_cap() -> None:
    """Every function outside the baseline stays at or under 150 LOC."""
    over = [h for h in _functions() if h[3] > CAP and (h[0], h[1]) not in BASELINE]
    assert not over, "Functions over the 150-LOC cap (split them; do not add to BASELINE):\n  " + (
        "\n  ".join(f"{f}:{line} {name}() {loc} LOC" for f, name, line, loc in over)
    )


def test_baseline_functions_only_shrink() -> None:
    """A baseline function never grows, and leaves the list once under cap."""
    # A nested def can share the outer one's name: the largest is the outer one.
    sizes: dict[tuple[str, str], int] = {}
    for f, name, _line, loc in _functions():
        sizes[(f, name)] = max(loc, sizes.get((f, name), 0))
    problems = []
    for key, recorded in BASELINE.items():
        loc = sizes.get(key)
        if loc is None:
            problems.append(f"{key[0]} {key[1]}(): gone or renamed; drop it from BASELINE")
        elif loc > recorded:
            problems.append(f"{key[0]} {key[1]}(): grew {recorded} -> {loc} LOC")
        elif loc <= CAP:
            problems.append(f"{key[0]} {key[1]}(): now {loc} LOC; remove it from BASELINE")
    assert not problems, "\n  ".join(["BASELINE drift:", *problems])

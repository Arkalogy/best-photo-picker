"""150-LOC cap on functions, the file cap's sibling (see test_loc_cap.py).

A function past 150 lines is several steps that lost their names, so
every function in the package must be <= 150 lines. This covers the
Python package; the page modules have the same gate in
``tests-js/function-loc-cap.test.mjs`` (a parsed scan, so it runs where
the JS toolchain is). When this fails because a function grew, split
it; do not raise the cap.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAP = 150


def test_no_function_over_cap() -> None:
    over = []
    for p in sorted((ROOT / "bpp").rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(ROOT).as_posix()
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.end_lineno:
                loc = node.end_lineno - node.lineno + 1
                if loc > CAP:
                    over.append(f"{rel}:{node.lineno} {node.name}() {loc} LOC")
    assert not over, "Functions over the 150-LOC cap (split them; do not raise the cap):\n  " + (
        "\n  ".join(over)
    )

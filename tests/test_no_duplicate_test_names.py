"""No two tests in one module share a name.

pytest collects a module by its namespace, so a second ``def test_x`` in
the same scope silently replaces the first and the test it shadowed never
runs. Ruff's F811 would say so, but the split test modules switch it off
file-wide (they import shared fixtures by name and re-bind them as test
parameters), so this gate keeps the check mechanical.
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _duplicates(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    problems = []

    def scan(body: list[ast.stmt], scope: str) -> None:
        names = Counter(
            n.name
            for n in body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test_")
        )
        problems.extend(f"{scope}{name} x{count}" for name, count in names.items() if count > 1)

    scan(tree.body, "")
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            scan(node.body, f"{node.name}.")
    return problems


def test_no_test_module_defines_a_test_name_twice():
    found = []
    for path in sorted((ROOT / "tests").rglob("test_*.py")):
        for problem in _duplicates(path):
            found.append(f"{path.relative_to(ROOT)}: {problem}")
    assert not found, "Duplicate test names (the earlier definition never runs):\n  " + "\n  ".join(
        found
    )

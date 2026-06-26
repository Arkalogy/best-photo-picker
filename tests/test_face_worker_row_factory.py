"""TDD test for H-3: face worker connection must use init_db (sets row_factory)."""

from __future__ import annotations

import ast
import os


def test_face_worker_uses_init_db():
    """FaceWorker._run must obtain its connection via init_db(),
    which sets row_factory = sqlite3.Row and registers it in the
    thread-local pool for proper WAL checkpoint on shutdown."""
    path = os.path.join(os.path.dirname(__file__), "..", "bpp", "web", "face_worker.py")
    with open(path) as f:
        source = f.read()

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run":
            # Search for a call to init_db(...)
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    func = child.func
                    if isinstance(func, ast.Name) and func.id == "init_db":
                        return  # Found it — test passes
            break

    raise AssertionError(
        "FaceWorker._run() must use init_db() to obtain its DB connection "
        "(not raw sqlite3.connect). init_db() sets row_factory and registers "
        "in the thread-local pool."
    )

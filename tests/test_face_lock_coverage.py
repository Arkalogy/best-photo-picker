"""TDD tests for H-2: api_faces_extract and api_set_avatar must use @with_face_lock."""

from __future__ import annotations

import ast
import os


def _get_decorators(func_name: str, source: str) -> list[str]:
    """Parse source and return decorator names applied to a function."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            names = []
            for dec in node.decorator_list:
                if isinstance(dec, ast.Attribute):
                    names.append(dec.attr)
                elif isinstance(dec, ast.Name):
                    names.append(dec.id)
                elif isinstance(dec, ast.Call):
                    if isinstance(dec.func, ast.Name):
                        names.append(dec.func.id)
                    elif isinstance(dec.func, ast.Attribute):
                        names.append(dec.func.attr)
            return names
    return []


def _read_bp_faces():
    path = os.path.join(os.path.dirname(__file__), "..", "bpp", "web", "bp_faces.py")
    with open(path) as f:
        return f.read()


def _read_bp_faces_extract():
    """api_faces_extract + api_faces_retry moved to bp_faces_extract.py
    in the v0.1 split. Tests that check lock decoration on those two
    handlers now read from the extract module instead of bp_faces."""
    path = os.path.join(os.path.dirname(__file__), "..", "bpp", "web", "bp_faces_extract.py")
    with open(path) as f:
        return f.read()


class TestFaceLockCoverage:
    def test_extract_has_face_lock(self):
        source = _read_bp_faces_extract()
        decs = _get_decorators("api_faces_extract", source)
        assert "with_face_lock" in decs, "api_faces_extract must have @with_face_lock decorator"

    def test_set_avatar_has_face_lock(self):
        source = _read_bp_faces()
        decs = _get_decorators("api_set_avatar", source)
        assert "with_face_lock" in decs, "api_set_avatar must have @with_face_lock decorator"

    def test_retry_has_face_lock(self):
        """Existing: retry already has the lock — verify it stays."""
        source = _read_bp_faces_extract()
        decs = _get_decorators("api_faces_retry", source)
        assert "with_face_lock" in decs

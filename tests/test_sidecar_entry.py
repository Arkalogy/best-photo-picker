"""The desktop sidecar's PyInstaller entry must call freeze_support first."""

from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_build_uses_sidecar_entry():
    script = (ROOT / "desktop" / "build-sidecar.sh").read_text()
    assert "PyInstaller desktop/bpp-server.spec" in script
    spec = (ROOT / "desktop" / "bpp-server.spec").read_text()
    assert '[str(DESKTOP / "sidecar_entry.py")]' in spec
    assert "cli.py" not in spec


def test_freeze_support_runs_before_bpp_import():
    tree = ast.parse((ROOT / "desktop" / "sidecar_entry.py").read_text())
    main_block = next(
        n for n in tree.body if isinstance(n, ast.If) and "__main__" in ast.unparse(n.test)
    )
    calls = [ast.unparse(s) for s in main_block.body]
    freeze = next(i for i, c in enumerate(calls) if "freeze_support()" in c)
    bpp_import = next(i for i, c in enumerate(calls) if "from bpp" in c)
    assert freeze < bpp_import, calls
    top_imports = [ast.unparse(n) for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not any("bpp" in i for i in top_imports), top_imports

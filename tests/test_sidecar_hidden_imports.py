"""Every module bpp loads indirectly (``import_gated``, ``requires`` or
``importable`` with a module name) is bundled in the Mac app's build: named
as a hidden import, imported normally somewhere in bpp (PyInstaller sees
that), or deliberately excluded. PyInstaller cannot see indirect imports,
and the built app fails at first use (re-clustering and the face-model
switch once failed this way)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDIRECT = re.compile(r"""\b(?:import_gated|requires|importable)\(\s*["']([\w.]+)["']""")


def _sources() -> dict[Path, str]:
    return {p: p.read_text(encoding="utf-8") for p in (ROOT / "bpp").rglob("*.py")}


def _imported_normally(module: str, sources: dict[Path, str]) -> bool:
    """The module, or a package above it, is imported normally somewhere
    (PyInstaller then bundles the package with what its __init__ loads)."""
    parts = module.split(".")
    names = [".".join(parts[:i]) for i in range(len(parts), 0, -1)]
    alt = "|".join(map(re.escape, names))
    pattern = re.compile(rf"^\s*(?:import (?:{alt})\b|from (?:{alt})(?:\.[\w.]+)? import )", re.M)
    return any(pattern.search(text) for text in sources.values())


def test_every_indirectly_loaded_module_is_bundled_or_excluded():
    script = (ROOT / "desktop" / "build-sidecar.sh").read_text(encoding="utf-8")
    hidden = set(re.findall(r'--hidden-import "([\w.]+)"', script))
    excluded = set(re.findall(r'--exclude-module "([\w.]+)"', script))
    sources = _sources()
    indirect = {m for text in sources.values() for m in INDIRECT.findall(text)}
    assert "scipy.cluster.hierarchy" in indirect, "the scan found the known indirect import"
    missing = sorted(
        m
        for m in indirect
        if m not in hidden
        and not any(m == x or m.startswith(x + ".") for x in excluded)
        and not _imported_normally(m, sources)
    )
    assert not missing, f"add --hidden-import for {missing} in desktop/build-sidecar.sh"


def test_every_hidden_import_is_checked_before_the_build():
    script = (ROOT / "desktop" / "build-sidecar.sh").read_text(encoding="utf-8")
    for module in re.findall(r'--hidden-import "(scipy[\w.]*)"', script):
        assert f'import {module}"' in script, f"the build must fail without {module}"

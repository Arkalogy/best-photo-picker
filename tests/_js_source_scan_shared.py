"""Shared fixtures and helpers of the test_js_source_scan.py suite (split for the 500-LOC cap)."""

from __future__ import annotations

import re
from pathlib import Path

JS_DIR = Path(__file__).resolve().parent.parent / "bpp" / "web" / "static" / "js"

CSS_DIR = Path(__file__).resolve().parent.parent / "bpp" / "web" / "static" / "css"

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "bpp" / "web" / "templates"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _all_js_content() -> dict[str, str]:
    """Return {filename: content} for all classic JS files."""
    return {p.name: _read(p) for p in sorted(JS_DIR.glob("*.js"))}


def _all_module_content() -> dict[str, str]:
    """Return {filename: content} for ES modules under modules/."""
    modules_dir = JS_DIR / "modules"
    if not modules_dir.is_dir():
        return {}
    return {p.name: _read(p) for p in sorted(modules_dir.glob("*.mjs"))}


def _modules_blob() -> str:
    """Concatenate every ES module under modules/ into one searchable string.

    Used by source-scan tests that look for a symbol defined "somewhere in
    the people / lightbox / faces JS surface" — the v0.1 cleanup sharded
    monoliths like people.mjs into people.mjs + people-review.mjs +
    people-merge.mjs + people-view.mjs + people-pair-review.mjs etc., so a
    single-file read no longer finds symbols that moved to a sibling.
    """
    return "\n".join(_all_module_content().values())


def _all_js_function_defs() -> set[str]:
    """Extract all function names defined across classic JS files AND
    all names exported from ES modules under modules/. The exported
    names are bridged onto window via index.html's module bootstrap,
    so they're effectively globals to the script-tag callers.

    Matches in classic .js:
      function NAME(, async function NAME(, NAME = function(,
      NAME = async function(, const/let/var NAME = (...) =>,
      and also plain variable declarations (let NAME = null).

    Matches in modules/*.mjs:
      export function NAME, export async function NAME,
      export const|let|var NAME, export { A, B as C }.
    """
    defs = set()
    func_def_re = re.compile(
        r"(?:async\s+)?function\s+(\w+)\s*\(|"  # function NAME( / async function NAME(
        r"(?:const|let|var)\s+(\w+)\s*=",  # const/let/var NAME = (anything)
    )
    for content in _all_js_content().values():
        for m in func_def_re.finditer(content):
            name = m.group(1) or m.group(2)
            if name:
                defs.add(name)

    # ES module exports — bridged onto window via the index.html
    # `<script type="module">` block, so visible to classic callers.
    export_fn_re = re.compile(r"export\s+(?:async\s+)?function\s+(\w+)")
    export_var_re = re.compile(r"export\s+(?:const|let|var)\s+(\w+)")
    export_list_re = re.compile(r"export\s*\{([^}]+)\}")
    for content in _all_module_content().values():
        for m in export_fn_re.finditer(content):
            defs.add(m.group(1))
        for m in export_var_re.finditer(content):
            defs.add(m.group(1))
        for m in export_list_re.finditer(content):
            for part in m.group(1).split(","):
                ident = part.strip().split(" as ")[-1].strip()
                if ident and re.fullmatch(r"[a-zA-Z_$][\w$]*", ident):
                    defs.add(ident)
    return defs

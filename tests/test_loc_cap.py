"""500-LOC cap gate (project file-size rules).

The cap was previously enforced only by review attention, and files
crossed it three times in one week. This gate makes it mechanical:

- Any source file NOT in GRANDFATHERED must be <= 500 lines.
- GRANDFATHERED files (over cap when the gate landed, 2026-06-12) are
  ratcheted: they may never GROW past their recorded baseline, and the
  moment one is split below the cap it must be REMOVED from the list
  (shrink-only — a file that left can't come back).

When this test fails because you grew a file, the fix is to split the
file, not to bump the baseline.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAP = 500

# Baselines measured 2026-06-12 (the 7-agent review's scale gate).
# Shrink-only: lower a number when a split lands; never raise one.
GRANDFATHERED: dict[str, int] = {
    "bpp/db/schema.py": 530,
    "bpp/db/photos.py": 507,
    "bpp/web/static/js/modules/albums-render.mjs": 505,
}


def _loc(path: Path) -> int:
    return len(path.read_text(encoding="utf-8", errors="replace").splitlines())


def _source_files() -> list[Path]:
    files = [p for p in (ROOT / "bpp").rglob("*.py") if "__pycache__" not in p.parts]
    js_root = ROOT / "bpp" / "web" / "static" / "js"
    files += list(js_root.rglob("*.mjs"))
    files += list(js_root.rglob("*.js"))
    return files


def test_no_source_file_over_cap():
    """Every non-grandfathered source file stays at or under 500 LOC."""
    over = []
    for p in _source_files():
        rel = p.relative_to(ROOT).as_posix()
        if rel in GRANDFATHERED:
            continue
        loc = _loc(p)
        if loc > CAP:
            over.append(f"{rel}: {loc} LOC")
    assert not over, (
        "Files over the 500-LOC cap (split them — do not add to GRANDFATHERED):\n  "
        + "\n  ".join(over)
    )


def test_grandfathered_files_only_shrink():
    """Grandfathered files may not grow; once under cap they leave the list."""
    problems = []
    for rel, baseline in GRANDFATHERED.items():
        p = ROOT / rel
        if not p.exists():
            problems.append(f"{rel}: deleted — remove its GRANDFATHERED entry")
            continue
        loc = _loc(p)
        if loc > baseline:
            problems.append(
                f"{rel}: grew {baseline} -> {loc} LOC — split it; baselines never go up"
            )
        elif loc <= CAP:
            problems.append(f"{rel}: now {loc} LOC (under cap!) — delete its GRANDFATHERED entry")
    assert not problems, "\n  ".join(["Grandfather ratchet violations:", *problems])

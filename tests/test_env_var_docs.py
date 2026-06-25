r"""Regression: every `BPP_*` env var read by source has a docs entry.

Why this test exists: env vars are configuration knobs that operators
need to know exist before they can use them. They're also security-
relevant — `BPP_TRUSTED_PROXIES` controls who gets promoted to
"loopback" for LAN-gate purposes, and a misconfig is a privilege-
escalation primitive. A new contributor adding `BPP_FOO_BAR` and
forgetting to document it would ship an undiscoverable knob; this
test catches that at PR time.

Strategy: walk the source tree, extract every literal that looks
like `"BPP_..."` from `os.environ.get(...)` and `os.getenv(...)`
calls, and assert each one has a `### \`BPP_FOO\`` heading in
docs/configuration.md. The exact heading shape is what link-fragments
in the rendered docs key off, so we lock that too.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BPP_ROOT = REPO_ROOT / "bpp"
CONFIG_DOCS = REPO_ROOT / "docs" / "configuration.md"

# Match `os.environ.get("BPP_XYZ"...)` and `os.getenv("BPP_XYZ"...)` —
# we want the literal string passed in, not constants assigned to
# vars and read via the var. share.py uses the constant pattern too,
# so we add a second regex that matches a `_FOO = "BPP_XXX"` constant
# and trust the test to verify the constant has a doc entry.
_ENV_READ_RE = re.compile(r"""os\.(?:environ\.get|getenv)\(\s*["']?(BPP_[A-Z_]+)["']?\s*[,)]""")
_CONST_DEFINE_RE = re.compile(
    r"""^[A-Z_]+\s*=\s*["'](BPP_[A-Z_]+)["']""",
    re.MULTILINE,
)


def _all_bpp_env_vars_in_source() -> set[str]:
    """Walk bpp/ and return the set of BPP_* names actually read."""
    names: set[str] = set()
    for py in BPP_ROOT.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        names.update(_ENV_READ_RE.findall(text))

        # Constants like `_TRUSTED_PROXIES_ENV = "BPP_TRUSTED_PROXIES"`
        # are read via the constant name, but the *literal* is the
        # source of truth for what env var operators actually set.
        for m in _CONST_DEFINE_RE.finditer(text):
            names.add(m.group(1))

    # Excluded: pure docstring mentions like `BPP_DB_DIALECT` in
    # bpp/db/dialect.py, which is a future-hook example, not a
    # variable that's actually read. The regex above already only
    # matches real reads / literals.
    return names


def _documented_bpp_vars() -> set[str]:
    r"""Parse docs/configuration.md for `### \`BPP_FOO\`` headings."""
    text = CONFIG_DOCS.read_text(encoding="utf-8")
    pattern = re.compile(r"^##\s+`(BPP_[A-Z_]+)`", re.MULTILINE)
    return set(pattern.findall(text))


def test_every_source_bpp_var_is_documented():
    r"""Every BPP_* env var the code actually reads must appear as a
    `### \`BPP_FOO\`` heading in docs/configuration.md → "Configuration"."""
    in_source = _all_bpp_env_vars_in_source()
    documented = _documented_bpp_vars()
    missing = in_source - documented
    assert not missing, (
        f"Found {len(missing)} BPP_* env var(s) read by source but not "
        f"documented in docs/configuration.md → 'Configuration: environment "
        f"variables':\n"
        + "\n".join(f"  - {n}" for n in sorted(missing))
        + "\n\nAdd a `### `"
        + next(iter(missing))
        + "`` heading with: "
        "default, type, what it does, and any safety/security implications."
    )


def test_no_stale_docs_entries_for_removed_vars():
    """Inverse: every documented BPP_* must still be read by source.
    Catches docs that were left behind when a var was removed."""
    in_source = _all_bpp_env_vars_in_source()
    documented = _documented_bpp_vars()
    stale = documented - in_source
    assert not stale, (
        f"docs/configuration.md documents {len(stale)} BPP_* env var(s) that "
        "the source no longer reads (or never read):\n"
        + "\n".join(f"  - {n}" for n in sorted(stale))
        + "\n\nEither remove the doc entry or restore the read site."
    )


def test_at_least_the_known_bpp_vars_are_present():
    """Sanity: a baseline set of BPP_* vars is always present. If this
    fails, either the regex broke or someone removed an env var without
    updating this floor."""
    in_source = _all_bpp_env_vars_in_source()
    expected_floor = {
        "BPP_CACHE_DIR",
        "BPP_MODELS_DIR",
        "BPP_TRUSTED_PROXIES",
        "BPP_TRUST_PROXY",
    }
    missing = expected_floor - in_source
    assert not missing, (
        "Expected to find these BPP_* vars in source, but didn't:\n"
        + "\n".join(f"  - {n}" for n in sorted(missing))
        + "\n\nIf one of these was intentionally removed, drop it from this "
        "test's expected_floor set in the same commit."
    )

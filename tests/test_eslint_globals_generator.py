"""Regression guard for scripts/generate-eslint-globals.mjs.

The ESLint globals allowlist is auto-generated from every top-level
``function`` / ``let`` / ``const`` / ``var`` in ``bpp/web/static/js/``.
If the generator silently starts producing zero (or dramatically fewer)
names, our ``no-undef`` rule turns from a safety net into noise.

These tests invoke the generator directly and sanity-check the output.
They run under the normal Python pytest harness so CI catches drift
regardless of whether ``npm run lint`` is executed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GENERATOR = REPO_ROOT / "scripts" / "generate-eslint-globals.mjs"
OUTPUT = REPO_ROOT / ".eslint-globals.json"


@pytest.fixture(scope="module")
def node_available() -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed in this environment")
    return node


def test_generator_script_exists():
    assert GENERATOR.exists(), f"missing {GENERATOR}"


def test_committed_globals_file_exists():
    assert OUTPUT.exists(), (
        ".eslint-globals.json should be committed — CI regenerates and "
        "verifies it's in sync via `git diff --exit-code`"
    )


def test_committed_globals_contains_many_names():
    """Sanity cap — if this drops below 500, either the generator regressed
    or a huge chunk of JS was removed. Either way, investigate."""
    data = json.loads(OUTPUT.read_text())
    assert len(data) >= 500, (
        f"globals allowlist has only {len(data)} entries (expected >= 500) "
        "— did the generator regex break?"
    )


def test_generator_produces_same_output_when_rerun(node_available, tmp_path):
    """Running the generator must produce the same bytes we've committed.

    This is the same invariant CI enforces via `git diff --exit-code`.
    Running it in Python too means developers catch drift locally via
    `pytest -k globals` without needing the npm tooling installed.
    """
    # Write regenerated output to a temp file by piping the generator
    # with a rewritten OUT path — simplest: capture current file, run
    # generator (which rewrites the committed file), diff, then restore.
    before = OUTPUT.read_bytes()
    result = subprocess.run(
        [node_available, str(GENERATOR)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    after = OUTPUT.read_bytes()

    # Restore on any outcome so we don't leave uncommitted noise
    if before != after:
        OUTPUT.write_bytes(before)

    assert result.returncode == 0, f"generator crashed: {result.stderr}"
    assert before == after, (
        "Regenerating .eslint-globals.json produced different output "
        "than what's committed. Run `npm run lint` and commit the diff."
    )


def test_known_function_names_present():
    """Sanity: a handful of well-known helpers must be in the allowlist.
    If these ever disappear, something is very wrong with the generator."""
    data = json.loads(OUTPUT.read_text())
    for name in [
        "toast",  # utils.js
        "appConfirm",  # utils.js
        "esc",  # utils.js
        "apiFetch",  # utils.js
        "showPeopleView",  # people.js
        "loadFaceClusters",  # faces.js
        "startFacePairReview",  # people.js — the new feature
        "find_ambiguous_pairs" if False else "findAmbiguousPairs",
    ]:
        # findAmbiguousPairs is Python-side; check the real JS name
        if name == "findAmbiguousPairs":
            continue
        assert name in data, f"{name!r} missing from .eslint-globals.json"

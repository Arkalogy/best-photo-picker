"""P6 — mypy strict-mode gate on the typed scope.

The TypedDict contract is only as good as the static checker that
enforces it. This gate runs mypy in strict mode against the modules
listed under ``[[tool.mypy.overrides]]`` in pyproject.toml. A
regression that introduces an untyped def or a raw-dict construction
with the PhotoDict key shape fails here before it ships.

The gate is intentionally narrow — adding more modules to the
override list is the migration path. Today the scope is
``bpp.web.photo_dict`` (the TypedDict + the two builders). Each
endpoint added to the override list as its PhotoDict consumption
gets typed will extend the gate.

Skipped on CI when mypy isn't installed (it's a dev dep, not a
runtime dep) — locally bpp's dev environment always has it.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(
    shutil.which("mypy") is None and not (REPO_ROOT / ".venv" / "bin" / "mypy").exists(),
    reason="mypy not installed",
)
def test_mypy_strict_scope_passes():
    """``mypy bpp/web/photo_dict.py`` must exit 0.

    Runs in the same process via subprocess so mypy picks up
    pyproject.toml's ``[[tool.mypy.overrides]]`` section. Any
    mypy error in the strict-scope modules fails this test.
    """
    # Prefer the venv mypy so we hit the exact pinned version.
    # ``--no-incremental`` avoids ``.mypy_cache`` lock contention when
    # CI runs this test under pytest-xdist with multiple workers; the
    # cache is process-local without the flag and shared state races
    # produce spurious "no such cached file" failures.
    mypy = REPO_ROOT / ".venv" / "bin" / "mypy"
    cmd = [
        str(mypy) if mypy.exists() else "mypy",
        "--no-incremental",
        "bpp/web/photo_dict.py",
    ]
    result = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"mypy strict-scope gate failed.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_photo_dict_is_typed_dict():
    """Smoke check: PhotoDict is a TypedDict (not just a dict alias).

    This catches a regression where a future refactor accidentally
    converts the TypedDict back into a plain dict[str, Any] — the
    strict-mode gate above would still pass, but the contract would
    silently weaken."""
    import typing

    from bpp.web.photo_dict import PhotoDict, PhotoMapDict

    # TypedDict subclasses are detected via __required_keys__ +
    # __optional_keys__ on Python 3.9+.
    assert hasattr(PhotoDict, "__annotations__")
    assert hasattr(PhotoDict, "__optional_keys__"), (
        "PhotoDict must be a TypedDict — got plain class"
    )
    # PhotoMapDict is total=True so __required_keys__ should be non-empty.
    assert PhotoMapDict.__required_keys__, "PhotoMapDict must be total=True with required keys"
    # PhotoDict is total=False so every key is optional.
    assert not PhotoDict.__required_keys__, (
        "PhotoDict must be total=False — all keys optional per contract"
    )
    # typing.get_type_hints must work on both.
    assert typing.get_type_hints(PhotoDict), "PhotoDict has no resolvable hints"
    assert typing.get_type_hints(PhotoMapDict), "PhotoMapDict has no resolvable hints"

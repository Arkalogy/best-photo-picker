"""The repository's tracked files, for guards that scan them.

One ``git ls-files`` per test session (the list is cached): a guard that
spawned git at collection time and another at test time each did their
own.
"""

from __future__ import annotations

import functools
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]


@functools.cache
def tracked_files() -> tuple[str, ...]:
    """Repo-relative paths git tracks. Raises ``OSError`` /
    ``CalledProcessError`` when this isn't a git checkout: callers skip."""
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    return tuple(p for p in out.split("\n") if p)

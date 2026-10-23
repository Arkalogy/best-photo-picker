"""Shared fixtures and helpers of the test_packaging.py suite (split for the 500-LOC cap)."""

from __future__ import annotations

# tomllib is part of the stdlib from Python 3.11 onward; pyproject pins
# `requires-python = ">=3.11"` so no version guard needed here.
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# The distribution name we publish under. Different from the *import*
# package name (`bpp`) — these are independent by design.
EXPECTED_DIST_NAME = "bppicker"


def _read_pyproject() -> dict:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())


def _read_publish_workflow() -> str:
    return (REPO_ROOT / ".github" / "workflows" / "publish.yml").read_text()


def _read_docker_workflow() -> str:
    return (REPO_ROOT / ".github" / "workflows" / "docker.yml").read_text()


def _read_dependabot() -> str:
    return (REPO_ROOT / ".github" / "dependabot.yml").read_text()

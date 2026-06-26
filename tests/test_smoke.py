"""Smoke tests for CLI entrypoint."""

from __future__ import annotations

import subprocess
import sys


def test_cli_help():
    """bpp --help should exit 0."""
    result = subprocess.run(
        [sys.executable, "-m", "bpp.cli", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "bpp" in result.stdout


def test_cli_version():
    """bpp --version should print version."""
    result = subprocess.run(
        [sys.executable, "-m", "bpp.cli", "--version"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "0.1.0" in result.stdout


def test_cli_no_command():
    """bpp with no subcommand should exit 1."""
    result = subprocess.run(
        [sys.executable, "-m", "bpp.cli"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1


def test_analyze_missing_input(tmp_path):
    """bpp analyze with nonexistent input should exit 1."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "bpp.cli",
            "analyze",
            "--input",
            str(tmp_path / "nonexistent"),
            "--out",
            str(tmp_path / "workdir"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1

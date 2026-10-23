"""Shared fixtures and helpers of the test_download_chokepoint.py suite (split for the 500-LOC
cap)."""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from bpp.registry.download_chokepoint import (
    _reset_chokepoint_for_tests,
    install_third_party_interceptions,
)


@pytest.fixture(autouse=True)
def _reset_chokepoint() -> Any:
    """Clean chokepoint state between tests so test order does not
    leak patches across assertions.

    Teardown re-installs the production interceptions so subsequent
    test files that go through the face orchestrator (which calls
    :func:`enforce_chokepoint` at the top of every run) don't trip
    over an empty :data:`_patched` set. Without the re-install, the
    orchestrator-driven test files (test_face_extraction_journal,
    test_clustering_stability, etc.) all blew up with
    ``BlockedAutoDownloadError`` after this file ran.
    """
    _reset_chokepoint_for_tests()
    yield
    _reset_chokepoint_for_tests()
    install_third_party_interceptions()


# ── Synthetic third-party module helper ──


def _install_fake_thirdparty(submodule: str, function_name: str) -> tuple[types.ModuleType, Any]:
    """Create a fake third-party module that exposes a callable named
    ``function_name`` and install it under ``sys.modules[submodule]``.

    Returns ``(module, original_callable)`` so the test can later
    invoke the original to confirm it survives patch + restore.
    """

    def original(*args: Any, **kwargs: Any) -> str:
        return f"called with args={args} kwargs={kwargs}"

    module = types.ModuleType(submodule)
    setattr(module, function_name, original)
    sys.modules[submodule] = module
    return module, original


def _uninstall_fake_thirdparty(submodule: str) -> None:
    """Remove a fake third-party module from sys.modules."""
    sys.modules.pop(submodule, None)


# ── Real-world insightface integration (run when insightface is installed) ──


def _insightface_available() -> bool:
    """Return ``True`` if the real ``insightface`` package can be
    imported in this environment.

    The chokepoint test suite primarily uses synthetic modules so the
    suite stays portable, but the synthetic coverage cannot catch the
    one regression we care most about: an upstream release renaming
    ``download`` to ``download_file_v2`` (or similar) and silently
    bypassing :data:`KNOWN_AUTO_DOWNLOADERS`. The real-import test
    below catches that the moment it lands."""
    try:
        import importlib.util

        return importlib.util.find_spec("insightface") is not None
    except (ImportError, ValueError):
        return False

"""The runtime-reset helpers (make an accept / withdrawal take effect
without a restart; drop one model file's cached state) live with the
model lifecycle code, not inside a web endpoint file — so every endpoint
reaches them the same way and tests patch one place.
"""

from __future__ import annotations

import pytest

from bpp.scoring import model_lifecycle
from bpp.scoring.model_base import ModelEntry, ModelRegistry
from bpp.scoring.model_lifecycle import reset_model_file, reset_runtime_for_legal_id

FAKE_LEGAL_ID = "test_runtime_reset_legal_id"


@pytest.fixture
def throwaway_files(tmp_path):
    """Two registry files declaring FAKE_LEGAL_ID: one whose reset hook
    records the call, one whose reset hook raises. Popped afterwards."""
    calls: list[str] = []

    def _boom() -> None:
        raise RuntimeError("reset hook broke")

    for name, reset in (("fake-fine", lambda: calls.append("fake-fine")), ("fake-broken", _boom)):
        ModelRegistry.register(
            ModelEntry(
                name=name,
                path=str(tmp_path / f"{name}.onnx"),
                url=f"https://models.example.org/{name}.onnx",
                sha256="0" * 64,
                reset=reset,
                legal_id=FAKE_LEGAL_ID,
            ),
            replace=True,
            builtin=False,
        )
    yield calls
    for name in ("fake-fine", "fake-broken"):
        ModelRegistry.unregister_for_tests(name)


def test_unknown_model_file_is_ignored() -> None:
    reset_model_file("definitely not a model")  # no raise


def test_a_known_file_runs_its_reset_hook(throwaway_files) -> None:
    reset_model_file("fake-fine")
    assert throwaway_files == ["fake-fine"]


def test_a_failing_reset_hook_is_logged_not_raised(throwaway_files, caplog) -> None:
    # The uninstall / redownload that led here already happened on disk.
    reset_model_file("fake-broken")
    assert "Reset hook failed for fake-broken" in caplog.text


def test_unknown_legal_id_resets_nothing() -> None:
    assert reset_runtime_for_legal_id("no_such_legal_id") == []


def test_every_file_declaring_the_legal_id_is_reset(throwaway_files) -> None:
    names = reset_runtime_for_legal_id(FAKE_LEGAL_ID)
    assert throwaway_files == ["fake-fine"]
    # A hook that raises is logged, not fatal: the accept / withdrawal
    # that triggered the reset still succeeds, and the others still reset.
    assert names == ["fake-fine"]


def test_the_endpoint_file_reaches_the_helpers_through_the_module() -> None:
    # So patching ``bpp.scoring.model_lifecycle.reset_model_file`` reaches
    # its callers (the other endpoints import the module the same way).
    from bpp.web import bp_models

    assert bp_models.model_lifecycle is model_lifecycle

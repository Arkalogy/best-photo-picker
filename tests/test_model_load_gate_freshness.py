"""A memoized license pass lasts only while the acceptance log and the
use-context declaration are unchanged.

Before: a pass was kept for the life of the process, so switching to
"Commercial" (which never re-armed the gates) left research-only models
loading, and a withdrawal didn't reach an analysis subprocess that had
already passed its gates.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from bpp.registry import (
    ModelLoadBlockedError,
    UseContext,
    confirm_acceptance,
    get_entry,
    prepare_acceptance,
    set_use_context,
    utc_now_iso,
)
from bpp.registry.acceptance_log import append_revocation
from bpp.scoring.model_load_gate import MemoizedLoadGate

pytestmark = pytest.mark.no_preaccept_permissive

_SCRFD = "insightface_scrfd_25g"


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("BPP_ACCEPTANCE_LOG_PATH", str(tmp_path / "acceptance.jsonl"))
    monkeypatch.setenv("BPP_USE_CONTEXT_PATH", str(tmp_path / "use-context.json"))
    set_use_context(UseContext.PERSONAL, set_via="test")
    draft = prepare_acceptance(get_entry(_SCRFD), use_context=UseContext.PERSONAL)
    confirm_acceptance(
        draft,
        checkbox_responses={cb: True for cb, _ in draft.required_checkboxes},
        accepted_at=utc_now_iso(),
    )


def test_switching_to_commercial_blocks_a_passed_research_model(isolated):
    gate = MemoizedLoadGate(_SCRFD)
    gate.enforce()
    set_use_context(UseContext.COMMERCIAL, set_via="test")
    with pytest.raises(ModelLoadBlockedError):
        gate.enforce()


def test_withdrawal_reaches_a_gate_nobody_reset(isolated):
    """An analysis subprocess never sees the server's reset call — the
    file change alone must re-arm its gate."""
    gate = MemoizedLoadGate(_SCRFD)
    gate.enforce()
    append_revocation(_SCRFD)
    with pytest.raises(ModelLoadBlockedError):
        gate.enforce()


def test_unchanged_files_do_not_recheck(isolated):
    gate = MemoizedLoadGate(_SCRFD)
    gate.enforce()
    with patch("bpp.registry.enforce_load_policy_for") as check:
        for _ in range(50):
            gate.enforce()
    check.assert_not_called()

"""An acceptance's answers must be real true/false values: ``bool("false")``
is True, so coercing would tick a required box or assert commercial rights
the user never asserted. Non-booleans are rejected and nothing is logged."""

from __future__ import annotations

from pathlib import Path

import pytest

from bpp.registry.acceptance import AcceptanceError, confirm_acceptance, prepare_acceptance
from bpp.registry.acceptance_log import iter_rows
from bpp.registry.use_context import REQUIRED_ACK_CHECKBOXES, UseContext
from tests.registry.test_acceptance import _make_restricted_entry


@pytest.fixture(autouse=True)
def _isolated_log(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BPP_ACCEPTANCE_LOG_PATH", str(tmp_path / "model-acceptance.jsonl"))


@pytest.mark.parametrize(
    "over",
    [{"checkbox": "false"}, {"checkbox": 1}, {"rights": "false"}, {"rights": 0}],
)
def test_a_non_boolean_answer_is_rejected_not_coerced(over):
    draft = prepare_acceptance(_make_restricted_entry(), use_context=UseContext.PERSONAL)
    checks: dict = {cb_id: True for cb_id, _ in REQUIRED_ACK_CHECKBOXES}
    if "checkbox" in over:
        checks[next(iter(checks))] = over["checkbox"]
    with pytest.raises(AcceptanceError, match="true or false"):
        confirm_acceptance(
            draft,
            checkbox_responses=checks,
            accepted_at="2026-06-02T12:34:56+00:00",
            separate_rights_asserted=over.get("rights", False),
        )
    assert list(iter_rows()) == []

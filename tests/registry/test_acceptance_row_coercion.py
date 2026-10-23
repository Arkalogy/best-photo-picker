"""Rows an earlier build wrote with a looser JSON type are still read:
an int-like string schema version and 0/1 checkbox answers mean the
same thing. Anything that could widen rights (a string "false") is
still refused."""

from __future__ import annotations

import pytest

from bpp.registry.acceptance_log import AcceptanceRow


def _raw(**over):
    raw = {
        "model_id": "x",
        "model_sha256": "0" * 64,
        "ack_text_version": "v1",
        "ack_text_sha256": "1" * 64,
        "use_context_text_version": "v1",
        "use_context_text_sha256": "2" * 64,
        "use_context_at_acceptance": "personal",
        "separate_rights_asserted": False,
        "terms_url": "https://example.invalid/x",
        "terms_permalink_url": "",
        "terms_retrieved_at": "2026-06-02",
        "accepted_at": "2026-06-02T12:34:56+00:00",
        "checkbox_responses": {"a": True},
        "schema_version": 2,
    }
    raw.update(over)
    return raw


def test_int_like_schema_version_and_binary_checkboxes_are_read():
    row = AcceptanceRow.from_dict(_raw(schema_version="2", checkbox_responses={"a": 1, "b": 0}))
    assert row.schema_version == 2
    assert row.checkbox_responses == {"a": True, "b": False}


@pytest.mark.parametrize(
    "over",
    [
        {"checkbox_responses": {"a": "false"}},
        {"checkbox_responses": {"a": 2}},
        {"schema_version": "two"},
        {"schema_version": "00"},
        {"schema_version": "\u0662"},
        {"separate_rights_asserted": 1},
    ],
)
def test_rights_widening_or_unreadable_types_are_still_refused(over):
    with pytest.raises(TypeError):
        AcceptanceRow.from_dict(_raw(**over))

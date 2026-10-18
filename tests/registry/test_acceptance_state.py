"""One judgement of a model's license acceptance — ``acceptance_state`` —
and the three places that used to each decide it on their own (the load
gate, the acceptance list, the picker's license state) now all agree with
it. A change to the rule is made once.
"""

from __future__ import annotations

import dataclasses

import pytest

from bpp.registry import (
    UseContext,
    confirm_acceptance,
    get_entry,
    prepare_acceptance,
    set_use_context,
    utc_now_iso,
)
from bpp.registry.acceptance import acceptance_state, is_acceptance_valid_for
from bpp.registry.acceptance_log import (
    append_revocation,
    append_row,
    find_latest_for_model,
)

pytestmark = pytest.mark.no_preaccept_permissive

RESTRICTED = "nudenet_320n"
PERMISSIVE = "openai_clip_vit_b32_onnx"


def _accept(model_id: str, *, rights: bool = False) -> None:
    set_use_context(UseContext.PERSONAL, set_via="test")
    entry = get_entry(model_id)
    assert entry is not None
    draft = prepare_acceptance(entry, use_context=UseContext.PERSONAL)
    confirm_acceptance(
        draft,
        checkbox_responses={cb: True for cb, _ in draft.required_checkboxes},
        accepted_at=utc_now_iso(),
        separate_rights_asserted=rights,
        source_of_rights_note="",
    )


def _make_stale(model_id: str) -> None:
    latest = find_latest_for_model(model_id)
    assert latest is not None
    append_row(dataclasses.replace(latest, ack_text_sha256="0" * 64, ack_text_version="old"))


class TestAcceptanceState:
    def test_never_accepted(self) -> None:
        st = acceptance_state(get_entry(RESTRICTED))
        assert (st.accepted, st.current, st.needs_reaccept) == (False, False, False)
        assert st.latest is None

    def test_permissive_needs_nothing(self) -> None:
        assert acceptance_state(get_entry(PERMISSIVE)).current is True

    def test_permissive_with_a_stray_withdrawal_row_still_loads(self) -> None:
        # The gate never read the log for such an entry; neither does this.
        from bpp.registry.policy import ModelLoadDecision, check_model_load_allowed
        from bpp.web.model_filter import license_state

        _accept(PERMISSIVE)
        append_revocation(PERMISSIVE)
        entry = get_entry(PERMISSIVE)
        assert acceptance_state(entry).current is True
        assert license_state(entry) == "ok"
        decision = check_model_load_allowed(entry, use_context=UseContext.PERSONAL).decision
        assert decision is ModelLoadDecision.ALLOW
        # The acceptance list, judging that row itself: a withdrawal, not current.
        st = acceptance_state(entry, row=find_latest_for_model(PERMISSIVE))
        assert (st.accepted, st.current, st.needs_reaccept) == (False, False, False)

    def test_current_acceptance(self) -> None:
        _accept(RESTRICTED)
        st = acceptance_state(get_entry(RESTRICTED))
        assert (st.accepted, st.current, st.needs_reaccept) == (True, True, False)

    def test_stale_acceptance(self) -> None:
        _accept(RESTRICTED)
        _make_stale(RESTRICTED)
        st = acceptance_state(get_entry(RESTRICTED))
        assert (st.accepted, st.current, st.needs_reaccept) == (True, False, True)

    def test_withdrawn(self) -> None:
        _accept(RESTRICTED)
        append_revocation(RESTRICTED)
        st = acceptance_state(get_entry(RESTRICTED))
        assert (st.accepted, st.current, st.needs_reaccept) == (False, False, False)

    def test_rights_assertion_rides_along(self) -> None:
        _accept(RESTRICTED, rights=True)
        assert acceptance_state(get_entry(RESTRICTED)).separate_rights_asserted is True

    def test_a_given_row_is_judged_instead_of_the_latest(self) -> None:
        _accept(RESTRICTED)
        first = find_latest_for_model(RESTRICTED)
        _make_stale(RESTRICTED)
        assert acceptance_state(get_entry(RESTRICTED), row=first).current is True
        assert acceptance_state(get_entry(RESTRICTED)).current is False

    def test_a_row_for_a_model_the_registry_forgot_is_never_current(self) -> None:
        _accept(RESTRICTED)
        row = find_latest_for_model(RESTRICTED)
        st = acceptance_state(None, row=row)
        assert (st.accepted, st.current, st.needs_reaccept) == (True, False, True)


class TestTheThreeCallersAgree:
    """The gate, the acceptance list and the picker read the same answer."""

    @pytest.mark.parametrize("setup", ["none", "current", "stale", "withdrawn"])
    def test_gate_list_and_picker_match_the_shared_state(self, setup: str) -> None:
        from bpp.web.bp_model_registry import _acceptance_freshness
        from bpp.web.model_filter import license_state

        if setup != "none":
            _accept(RESTRICTED)
        if setup == "stale":
            _make_stale(RESTRICTED)
        if setup == "withdrawn":
            append_revocation(RESTRICTED)
        entry = get_entry(RESTRICTED)
        st = acceptance_state(entry)

        assert is_acceptance_valid_for(entry) is st.current, setup
        expected = "ok" if st.current else "needs_reaccept" if st.accepted else "needs_license"
        assert license_state(entry) == expected, setup
        latest = find_latest_for_model(RESTRICTED)
        if latest is not None:
            assert _acceptance_freshness(latest, entry) == {
                "current": st.current,
                "needs_reaccept": st.needs_reaccept,
            }, setup

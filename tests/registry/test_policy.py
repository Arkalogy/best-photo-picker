"""Batch 5 policy + use-context-store tests.

Pins every rule in :func:`check_model_load_allowed`, the
file-backed use-context store, and the trap-T7 reclassification
lock. Together with the Batch-4 acceptance tests, these tests are
the spec for the legal-posture hard-block: what loads, what
doesn't, and why.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from bpp.registry import (
    REQUIRED_ACK_CHECKBOXES,
    LicenseClass,
    ModelEntry,
    ModelLoadBlockedError,
    ModelLoadDecision,
    ModelStatus,
    UseContext,
    assert_no_silent_reclassification,
    canonical_disclaimer_sha256,
    check_model_load_allowed,
    confirm_acceptance,
    get_use_context,
    has_been_set,
    prepare_acceptance,
    raise_if_blocked,
    read_record,
    set_use_context,
)
from bpp.registry.policy import _reset_reclassification_lock_for_tests


@pytest.fixture
def isolated_acceptance_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "model-acceptance.jsonl"
    monkeypatch.setenv("BPP_ACCEPTANCE_LOG_PATH", str(target))
    return target


@pytest.fixture
def isolated_use_context_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "use-context.json"
    monkeypatch.setenv("BPP_USE_CONTEXT_PATH", str(target))
    return target


@pytest.fixture(autouse=True)
def _reset_reclassification_lock() -> None:
    _reset_reclassification_lock_for_tests()


def _make_restricted_entry(entry_id: str = "test_restricted") -> ModelEntry:
    return ModelEntry(
        id=entry_id,
        display_name="Restricted Test Model v1",
        kind="face_embedder",
        source_url="https://example.invalid/r.onnx",
        terms_url="https://example.invalid/LICENSE",
        # Permalink required for restricted entries (item 19); see
        # TestPermalinkValidation in test_acceptance.py.
        terms_permalink_url="https://example.invalid/abc123/LICENSE",
        terms_retrieved_at="2026-06-02",
        license_summary="Test restricted entry — non-commercial only",
        requires_explicit_ack=True,
        ack_text_version="canonical-disclaimer-v1",
        ack_text_sha256=canonical_disclaimer_sha256(),
        upstream_claimed_license_class=LicenseClass.RESEARCH_NON_COMMERCIAL,
        commercial_use_restriction_known=True,
        bppicker_commercial_default_allowed=False,
        commercial_unlock_requires_rights_assertion=True,
        status=ModelStatus.AVAILABLE,
        training_data="synthetic",
        weight_sha256="a" * 64,
        default_for_kind=False,
    )


def _make_permissive_entry(entry_id: str = "test_permissive") -> ModelEntry:
    return ModelEntry(
        id=entry_id,
        display_name="Permissive Test Model",
        kind="face_embedder",
        source_url="https://example.invalid/p.onnx",
        terms_url="https://example.invalid/PERMISSIVE",
        terms_permalink_url=None,
        terms_retrieved_at="2026-06-02",
        license_summary="Test permissive entry",
        requires_explicit_ack=False,
        ack_text_version="canonical-disclaimer-v1",
        ack_text_sha256=canonical_disclaimer_sha256(),
        upstream_claimed_license_class=LicenseClass.APACHE_2_0,
        commercial_use_restriction_known=False,
        bppicker_commercial_default_allowed=True,
        commercial_unlock_requires_rights_assertion=False,
        status=ModelStatus.AVAILABLE,
        training_data="synthetic",
        weight_sha256="b" * 64,
        default_for_kind=False,
    )


def _record_acceptance(
    entry: ModelEntry,
    *,
    use_context: UseContext,
    separate_rights_asserted: bool = False,
) -> None:
    draft = prepare_acceptance(entry, use_context=use_context)
    confirm_acceptance(
        draft,
        checkbox_responses={cb_id: True for cb_id, _ in REQUIRED_ACK_CHECKBOXES},
        accepted_at="2026-06-02T12:34:56+00:00",
        separate_rights_asserted=separate_rights_asserted,
    )


# ── Permissive path ──


class TestPermissivePath:
    def test_permissive_always_allowed(self, isolated_acceptance_log: Path) -> None:
        entry = _make_permissive_entry()
        for ctx in (
            UseContext.PERSONAL,
            UseContext.RESEARCH,
            UseContext.COMMERCIAL,
            UseContext.UNSPECIFIED,
        ):
            result = check_model_load_allowed(entry, use_context=ctx)
            assert result.decision is ModelLoadDecision.ALLOW, (
                f"Permissive model blocked under use_context={ctx.value}: {result.reason}"
            )


# ── Restricted path ──


class TestRestrictedNeedsAck:
    def test_restricted_with_no_acceptance_blocks_needs_ack(
        self, isolated_acceptance_log: Path
    ) -> None:
        entry = _make_restricted_entry()
        result = check_model_load_allowed(entry, use_context=UseContext.PERSONAL)
        assert result.decision is ModelLoadDecision.BLOCKED_NEEDS_ACK
        assert entry.id in result.reason


class TestRestrictedWithAcceptanceUnderNonCommercial:
    @pytest.mark.parametrize(
        "ctx",
        [UseContext.PERSONAL, UseContext.RESEARCH, UseContext.UNSPECIFIED],
    )
    def test_acceptance_allows_non_commercial_use(
        self,
        ctx: UseContext,
        isolated_acceptance_log: Path,
    ) -> None:
        entry = _make_restricted_entry()
        _record_acceptance(entry, use_context=ctx)
        result = check_model_load_allowed(entry, use_context=ctx)
        assert result.decision is ModelLoadDecision.ALLOW

    def test_acceptance_invalidated_by_wording_change(self, isolated_acceptance_log: Path) -> None:
        entry = _make_restricted_entry()
        _record_acceptance(entry, use_context=UseContext.PERSONAL)
        # Now simulate a wording change.
        updated = replace(entry, ack_text_sha256="0" * 64)
        result = check_model_load_allowed(updated, use_context=UseContext.PERSONAL)
        assert result.decision is ModelLoadDecision.BLOCKED_NEEDS_ACK


class TestRestrictedCommercialMode:
    def test_commercial_without_rights_blocks(self, isolated_acceptance_log: Path) -> None:
        entry = _make_restricted_entry()
        _record_acceptance(
            entry,
            use_context=UseContext.PERSONAL,
            separate_rights_asserted=False,
        )
        result = check_model_load_allowed(entry, use_context=UseContext.COMMERCIAL)
        assert result.decision is ModelLoadDecision.BLOCKED_COMMERCIAL_NO_RIGHTS

    def test_commercial_with_rights_allows(self, isolated_acceptance_log: Path) -> None:
        entry = _make_restricted_entry()
        _record_acceptance(
            entry,
            use_context=UseContext.COMMERCIAL,
            separate_rights_asserted=True,
        )
        result = check_model_load_allowed(entry, use_context=UseContext.COMMERCIAL)
        assert result.decision is ModelLoadDecision.ALLOW


# ── Status overrides everything ──


class TestStatusOverrides:
    def test_legally_blocked_overrides_acceptance(self, isolated_acceptance_log: Path) -> None:
        entry = _make_restricted_entry()
        _record_acceptance(entry, use_context=UseContext.PERSONAL)
        blocked = replace(entry, status=ModelStatus.LEGALLY_BLOCKED)
        result = check_model_load_allowed(blocked, use_context=UseContext.PERSONAL)
        assert result.decision is ModelLoadDecision.BLOCKED_LEGAL

    def test_legally_blocked_blocks_permissive_too(self, isolated_acceptance_log: Path) -> None:
        """LEGALLY_BLOCKED is the strongest state — even permissive
        entries are blocked when upstream sends a takedown."""
        entry = _make_permissive_entry()
        blocked = replace(entry, status=ModelStatus.LEGALLY_BLOCKED)
        result = check_model_load_allowed(blocked, use_context=UseContext.PERSONAL)
        assert result.decision is ModelLoadDecision.BLOCKED_LEGAL

    def test_withdrawn_restricted_blocks_new_loads(self, isolated_acceptance_log: Path) -> None:
        entry = _make_restricted_entry()
        _record_acceptance(entry, use_context=UseContext.PERSONAL)
        withdrawn = replace(entry, status=ModelStatus.WITHDRAWN_NO_NEW_DOWNLOADS)
        result = check_model_load_allowed(withdrawn, use_context=UseContext.PERSONAL)
        assert result.decision is ModelLoadDecision.BLOCKED_WITHDRAWN

    def test_withdrawn_permissive_still_allowed(self, isolated_acceptance_log: Path) -> None:
        """A withdrawn permissive entry (typically an older
        version of a safe model) does not need re-prompting; we
        let the load proceed under the rule that permissive +
        non-LEGALLY_BLOCKED is always allowed."""
        entry = _make_permissive_entry()
        withdrawn = replace(entry, status=ModelStatus.WITHDRAWN_NO_NEW_DOWNLOADS)
        result = check_model_load_allowed(withdrawn, use_context=UseContext.PERSONAL)
        assert result.decision is ModelLoadDecision.ALLOW


# ── raise_if_blocked ──


class TestRaiseIfBlocked:
    def test_allow_does_not_raise(self, isolated_acceptance_log: Path) -> None:
        entry = _make_permissive_entry()
        raise_if_blocked(check_model_load_allowed(entry, use_context=UseContext.PERSONAL))

    def test_block_raises_with_decision_attached(self, isolated_acceptance_log: Path) -> None:
        entry = _make_restricted_entry()
        with pytest.raises(ModelLoadBlockedError) as excinfo:
            raise_if_blocked(check_model_load_allowed(entry, use_context=UseContext.PERSONAL))
        assert excinfo.value.result.decision is ModelLoadDecision.BLOCKED_NEEDS_ACK


# ── Use-context store ──


class TestUseContextStore:
    def test_default_is_unspecified(self, isolated_use_context_store: Path) -> None:
        assert get_use_context() is UseContext.UNSPECIFIED
        assert has_been_set() is False

    def test_set_then_get_round_trips(self, isolated_use_context_store: Path) -> None:
        set_use_context(UseContext.PERSONAL, set_via="test")
        assert get_use_context() is UseContext.PERSONAL
        assert has_been_set() is True

    def test_set_records_history(self, isolated_use_context_store: Path) -> None:
        set_use_context(UseContext.PERSONAL, set_via="test")
        set_use_context(UseContext.RESEARCH, set_via="test")
        set_use_context(UseContext.COMMERCIAL, set_via="test")
        record = read_record()
        assert record.use_context is UseContext.COMMERCIAL
        # History should hold the two prior declarations.
        assert len(record.history) == 2
        assert record.history[0]["use_context"] == "personal"
        assert record.history[1]["use_context"] == "research"

    def test_set_via_label_persists(self, isolated_use_context_store: Path) -> None:
        set_use_context(UseContext.PERSONAL, set_via="first-launch-gate")
        record = read_record()
        assert record.set_via == "first-launch-gate"

    def test_unparseable_file_treated_as_unspecified(
        self, isolated_use_context_store: Path
    ) -> None:
        isolated_use_context_store.parent.mkdir(parents=True, exist_ok=True)
        isolated_use_context_store.write_text("not-json")
        assert get_use_context() is UseContext.UNSPECIFIED


# ── Trap T7: reclassification lock ──


class TestReclassificationLock:
    def test_first_registration_records_restriction(self) -> None:
        entry = _make_restricted_entry()
        # First call records; second call is idempotent.
        assert_no_silent_reclassification(entry)
        assert_no_silent_reclassification(entry)

    def test_downgrade_after_restriction_raises(self) -> None:
        entry = _make_restricted_entry()
        assert_no_silent_reclassification(entry)
        downgraded = replace(entry, commercial_use_restriction_known=False)
        with pytest.raises(RuntimeError, match="Silent reclassification refused"):
            assert_no_silent_reclassification(downgraded)

    def test_permissive_unrelated_to_restricted_passes(self) -> None:
        """Locking an id only affects that id — registering a
        permissive entry with a different id is unaffected."""
        restricted = _make_restricted_entry()
        permissive_other = _make_permissive_entry(entry_id="other_perm")
        assert_no_silent_reclassification(restricted)
        # No raise.
        assert_no_silent_reclassification(permissive_other)

    def test_permissive_then_permissive_passes(self) -> None:
        permissive = _make_permissive_entry()
        # Re-registering a permissive entry as permissive is fine.
        assert_no_silent_reclassification(permissive)
        assert_no_silent_reclassification(permissive)

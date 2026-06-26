"""Batch 4 acceptance-flow tests.

These pin the contracts every consumer (HTML dialog, CLI prompt,
Flask endpoint, Batch-5 hard-block gate) relies on:

* The :class:`AcceptanceDraft` carries every string the dialog
  must render and every checkbox the user must answer.
* :func:`confirm_acceptance` enforces the legal-review-mandated
  four-checkbox gate (all required, all true) and persists every
  evidentiary field.
* :func:`is_acceptance_valid_for` returns ``True`` only when the
  user accepted the exact wording currently active. A wording
  change invalidates older acceptances and re-prompts.
* The acceptance-log file lives outside the library directory and
  honors ``BPP_ACCEPTANCE_LOG_PATH``.
* Trap T6: separate-rights assertion is per-model.
* Trap T5: commercial-use definition is rendered inside the
  acceptance flow.
* Item 13: biometric-responsibility text is part of the dialog
  payload.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from bpp.registry import (
    AcceptanceError,
    LicenseClass,
    ModelEntry,
    ModelStatus,
    UseContext,
    append_revocation,
    canonical_disclaimer_sha256,
    confirm_acceptance,
    find_latest_for_model,
    has_accepted,
    is_acceptance_valid_for,
    list_acceptances,
    prepare_acceptance,
    use_context_text_sha256,
    utc_now_iso,
)
from bpp.registry.acceptance_log import (
    SCHEMA_VERSION,
    AcceptanceRow,
    append_row,
    get_acceptance_log_path,
    iter_rows,
)
from bpp.registry.use_context import (
    BIOMETRIC_RESPONSIBILITY_TEXT,
    COMMERCIAL_USE_DEFINITION,
    REQUIRED_ACK_CHECKBOXES,
    USE_CONTEXT_TEXT_VERSION,
    required_checkbox_ids,
)


@pytest.fixture
def isolated_log_path(tmp_path: Path, monkeypatch) -> Path:
    """Force the acceptance log into a tempdir for every test using
    this fixture."""
    target = tmp_path / "model-acceptance.jsonl"
    monkeypatch.setenv("BPP_ACCEPTANCE_LOG_PATH", str(target))
    return target


def _make_restricted_entry(entry_id: str = "test_restricted") -> ModelEntry:
    return ModelEntry(
        id=entry_id,
        display_name="Restricted Test Model v1",
        kind="face_embedder",
        source_url="https://example.invalid/model.onnx",
        terms_url="https://example.invalid/LICENSE",
        # Restricted entries must have a permalink — see the
        # TestPermalinkValidation suite. Using a valid placeholder
        # so the rest of this fixture matches a real builtin.
        terms_permalink_url="https://example.invalid/abc123/LICENSE",
        terms_retrieved_at="2026-06-02",
        license_summary="Test restricted entry — non-commercial only",
        requires_explicit_ack=True,
        ack_text_version="canonical-disclaimer-v2",
        ack_text_sha256=canonical_disclaimer_sha256(),
        upstream_claimed_license_class=LicenseClass.RESEARCH_NON_COMMERCIAL,
        commercial_use_restriction_known=True,
        bppicker_commercial_default_allowed=False,
        commercial_unlock_requires_rights_assertion=True,
        status=ModelStatus.AVAILABLE,
        training_data="synthetic test corpus",
        weight_sha256="a" * 64,
        default_for_kind=False,
    )


def _make_permissive_entry(entry_id: str = "test_permissive") -> ModelEntry:
    return ModelEntry(
        id=entry_id,
        display_name="Permissive Test Model",
        kind="face_embedder",
        source_url="https://example.invalid/permissive.onnx",
        terms_url="https://example.invalid/PERMISSIVE",
        terms_permalink_url=None,
        terms_retrieved_at="2026-06-02",
        license_summary="Test permissive entry",
        requires_explicit_ack=False,
        ack_text_version="canonical-disclaimer-v2",
        ack_text_sha256=canonical_disclaimer_sha256(),
        upstream_claimed_license_class=LicenseClass.APACHE_2_0,
        commercial_use_restriction_known=False,
        bppicker_commercial_default_allowed=True,
        commercial_unlock_requires_rights_assertion=False,
        status=ModelStatus.AVAILABLE,
        training_data="synthetic test corpus",
        weight_sha256="b" * 64,
        default_for_kind=False,
    )


# ── AcceptanceDraft shape ──


class TestAcceptanceDraft:
    """The dialog payload carries every legally-required text. Trap
    T5 (commercial-use definition), item 13 (biometric
    responsibility), and trap T6 (per-model rights assertion) all
    appear on the draft."""

    def test_draft_contains_required_checkboxes(self) -> None:
        entry = _make_restricted_entry()
        draft = prepare_acceptance(entry, use_context=UseContext.PERSONAL)
        ids = {cb_id for cb_id, _ in draft.required_checkboxes}
        assert ids == required_checkbox_ids()
        assert len(draft.required_checkboxes) == 4

    def test_draft_carries_commercial_use_definition(self) -> None:
        draft = prepare_acceptance(_make_restricted_entry(), use_context=UseContext.PERSONAL)
        assert draft.commercial_use_definition == COMMERCIAL_USE_DEFINITION
        assert "paid work" in draft.commercial_use_definition
        assert "commercial activity" in draft.commercial_use_definition

    def test_draft_carries_biometric_responsibility_text(self) -> None:
        draft = prepare_acceptance(_make_restricted_entry(), use_context=UseContext.PERSONAL)
        assert draft.biometric_responsibility_text == BIOMETRIC_RESPONSIBILITY_TEXT
        assert "biometric" in draft.biometric_responsibility_text.lower()
        assert "Colorado" in draft.biometric_responsibility_text
        assert "Texas" in draft.biometric_responsibility_text

    def test_draft_renders_model_specific_rights_assertion(self) -> None:
        entry = _make_restricted_entry()
        draft = prepare_acceptance(entry, use_context=UseContext.COMMERCIAL)
        assert entry.display_name in draft.separate_rights_assertion

    def test_draft_carries_ack_hash_matching_entry(self) -> None:
        entry = _make_restricted_entry()
        draft = prepare_acceptance(entry, use_context=UseContext.PERSONAL)
        assert draft.ack_text_sha256 == entry.ack_text_sha256
        assert draft.ack_text_version == entry.ack_text_version

    def test_draft_carries_use_context_text_hash(self) -> None:
        draft = prepare_acceptance(_make_restricted_entry(), use_context=UseContext.PERSONAL)
        assert draft.use_context_text_version == USE_CONTEXT_TEXT_VERSION
        assert draft.use_context_text_sha256 == use_context_text_sha256()


# ── confirm_acceptance ──


class TestConfirmAcceptance:
    def _all_checks_true(self) -> dict[str, bool]:
        return {cb_id: True for cb_id, _ in REQUIRED_ACK_CHECKBOXES}

    def _all_checks_false(self) -> dict[str, bool]:
        return {cb_id: False for cb_id, _ in REQUIRED_ACK_CHECKBOXES}

    def test_happy_path_persists_row(self, isolated_log_path: Path) -> None:
        entry = _make_restricted_entry()
        draft = prepare_acceptance(entry, use_context=UseContext.PERSONAL)
        row = confirm_acceptance(
            draft,
            checkbox_responses=self._all_checks_true(),
            accepted_at="2026-06-02T12:34:56+00:00",
            source_of_rights_note="for personal use only",
        )
        assert row.model_id == entry.id
        assert row.use_context_at_acceptance == "personal"
        assert row.ack_text_sha256 == entry.ack_text_sha256
        assert row.source_of_rights_note == "for personal use only"
        assert row.schema_version == SCHEMA_VERSION
        # And it actually landed on disk.
        rows = list(iter_rows())
        assert len(rows) == 1
        assert rows[0].model_id == entry.id

    def test_missing_required_checkbox_raises(self, isolated_log_path: Path) -> None:
        entry = _make_restricted_entry()
        draft = prepare_acceptance(entry, use_context=UseContext.PERSONAL)
        partial: dict[str, bool] = self._all_checks_true()
        partial.pop(next(iter(partial.keys())))  # drop one
        with pytest.raises(AcceptanceError, match="missing required"):
            confirm_acceptance(
                draft,
                checkbox_responses=partial,
                accepted_at="2026-06-02T12:34:56+00:00",
            )

    def test_unchecked_required_checkbox_raises(self, isolated_log_path: Path) -> None:
        entry = _make_restricted_entry()
        draft = prepare_acceptance(entry, use_context=UseContext.PERSONAL)
        with pytest.raises(AcceptanceError, match="check every required box"):
            confirm_acceptance(
                draft,
                checkbox_responses=self._all_checks_false(),
                accepted_at="2026-06-02T12:34:56+00:00",
            )

    def test_empty_accepted_at_raises(self, isolated_log_path: Path) -> None:
        entry = _make_restricted_entry()
        draft = prepare_acceptance(entry, use_context=UseContext.PERSONAL)
        with pytest.raises(AcceptanceError, match="accepted_at"):
            confirm_acceptance(
                draft,
                checkbox_responses=self._all_checks_true(),
                accepted_at="",
            )

    def test_separate_rights_asserted_persists(self, isolated_log_path: Path) -> None:
        entry = _make_restricted_entry()
        draft = prepare_acceptance(entry, use_context=UseContext.COMMERCIAL)
        row = confirm_acceptance(
            draft,
            checkbox_responses=self._all_checks_true(),
            accepted_at="2026-06-02T12:34:56+00:00",
            separate_rights_asserted=True,
            source_of_rights_note="commercial license from foo corp",
        )
        assert row.separate_rights_asserted is True
        assert row.source_of_rights_note == "commercial license from foo corp"

    def test_per_checkbox_responses_persist_on_acceptance_row(
        self, isolated_log_path: Path
    ) -> None:
        """Item 5 evidentiary chain: each required checkbox the user
        engaged with must be recorded individually so a future audit
        can prove "user clicked checkbox #3" not just "user submitted
        a valid form"."""
        entry = _make_restricted_entry()
        draft = prepare_acceptance(entry, use_context=UseContext.PERSONAL)
        row = confirm_acceptance(
            draft,
            checkbox_responses=self._all_checks_true(),
            accepted_at="2026-06-02T12:34:56+00:00",
        )
        assert isinstance(row.checkbox_responses, dict)
        # Every required id from the draft must appear in the row.
        for cb_id, _ in draft.required_checkboxes:
            assert cb_id in row.checkbox_responses, (
                f"acceptance row missing per-checkbox response for {cb_id!r}"
            )
            assert row.checkbox_responses[cb_id] is True

    def test_per_checkbox_responses_survive_jsonl_round_trip(self, isolated_log_path: Path) -> None:
        entry = _make_restricted_entry()
        draft = prepare_acceptance(entry, use_context=UseContext.PERSONAL)
        confirm_acceptance(
            draft,
            checkbox_responses=self._all_checks_true(),
            accepted_at="2026-06-02T12:34:56+00:00",
        )
        rows = list(iter_rows())
        assert len(rows) == 1
        assert isinstance(rows[0].checkbox_responses, dict)
        for cb_id, _ in draft.required_checkboxes:
            assert rows[0].checkbox_responses[cb_id] is True

    def test_acceptance_row_schema_version_is_2(self, isolated_log_path: Path) -> None:
        """Bumping schema_version from 1 → 2 signals to any future
        reader that the row carries the per-checkbox map (older v1
        rows do not). Stays in sync with the SCHEMA_VERSION
        constant."""
        entry = _make_restricted_entry()
        draft = prepare_acceptance(entry, use_context=UseContext.PERSONAL)
        row = confirm_acceptance(
            draft,
            checkbox_responses=self._all_checks_true(),
            accepted_at="2026-06-02T12:34:56+00:00",
        )
        assert row.schema_version == 2

    def test_legacy_v1_row_without_checkbox_field_still_parses(
        self, isolated_log_path: Path
    ) -> None:
        """Existing v1 rows on disk (written by BPP versions before
        this commit) do not carry the per-checkbox map. They must
        still load — defaulting to ``{}`` — so a user upgrading
        doesn't see their old acceptance history disappear."""
        import json

        v1_row = {
            "model_id": "legacy_v1_entry",
            "model_sha256": "0" * 64,
            "ack_text_version": "canonical-disclaimer-v2",
            "ack_text_sha256": "1" * 64,
            "use_context_text_version": "use-context-v1",
            "use_context_text_sha256": "2" * 64,
            "use_context_at_acceptance": "personal",
            "separate_rights_asserted": False,
            "terms_url": "https://example.invalid/LICENSE",
            "terms_permalink_url": "https://example.invalid/abc/LICENSE",
            "terms_retrieved_at": "2026-01-01",
            "accepted_at": "2026-01-01T00:00:00+00:00",
            "source_of_rights_note": "",
            "schema_version": 1,
        }
        isolated_log_path.parent.mkdir(parents=True, exist_ok=True)
        isolated_log_path.write_text(json.dumps(v1_row) + "\n", encoding="utf-8")
        rows = list(iter_rows())
        assert len(rows) == 1
        assert rows[0].model_id == "legacy_v1_entry"
        assert rows[0].schema_version == 1
        assert rows[0].checkbox_responses == {}


# ── terms_permalink_url evidentiary chain ──


class TestPermalinkValidation:
    """A restricted-license model with ``requires_explicit_ack=True``
    must have a non-empty ``terms_permalink_url`` — a URL pinned to a
    specific upstream commit. Without it, the acceptance row records a
    floating URL that rots when the upstream rewrites its README, and
    the chain "user X agreed to wording Y on date Z" can no longer
    point at the exact wording the user saw.

    Permissive entries (``requires_explicit_ack=False``) do not go
    through the acceptance flow at all, so they're exempt — but the
    surface test below still records the policy explicitly.
    """

    def test_confirm_refuses_when_restricted_entry_lacks_permalink(
        self, isolated_log_path: Path
    ) -> None:
        entry = _make_restricted_entry()
        entry_no_permalink = replace(entry, terms_permalink_url=None)
        draft = prepare_acceptance(entry_no_permalink, use_context=UseContext.PERSONAL)
        with pytest.raises(AcceptanceError, match="terms_permalink_url"):
            confirm_acceptance(
                draft,
                checkbox_responses={cb_id: True for cb_id, _ in REQUIRED_ACK_CHECKBOXES},
                accepted_at="2026-06-02T12:34:56+00:00",
            )

    def test_confirm_refuses_when_restricted_entry_has_empty_permalink(
        self, isolated_log_path: Path
    ) -> None:
        """Empty string is the on-disk JSONL representation of the
        no-permalink case (a JSON null deserialises to "" in the
        AcceptanceRow). Catch it at the same gate as None so we don't
        record a row whose ``terms_permalink_url`` is the empty
        string."""
        entry = _make_restricted_entry()
        entry_blank = replace(entry, terms_permalink_url="")
        draft = prepare_acceptance(entry_blank, use_context=UseContext.PERSONAL)
        with pytest.raises(AcceptanceError, match="terms_permalink_url"):
            confirm_acceptance(
                draft,
                checkbox_responses={cb_id: True for cb_id, _ in REQUIRED_ACK_CHECKBOXES},
                accepted_at="2026-06-02T12:34:56+00:00",
            )

    def test_confirm_allows_byom_without_permalink(self, isolated_log_path: Path) -> None:
        """BYOM entries are user-supplied — there's no upstream
        permalink to pin. The ack-text snapshot (BYOM_DISCLAIMER hash
        + version) carries the user's responsibility attestation; the
        permalink rule applies only to third-party upstream models."""
        entry = _make_restricted_entry("test_byom_like")
        byom_like = replace(
            entry,
            ack_text_kind="byom",
            terms_permalink_url=None,
            requires_explicit_ack=True,
        )
        draft = prepare_acceptance(byom_like, use_context=UseContext.PERSONAL)
        # BYOM dialog has only one required checkbox
        responses = {cb_id: True for cb_id, _ in draft.required_checkboxes}
        row = confirm_acceptance(
            draft,
            checkbox_responses=responses,
            accepted_at="2026-06-02T12:34:56+00:00",
        )
        assert row.model_id == byom_like.id

    def test_every_builtin_restricted_entry_has_permalink(self) -> None:
        """Builtin scan: every bundled entry that requires an explicit
        ack and isn't a BYOM-flavored ack MUST have a non-empty
        terms_permalink_url. Catches the regression where a new
        restricted model gets registered with a tag-less main-branch
        URL."""
        from bpp.registry import iter_entries

        missing = [
            e.id
            for e in iter_entries()
            if e.requires_explicit_ack
            and e.ack_text_kind != "byom"
            and not (e.terms_permalink_url or "").strip()
        ]
        assert missing == [], (
            "These restricted-license builtins lack a "
            "terms_permalink_url: " + ", ".join(missing) + ". "
            "Pin the upstream LICENSE URL to a specific commit SHA "
            "so acceptance rows record a stable evidentiary URL."
        )


# ── is_acceptance_valid_for ──


class TestIsAcceptanceValidFor:
    """The Batch-5 hard-block gate uses this to decide whether to let
    a restricted model load. Wording change invalidates older
    acceptances; permissive models trivially pass."""

    def test_permissive_entry_trivially_valid(self) -> None:
        entry = _make_permissive_entry()
        # No isolated_log_path needed — function should not consult
        # the log for permissive entries.
        assert is_acceptance_valid_for(entry) is True

    def test_no_acceptance_row_is_invalid(self, isolated_log_path: Path) -> None:
        entry = _make_restricted_entry()
        assert is_acceptance_valid_for(entry) is False

    def test_matching_acceptance_is_valid(self, isolated_log_path: Path) -> None:
        entry = _make_restricted_entry()
        draft = prepare_acceptance(entry, use_context=UseContext.PERSONAL)
        confirm_acceptance(
            draft,
            checkbox_responses={cb_id: True for cb_id, _ in REQUIRED_ACK_CHECKBOXES},
            accepted_at="2026-06-02T12:34:56+00:00",
        )
        assert is_acceptance_valid_for(entry) is True

    def test_wording_change_invalidates_acceptance(self, isolated_log_path: Path) -> None:
        """The hash on the entry is what locks the acceptance to one
        specific text. If the entry's ack_text_sha256 changes (e.g.
        a registry update bumps the wording version), older
        acceptances are no longer valid."""
        entry = _make_restricted_entry()
        draft = prepare_acceptance(entry, use_context=UseContext.PERSONAL)
        confirm_acceptance(
            draft,
            checkbox_responses={cb_id: True for cb_id, _ in REQUIRED_ACK_CHECKBOXES},
            accepted_at="2026-06-02T12:34:56+00:00",
        )
        assert is_acceptance_valid_for(entry) is True
        # Now simulate a wording change by editing the entry's hash.
        updated_entry = replace(
            entry,
            ack_text_sha256="0" * 64,
        )
        assert is_acceptance_valid_for(updated_entry) is False


# ── Acceptance log file + path ──


class TestAcceptanceLogPath:
    def test_env_var_overrides_default(self, isolated_log_path: Path) -> None:
        assert get_acceptance_log_path() == isolated_log_path

    def test_log_file_is_jsonl_one_row_per_line(self, isolated_log_path: Path) -> None:
        # Write two rows directly through the lower-level helper to
        # confirm the on-disk format.
        row = AcceptanceRow(
            model_id="x",
            model_sha256="0" * 64,
            ack_text_version="v1",
            ack_text_sha256="1" * 64,
            use_context_text_version="v1",
            use_context_text_sha256="2" * 64,
            use_context_at_acceptance="personal",
            separate_rights_asserted=False,
            terms_url="https://example.invalid/x",
            terms_permalink_url="",
            terms_retrieved_at="2026-06-02",
            accepted_at="2026-06-02T12:34:56+00:00",
        )
        append_row(row)
        append_row(replace(row, accepted_at="2026-06-02T12:35:00+00:00"))
        with isolated_log_path.open() as f:
            lines = f.readlines()
        assert len(lines) == 2
        # Each line is valid JSON ending in a newline.
        import json

        for line in lines:
            parsed = json.loads(line)
            assert parsed["model_id"] == "x"

    def test_truncated_last_line_is_skipped_not_raised(self, isolated_log_path: Path) -> None:
        """Power-loss mid-write can leave a partial JSON object on the
        last line. Readers should skip with a warning, not crash."""
        isolated_log_path.parent.mkdir(parents=True, exist_ok=True)
        with isolated_log_path.open("w") as f:
            f.write('{"model_id": "good", "model_sha256": "x", ')
            f.write('"ack_text_version": "v1", "ack_text_sha256": "x", ')
            f.write('"use_context_text_version": "v1", ')
            f.write('"use_context_text_sha256": "x", ')
            f.write('"use_context_at_acceptance": "personal", ')
            f.write('"separate_rights_asserted": false, ')
            f.write('"terms_url": "x", "terms_permalink_url": "", ')
            f.write('"terms_retrieved_at": "x", "accepted_at": "x"}\n')
            # And now a torn line.
            f.write('{"model_id": "torn", "ack_text_versi')
        rows = list_acceptances()
        assert len(rows) == 1
        assert rows[0].model_id == "good"


# ── has_accepted / find_latest_for_model ──


class TestHasAccepted:
    def test_no_log_file_means_not_accepted(self, isolated_log_path: Path) -> None:
        assert has_accepted("anything") is False

    def test_after_confirm_returns_true(self, isolated_log_path: Path) -> None:
        entry = _make_restricted_entry()
        draft = prepare_acceptance(entry, use_context=UseContext.PERSONAL)
        confirm_acceptance(
            draft,
            checkbox_responses={cb_id: True for cb_id, _ in REQUIRED_ACK_CHECKBOXES},
            accepted_at="2026-06-02T12:34:56+00:00",
        )
        assert has_accepted(entry.id) is True
        assert has_accepted("not_accepted") is False

    def test_find_latest_returns_most_recent(self, isolated_log_path: Path) -> None:
        """Multiple acceptances for the same model — find_latest
        returns the last one written (which is the most recent in
        append order)."""
        entry = _make_restricted_entry()
        draft = prepare_acceptance(entry, use_context=UseContext.PERSONAL)
        confirm_acceptance(
            draft,
            checkbox_responses={cb_id: True for cb_id, _ in REQUIRED_ACK_CHECKBOXES},
            accepted_at="2026-06-02T00:00:00+00:00",
        )
        confirm_acceptance(
            draft,
            checkbox_responses={cb_id: True for cb_id, _ in REQUIRED_ACK_CHECKBOXES},
            accepted_at="2026-06-02T12:00:00+00:00",
            source_of_rights_note="later acceptance",
        )
        latest = find_latest_for_model(entry.id)
        assert latest is not None
        assert latest.source_of_rights_note == "later acceptance"


# ── Withdrawal (append-only revocation) ──


class TestRevocation:
    def _accept(self, entry) -> None:
        draft = prepare_acceptance(entry, use_context=UseContext.PERSONAL)
        confirm_acceptance(
            draft,
            checkbox_responses={cb_id: True for cb_id, _ in REQUIRED_ACK_CHECKBOXES},
            accepted_at="2026-06-02T12:00:00+00:00",
        )

    def test_revoke_regate_model_and_preserves_audit_trail(self, isolated_log_path: Path) -> None:
        entry = _make_restricted_entry()
        self._accept(entry)
        assert is_acceptance_valid_for(entry) is True
        assert has_accepted(entry.id) is True

        row = append_revocation(entry.id)
        assert row is not None
        assert row.event == "revoke"

        # Re-gated: the load policy will block until re-acceptance.
        assert is_acceptance_valid_for(entry) is False
        assert has_accepted(entry.id) is False

        # Append-only: the original acceptance row is STILL on disk (legal
        # audit trail), now followed by the revocation.
        rows = list(iter_rows())
        assert len(rows) == 2, rows
        assert rows[0].event == "accept"
        assert rows[1].event == "revoke"

    def test_revoke_with_nothing_to_withdraw_is_noop(self, isolated_log_path: Path) -> None:
        assert append_revocation("never_accepted") is None

    def test_double_revoke_is_noop(self, isolated_log_path: Path) -> None:
        entry = _make_restricted_entry()
        self._accept(entry)
        assert append_revocation(entry.id) is not None
        # Latest row is already a revocation — nothing to withdraw again.
        assert append_revocation(entry.id) is None

    def test_reaccept_after_revoke_restores_validity(self, isolated_log_path: Path) -> None:
        entry = _make_restricted_entry()
        self._accept(entry)
        append_revocation(entry.id)
        assert is_acceptance_valid_for(entry) is False
        # Accept again — latest row is an acceptance, so it's valid.
        self._accept(entry)
        assert is_acceptance_valid_for(entry) is True
        assert has_accepted(entry.id) is True

    def test_revoked_row_round_trips_through_log(self, isolated_log_path: Path) -> None:
        entry = _make_restricted_entry()
        self._accept(entry)
        append_revocation(entry.id)
        # Re-read from disk: the event discriminator survives serialisation.
        latest = find_latest_for_model(entry.id)
        assert latest is not None
        assert latest.event == "revoke"


# ── utc_now_iso shape (so caller-supplied stamps look the same) ──


class TestUtcNowIso:
    def test_returns_iso_format(self) -> None:
        stamp = utc_now_iso()
        # YYYY-MM-DDTHH:MM:SS at minimum, with +00:00 suffix.
        assert "T" in stamp
        assert stamp.endswith("+00:00")


# ── File-system privacy (item 6 + privacy-by-design) ──


import os  # noqa: E402  (deferred to keep import section clean)
import stat  # noqa: E402
import sys  # noqa: E402


@pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason=(
        "POSIX-only: Windows ACLs don't map to the same st_mode bits, "
        "and BPP's primary distribution targets macOS + Linux."
    ),
)
class TestAcceptanceLogPermissions:
    """The acceptance log records sensitive metadata — which
    restricted models the user agreed to, their declared use context,
    and free-text source-of-rights notes. On shared machines the
    default umask makes the file world-readable, leaking this
    history to any other local user. The acceptance-log writer must
    create the file with ``0o600`` (owner read/write only) and the
    parent directory with ``0o700``."""

    def test_log_file_mode_is_0o600_when_created(self, isolated_log_path: Path) -> None:
        entry = _make_restricted_entry()
        draft = prepare_acceptance(entry, use_context=UseContext.PERSONAL)
        confirm_acceptance(
            draft,
            checkbox_responses={cb_id: True for cb_id, _ in REQUIRED_ACK_CHECKBOXES},
            accepted_at="2026-06-02T12:34:56+00:00",
        )
        assert isolated_log_path.exists()
        mode = stat.S_IMODE(os.stat(isolated_log_path).st_mode)
        assert mode == 0o600, (
            f"acceptance log mode is {oct(mode)} — should be 0o600 so "
            "no other local user can read which restricted models "
            "the user accepted"
        )

    def test_log_parent_dir_mode_is_0o700_when_created(self, tmp_path: Path, monkeypatch) -> None:
        """When BPP creates a fresh ``XDG_CONFIG_HOME/bpp/`` directory
        the mode should be ``0o700``. We point the env var at a
        non-existent subdirectory so the writer has to create it."""
        target = tmp_path / "fresh-config" / "bpp" / "model-acceptance.jsonl"
        monkeypatch.setenv("BPP_ACCEPTANCE_LOG_PATH", str(target))
        entry = _make_restricted_entry("test_dir_perms")
        draft = prepare_acceptance(entry, use_context=UseContext.PERSONAL)
        confirm_acceptance(
            draft,
            checkbox_responses={cb_id: True for cb_id, _ in REQUIRED_ACK_CHECKBOXES},
            accepted_at="2026-06-02T12:34:56+00:00",
        )
        assert target.parent.exists()
        mode = stat.S_IMODE(os.stat(target.parent).st_mode)
        assert mode == 0o700, (
            f"acceptance-log directory mode is {oct(mode)} — should be "
            "0o700 so no other local user can list the directory"
        )

    def test_pre_existing_loose_perms_get_tightened_on_next_write(
        self, isolated_log_path: Path
    ) -> None:
        """If a previous BPP version created the log with default
        (world-readable) permissions, the next acceptance write must
        tighten them. This keeps existing installs from staying
        leaky after the upgrade."""
        # First write — gives us a real log file.
        entry = _make_restricted_entry()
        draft = prepare_acceptance(entry, use_context=UseContext.PERSONAL)
        confirm_acceptance(
            draft,
            checkbox_responses={cb_id: True for cb_id, _ in REQUIRED_ACK_CHECKBOXES},
            accepted_at="2026-06-02T12:34:56+00:00",
        )
        # Simulate "previous BPP version wrote it loosely".
        os.chmod(isolated_log_path, 0o644)
        # Second write should re-tighten.
        confirm_acceptance(
            draft,
            checkbox_responses={cb_id: True for cb_id, _ in REQUIRED_ACK_CHECKBOXES},
            accepted_at="2026-06-02T13:00:00+00:00",
        )
        mode = stat.S_IMODE(os.stat(isolated_log_path).st_mode)
        assert mode == 0o600, (
            f"acceptance log mode after second write is {oct(mode)} — "
            "should have been tightened to 0o600 by the writer"
        )

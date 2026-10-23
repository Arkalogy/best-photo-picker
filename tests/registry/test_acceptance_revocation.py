"""Acceptance-flow tests — part 2 of 2: TestRevocation, TestUtcNowIso,
TestAcceptanceLogPermissions."""

# Shared fixtures are imported by name and re-bound as test parameters.
# ruff: noqa: F811

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from bpp.registry import (
    UseContext,
    append_revocation,
    confirm_acceptance,
    find_latest_for_model,
    has_accepted,
    is_acceptance_valid_for,
    prepare_acceptance,
    utc_now_iso,
)
from bpp.registry.acceptance_log import (
    iter_rows,
)
from bpp.registry.use_context import (
    REQUIRED_ACK_CHECKBOXES,
)
from tests.registry._acceptance_shared import (
    _make_restricted_entry,
    isolated_log_path,  # noqa: F401
)

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


@pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason=(
        "POSIX-only: Windows ACLs don't map to the same st_mode bits, "
        "and BPP's primary distribution targets macOS + Linux."
    ),
)
# ── File-system privacy (privacy-by-design) ──

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

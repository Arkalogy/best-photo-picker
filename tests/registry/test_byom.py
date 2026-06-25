"""Batch 6 BYOM tests.

Pins:

* The BYOM store rounds-trips on disk with the right schema and
  honors ``BPP_BYOM_PATH``.
* The on-disk file SHA-256 drives the id so re-adding the same
  bytes is idempotent.
* :func:`bpp.registry.acceptance.prepare_acceptance` dispatches on
  ``ack_text_kind`` and ships the BYOM disclaimer + single checkbox
  for BYOM entries.
* :func:`bpp.registry.acceptance.confirm_acceptance` accepts the
  BYOM-shaped response.
* :func:`bpp.registry.policy.check_model_load_allowed` treats BYOM
  entries as permissive once their (BYOM-style) acceptance row is
  on file — they pass the commercial-mode hard-block without a
  separate-rights assertion because Arkalogy isn't in the rights
  chain.
* The BYOM disclaimer carries the verbatim user-responsibility
  wording the legal-posture spec specified.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from bpp.registry import (
    BYOM_DISCLAIMER,
    BYOM_DISCLAIMER_COMPRESSED,
    BYOM_DISCLAIMER_VERSION,
    BYOMEntry,
    ModelLoadDecision,
    UseContext,
    add_byom_entry,
    byom_disclaimer_sha256,
    check_model_load_allowed,
    confirm_acceptance,
    get_byom_entry,
    list_byom_entries,
    prepare_acceptance,
    remove_byom_entry,
)


@pytest.fixture
def isolated_byom_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "byom-models.json"
    monkeypatch.setenv("BPP_BYOM_PATH", str(target))
    return target


@pytest.fixture
def isolated_acceptance_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "model-acceptance.jsonl"
    monkeypatch.setenv("BPP_ACCEPTANCE_LOG_PATH", str(target))
    return target


def _write_model_file(tmp_path: Path, content: bytes = b"fake-model") -> Path:
    fp = tmp_path / "my-model.onnx"
    fp.write_bytes(content)
    return fp


# ── Disclaimer + dispatch ──


class TestBYOMDisclaimer:
    """The BYOM disclaimer carries the verbatim user-responsibility
    wording the legal-posture spec specified."""

    def test_user_responsibility_paragraph_is_present(self) -> None:
        assert (
            "You are responsible for ensuring you have rights to use this model file"
        ) in BYOM_DISCLAIMER
        assert (
            "Best Photo Picker does not verify or grant rights to user-provided model files"
        ) in BYOM_DISCLAIMER

    def test_arkalogy_mit_distinction_present(self) -> None:
        assert "Best Photo Picker's source code is MIT-licensed" in BYOM_DISCLAIMER

    def test_compressed_form_carries_same_substance(self) -> None:
        compressed = BYOM_DISCLAIMER_COMPRESSED.lower()
        assert "responsible" in compressed
        assert "rights" in compressed
        assert "best photo picker" in compressed
        assert "user-provided" in compressed

    def test_does_not_name_specific_restricted_models(self) -> None:
        """Item 14 / trap T3 — the BYOM disclaimer must not name
        AdaFace / buffalo / antelopev2 / w600k. That would recreate
        the inducement problem."""
        forbidden = (
            "adaface",
            "buffalo",
            "antelopev2",
            "w600k",
            "insightface",
        )
        haystack = BYOM_DISCLAIMER.lower() + BYOM_DISCLAIMER_COMPRESSED.lower()
        for name in forbidden:
            assert name not in haystack, (
                f"Restricted-model name {name!r} appears in the BYOM "
                f"disclaimer. Inducement risk — the docs must describe "
                f"the BYOM mechanism without pointing users at any "
                f"specific upstream they could download from."
            )


# ── Store add / list / remove ──


class TestBYOMStore:
    def test_add_persists_entry(self, tmp_path: Path, isolated_byom_store: Path) -> None:
        fp = _write_model_file(tmp_path)
        entry = add_byom_entry(
            display_name="My ONNX",
            kind="face_embedder",
            file_path=fp,
        )
        assert isinstance(entry, BYOMEntry)
        assert entry.id.startswith("byom_")
        assert entry.display_name == "My ONNX"
        assert entry.kind == "face_embedder"
        assert entry.file_path == str(fp.resolve())
        # Hash matches what we'd compute directly.
        expected_hash = hashlib.sha256(b"fake-model").hexdigest()
        assert entry.weight_sha256 == expected_hash
        # Round-trip through list_byom_entries.
        rows = list_byom_entries()
        assert len(rows) == 1
        assert rows[0].id == entry.id

    def test_same_bytes_dedupes_on_id(self, tmp_path: Path, isolated_byom_store: Path) -> None:
        """Re-registering the same file (same SHA-256) replaces the
        existing entry rather than producing a duplicate."""
        fp = _write_model_file(tmp_path)
        first = add_byom_entry(display_name="First name", kind="face_embedder", file_path=fp)
        second = add_byom_entry(display_name="Second name", kind="face_embedder", file_path=fp)
        assert first.id == second.id
        rows = list_byom_entries()
        assert len(rows) == 1
        # The store keeps the latest registration (display_name update).
        assert rows[0].display_name == "Second name"

    def test_different_bytes_distinct_ids(self, tmp_path: Path, isolated_byom_store: Path) -> None:
        fp_a = _write_model_file(tmp_path, b"model-A")
        fp_b = tmp_path / "model-B.onnx"
        fp_b.write_bytes(b"model-B-different-bytes")
        a = add_byom_entry(display_name="A", kind="face_embedder", file_path=fp_a)
        b = add_byom_entry(display_name="B", kind="face_embedder", file_path=fp_b)
        assert a.id != b.id
        assert len(list_byom_entries()) == 2

    def test_remove_drops_entry(self, tmp_path: Path, isolated_byom_store: Path) -> None:
        fp = _write_model_file(tmp_path)
        entry = add_byom_entry(display_name="x", kind="face_embedder", file_path=fp)
        assert remove_byom_entry(entry.id) is True
        assert list_byom_entries() == []

    def test_remove_unknown_returns_false(self, isolated_byom_store: Path) -> None:
        assert remove_byom_entry("byom_nonexistent") is False

    def test_missing_file_raises(self, isolated_byom_store: Path) -> None:
        with pytest.raises(FileNotFoundError):
            add_byom_entry(
                display_name="x",
                kind="face_embedder",
                file_path="/definitely/not/a/file.onnx",
            )


# ── prepare_acceptance dispatch ──


class TestPrepareAcceptanceDispatchesByKind:
    def test_byom_entry_gets_byom_dialog(self, tmp_path: Path, isolated_byom_store: Path) -> None:
        fp = _write_model_file(tmp_path)
        byom = add_byom_entry(
            display_name="My BYOM",
            kind="face_embedder",
            file_path=fp,
        )
        entry = byom.to_model_entry()
        draft = prepare_acceptance(entry, use_context=UseContext.PERSONAL)
        assert draft.full_disclaimer == BYOM_DISCLAIMER
        assert draft.compressed_disclaimer == BYOM_DISCLAIMER_COMPRESSED
        # BYOM uses a single user-responsibility checkbox.
        assert len(draft.required_checkboxes) == 1
        assert draft.required_checkboxes[0][0] == ("byom_user_takes_responsibility")
        # No per-model rights assertion (no upstream to assert
        # against).
        assert draft.separate_rights_assertion == ""
        # The ack_text fingerprints match the BYOM constants.
        assert draft.ack_text_version == BYOM_DISCLAIMER_VERSION
        assert draft.ack_text_sha256 == byom_disclaimer_sha256()

    def test_canonical_entry_still_gets_canonical_dialog(
        self, tmp_path: Path, isolated_byom_store: Path
    ) -> None:
        """Regression check: changing the BYOM path must not affect
        the canonical-disclaimer dialog flow for existing
        restricted-model entries."""
        from bpp.registry import canonical_disclaimer_sha256
        from bpp.registry.builtins import BUFFALO_S_ENTRY

        # SFace used to be the canonical-dialog reference here, but
        # it moved to the permissive-attribution dialog under
        # Option B. Use a truly restricted entry (buffalo_s) so the
        # regression guard still validates the four-checkbox
        # canonical flow.
        draft = prepare_acceptance(BUFFALO_S_ENTRY, use_context=UseContext.PERSONAL)
        assert draft.ack_text_sha256 == canonical_disclaimer_sha256()
        assert len(draft.required_checkboxes) == 4


# ── confirm_acceptance ──


class TestConfirmAcceptanceBYOM:
    def test_byom_acceptance_persists(
        self,
        tmp_path: Path,
        isolated_byom_store: Path,
        isolated_acceptance_log: Path,
    ) -> None:
        fp = _write_model_file(tmp_path)
        byom = add_byom_entry(
            display_name="My BYOM",
            kind="face_embedder",
            file_path=fp,
        )
        entry = byom.to_model_entry()
        draft = prepare_acceptance(entry, use_context=UseContext.COMMERCIAL)
        row = confirm_acceptance(
            draft,
            checkbox_responses={
                "byom_user_takes_responsibility": True,
            },
            accepted_at="2026-06-02T12:34:56+00:00",
        )
        assert row.model_id == entry.id
        assert row.ack_text_version == BYOM_DISCLAIMER_VERSION
        assert row.ack_text_sha256 == byom_disclaimer_sha256()
        assert row.use_context_at_acceptance == "commercial"

    def test_byom_unchecked_box_raises(
        self,
        tmp_path: Path,
        isolated_byom_store: Path,
        isolated_acceptance_log: Path,
    ) -> None:
        from bpp.registry import AcceptanceError

        fp = _write_model_file(tmp_path)
        byom = add_byom_entry(display_name="x", kind="face_embedder", file_path=fp)
        entry = byom.to_model_entry()
        draft = prepare_acceptance(entry, use_context=UseContext.PERSONAL)
        with pytest.raises(AcceptanceError):
            confirm_acceptance(
                draft,
                checkbox_responses={
                    "byom_user_takes_responsibility": False,
                },
                accepted_at="2026-06-02T12:34:56+00:00",
            )


# ── Policy: BYOM is permissive once acknowledged ──


class TestBYOMPolicy:
    def test_byom_without_acceptance_blocks_needs_ack(
        self,
        tmp_path: Path,
        isolated_byom_store: Path,
        isolated_acceptance_log: Path,
    ) -> None:
        fp = _write_model_file(tmp_path)
        byom = add_byom_entry(display_name="x", kind="face_embedder", file_path=fp)
        entry = byom.to_model_entry()
        # requires_explicit_ack is True for BYOM entries — the
        # user-responsibility ack must be on file before load.
        result = check_model_load_allowed(entry, use_context=UseContext.PERSONAL)
        assert result.decision is ModelLoadDecision.BLOCKED_NEEDS_ACK

    def test_byom_with_acceptance_allows_commercial_use(
        self,
        tmp_path: Path,
        isolated_byom_store: Path,
        isolated_acceptance_log: Path,
    ) -> None:
        """The whole point of BYOM is that commercial-mode users
        get a clean path that doesn't depend on Arkalogy's rights
        chain. After the user-responsibility ack is on file, BYOM
        loads in commercial mode without requiring a separate-rights
        assertion."""
        fp = _write_model_file(tmp_path)
        byom = add_byom_entry(display_name="x", kind="face_embedder", file_path=fp)
        entry = byom.to_model_entry()
        draft = prepare_acceptance(entry, use_context=UseContext.COMMERCIAL)
        confirm_acceptance(
            draft,
            checkbox_responses={
                "byom_user_takes_responsibility": True,
            },
            accepted_at="2026-06-02T12:34:56+00:00",
            # No separate_rights_asserted — that field is for the
            # restricted-model path, not BYOM.
        )
        result = check_model_load_allowed(entry, use_context=UseContext.COMMERCIAL)
        assert result.decision is ModelLoadDecision.ALLOW


# ── ModelEntry round-trip ──


class TestBYOMModelEntryShape:
    def test_byom_to_model_entry_has_byom_ack_kind(
        self, tmp_path: Path, isolated_byom_store: Path
    ) -> None:
        fp = _write_model_file(tmp_path)
        byom = add_byom_entry(display_name="x", kind="face_embedder", file_path=fp)
        entry = byom.to_model_entry()
        assert entry.ack_text_kind == "byom"
        assert entry.requires_explicit_ack is True
        # BYOM treats Arkalogy as out of the rights chain.
        assert entry.commercial_use_restriction_known is False
        assert entry.bppicker_commercial_default_allowed is True
        assert entry.upstream_claimed_license_class.value == "unknown"

    def test_get_byom_entry_returns_round_trip(
        self, tmp_path: Path, isolated_byom_store: Path
    ) -> None:
        fp = _write_model_file(tmp_path)
        original = add_byom_entry(
            display_name="my onnx",
            kind="face_embedder",
            file_path=fp,
        )
        found = get_byom_entry(original.id)
        assert found is not None
        assert found.id == original.id
        assert found.file_path == original.file_path

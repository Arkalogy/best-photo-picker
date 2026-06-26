"""Schema + lifecycle + license + disclaimer + label tests for the
model registry foundation (Batch 1 of the legal-posture rollout).

These tests pin the contracts the rest of the plan depends on. If
any of them fails, downstream batches (default safety, gate, dialog,
acceptance log, BYOM, signed registry) are reading from a foundation
that has drifted from the legal-review specification, and the legal
posture is no longer the one we vetted.

What's specifically pinned

* :class:`ModelStatus` and :class:`LicenseClass` enums are exhaustive
  in the UI-label tables — adding a new enum value without a label
  fails CI (catches Q10 drift).
* :func:`status_behavior` returns the exact policy matrix the
  legal-posture rollout specified — adding a state without
  defining its behaviour fails CI (catches item 20 silent-failure
  hole).
* :data:`CANONICAL_DISCLAIMER` paragraphs include the verbatim
  legal-review phrasings — a routine edit that drops a key
  paragraph fails CI (catches item 7 / item 17 wording drift).
* :func:`ui_label_for_entry` is deterministic and omits sections
  cleanly when their corresponding flag isn't set (catches the
  picker showing stray punctuation or warranty-like phrasing).
"""

from __future__ import annotations

import re

import pytest

from bpp.registry import (
    CANONICAL_DISCLAIMER,
    CANONICAL_DISCLAIMER_COMPRESSED,
    CANONICAL_DISCLAIMER_VERSION,
    LicenseClass,
    ModelEntry,
    ModelStatus,
    StatusBehavior,
    canonical_disclaimer_sha256,
    get_entry,
    list_entries,
    plain_english_license_label,
    plain_english_status_label,
    register_entry,
    status_behavior,
    ui_label_for_entry,
)
from bpp.registry.disclaimers import canonical_disclaimer_compressed_sha256
from bpp.registry.model_registry import _reset_registry_for_tests


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    """Empty the global registry between tests so test order does not
    leak entries into each other's assertions."""
    _reset_registry_for_tests()


# ── Enum exhaustiveness ──


class TestEnumExhaustiveness:
    """Adding a new ModelStatus / LicenseClass without a UI label is
    a silent-failure path — the picker would render an empty string
    or KeyError. The two tests below assert that every defined enum
    value has both a translation entry and a behaviour entry."""

    def test_every_model_status_has_a_label(self) -> None:
        for status in ModelStatus:
            label = plain_english_status_label(status)
            assert label, (
                f"ModelStatus.{status.name} has no plain-english label. "
                f"Add it to _STATUS_LABELS in bpp/registry/labels.py."
            )
            assert label.strip() == label, (
                f"ModelStatus.{status.name} label has leading/trailing whitespace: {label!r}"
            )

    def test_every_license_class_has_a_label(self) -> None:
        for license_class in LicenseClass:
            label = plain_english_license_label(license_class)
            assert label, (
                f"LicenseClass.{license_class.name} has no plain-english label. "
                f"Add it to _LICENSE_LABELS in bpp/registry/labels.py."
            )
            assert label.strip() == label

    def test_every_model_status_has_behavior(self) -> None:
        """Q9 / item 20: a status with no behaviour entry would let
        a takedown be silently ignored. Catch the omission at CI time."""
        for status in ModelStatus:
            behavior = status_behavior(status)
            assert isinstance(behavior, StatusBehavior)


# ── Status policy matrix (item 20) ──


class TestStatusBehaviorMatrix:
    """The legal-posture rollout specified the exact behaviour
    table for the four states. These tests pin each row so a future
    edit cannot quietly weaken (e.g. "let LEGALLY_BLOCKED keep
    serving existing local copies")."""

    def test_available_allows_everything(self) -> None:
        b = status_behavior(ModelStatus.AVAILABLE)
        assert b.new_download_allowed
        assert b.existing_local_use_allowed
        assert not b.warn_on_use
        assert not b.requires_rights_assertion_to_use

    def test_deprecated_allows_use_but_warns(self) -> None:
        b = status_behavior(ModelStatus.DEPRECATED)
        assert b.new_download_allowed
        assert b.existing_local_use_allowed
        assert b.warn_on_use
        assert not b.requires_rights_assertion_to_use

    def test_withdrawn_blocks_new_but_allows_existing(self) -> None:
        b = status_behavior(ModelStatus.WITHDRAWN_NO_NEW_DOWNLOADS)
        assert not b.new_download_allowed
        assert b.existing_local_use_allowed
        assert b.warn_on_use
        assert not b.requires_rights_assertion_to_use

    def test_legally_blocked_blocks_everything_without_rights_assertion(self) -> None:
        """The strongest state — used on a legal takedown. Existing
        local copies refuse to load unless the user re-asserts
        separate rights. Pin both halves of that contract."""
        b = status_behavior(ModelStatus.LEGALLY_BLOCKED)
        assert not b.new_download_allowed
        assert not b.existing_local_use_allowed
        assert b.warn_on_use
        assert b.requires_rights_assertion_to_use


# ── Canonical disclaimer (items 7 + 8 + 17) ──


class TestCanonicalDisclaimer:
    """The disclaimer contains specific sentences the legal-posture spec
    asked to be preserved verbatim. A maintainer who rewrites a
    paragraph cannot silently drop these without the test failing."""

    def test_version_constant_is_set(self) -> None:
        assert CANONICAL_DISCLAIMER_VERSION
        assert "v" in CANONICAL_DISCLAIMER_VERSION

    def test_distribution_disclaimer_phrasing_is_preserved(self) -> None:
        """Item 7 — Arkalogy distribution posture, verbatim wording."""
        assert (
            "Best Photo Picker does not redistribute or bundle this model" in CANONICAL_DISCLAIMER
        )
        assert "downloaded by the user's local installation directly" in CANONICAL_DISCLAIMER
        assert "from the upstream provider" in CANONICAL_DISCLAIMER

    def test_arkalogy_monetization_framing_is_corrected(self) -> None:
        """Item 17 — the corrected wording distinguishes Arkalogy's
        monetization stance from third-party MIT rights."""
        assert "Arkalogy will not monetize" in CANONICAL_DISCLAIMER
        assert "MIT-licensed" in CANONICAL_DISCLAIMER
        assert "third parties may still use the code commercially" in CANONICAL_DISCLAIMER
        # Negative: the prior, legally inaccurate phrasing must NOT appear.
        assert "BPP will never be commercialized" not in CANONICAL_DISCLAIMER

    def test_source_vs_models_distinction_present(self) -> None:
        """Item 8 — replace any "MIT and free for commercial use"
        phrasing with the source-vs-models distinction."""
        assert "Best Photo Picker's source code is MIT-licensed" in CANONICAL_DISCLAIMER
        assert "Optional third-party models may have separate licenses" in CANONICAL_DISCLAIMER
        # Negative: the misleading phrasing must NOT appear.
        assert "MIT and free for commercial use" not in CANONICAL_DISCLAIMER

    def test_compressed_form_carries_same_substance(self) -> None:
        """Q6 — the compressed form used in the click-through dialog
        must convey the same legal points as the full form (no
        meaning swap allowed)."""
        # Each substantive claim from the full disclaimer must appear,
        # at least topically, in the compressed form.
        compressed = CANONICAL_DISCLAIMER_COMPRESSED.lower()
        assert "monetize" in compressed
        assert "mit" in compressed
        assert "third-party" in compressed or "third party" in compressed
        assert "commercial" in compressed
        assert "gate" in compressed or "gated" in compressed

    def test_disclaimer_hash_is_stable_and_distinct(self) -> None:
        """The full and compressed forms must hash to distinct values
        (they are deliberately different texts). The hashes are also
        what the acceptance log records, so they need to be
        deterministic across runs."""
        full_hash = canonical_disclaimer_sha256()
        compressed_hash = canonical_disclaimer_compressed_sha256()
        assert len(full_hash) == 64
        assert len(compressed_hash) == 64
        assert full_hash != compressed_hash
        # Stability: a second call returns the same value.
        assert canonical_disclaimer_sha256() == full_hash


# ── Version-locked hash pin (item 19 — evidentiary chain) ──


#: Hash-version map for the canonical and BYOM disclaimers. The whole
#: evidentiary chain ("we can prove what wording user X agreed to on
#: date Z") rests on each ``*_VERSION`` constant pinning ONE specific
#: text. A typo fix that changes the bytes without bumping the
#: version silently desyncs every historical acceptance row whose
#: hash was snapshotted under the old text.
#:
#: When you intentionally update the wording:
#:   1. Bump the ``_VERSION`` constant in disclaimers.py.
#:   2. Run this file with -p no:cacheprovider once and copy the new
#:      hash from the assertion failure into the table below.
#:   3. Commit the constant + the table edit in the same change.
_PINNED_DISCLAIMER_HASHES = {
    "canonical-disclaimer-v2": {
        "full": "a7346a755f33a9a4b1a6e4a08bcddce0dfca976b63baa15c2139254c1d00c5e5",
        "compressed": "45886a7a76c11a2815480b2aa5ce708d245bbadc5934271c510a1de177afa0a6",
    },
    "byom-disclaimer-v2": {
        "full": "61834c690bb43802b788a1ffd00937b8d5632b43ba01808f8a6995a85ec70793",
        "compressed": "08a299518ebb9c34320c0e31477e7e8a5a2d635097de7b1e740a2c9135abd270",
    },
}


class TestDisclaimerVersionLock:
    """A maintainer who edits the disclaimer text without bumping the
    version triggers this test. The error message tells them which
    constant moved and how to recover."""

    def test_canonical_disclaimer_version_is_pinned(self) -> None:
        assert CANONICAL_DISCLAIMER_VERSION in _PINNED_DISCLAIMER_HASHES, (
            f"CANONICAL_DISCLAIMER_VERSION is {CANONICAL_DISCLAIMER_VERSION!r} "
            "but no entry exists in _PINNED_DISCLAIMER_HASHES. If you "
            "bumped the version, add the new hashes to the map at the "
            "top of TestDisclaimerVersionLock."
        )

    def test_canonical_full_disclaimer_hash_matches_pin(self) -> None:
        expected = _PINNED_DISCLAIMER_HASHES[CANONICAL_DISCLAIMER_VERSION]["full"]
        actual = canonical_disclaimer_sha256()
        assert actual == expected, (
            f"CANONICAL_DISCLAIMER text changed without a version "
            f"bump. Version is still {CANONICAL_DISCLAIMER_VERSION!r} "
            f"but the SHA-256 is now {actual!r} (expected {expected!r}).\n"
            "Either:\n"
            "  (a) revert the text change, OR\n"
            "  (b) bump CANONICAL_DISCLAIMER_VERSION in "
            "      bpp/registry/disclaimers.py to the next version "
            "      string AND add the new hash to "
            "      _PINNED_DISCLAIMER_HASHES in this file.\n"
            "Silent edits desync every historical acceptance row whose "
            "ack_text_sha256 was snapshotted under the old text — the "
            "evidentiary chain breaks for those users."
        )

    def test_canonical_compressed_disclaimer_hash_matches_pin(self) -> None:
        expected = _PINNED_DISCLAIMER_HASHES[CANONICAL_DISCLAIMER_VERSION]["compressed"]
        actual = canonical_disclaimer_compressed_sha256()
        assert actual == expected, (
            f"CANONICAL_DISCLAIMER_COMPRESSED text changed without a "
            f"version bump. SHA-256 is now {actual!r} (expected "
            f"{expected!r}). See the canonical-full-hash failure "
            "message for the recovery procedure."
        )

    def test_byom_disclaimer_version_is_pinned(self) -> None:
        from bpp.registry.disclaimers import BYOM_DISCLAIMER_VERSION

        assert BYOM_DISCLAIMER_VERSION in _PINNED_DISCLAIMER_HASHES, (
            f"BYOM_DISCLAIMER_VERSION is {BYOM_DISCLAIMER_VERSION!r} "
            "but no entry exists in _PINNED_DISCLAIMER_HASHES. If you "
            "bumped the version, add the new hashes."
        )

    def test_byom_full_disclaimer_hash_matches_pin(self) -> None:
        from bpp.registry.disclaimers import (
            BYOM_DISCLAIMER_VERSION,
            byom_disclaimer_sha256,
        )

        expected = _PINNED_DISCLAIMER_HASHES[BYOM_DISCLAIMER_VERSION]["full"]
        actual = byom_disclaimer_sha256()
        assert actual == expected, (
            f"BYOM_DISCLAIMER text changed without a version bump. "
            f"Version is still {BYOM_DISCLAIMER_VERSION!r} but the "
            f"SHA-256 is now {actual!r} (expected {expected!r}). "
            "See the canonical-full-hash failure message for the "
            "recovery procedure."
        )

    def test_byom_compressed_disclaimer_hash_matches_pin(self) -> None:
        from bpp.registry.disclaimers import (
            BYOM_DISCLAIMER_VERSION,
            byom_disclaimer_compressed_sha256,
        )

        expected = _PINNED_DISCLAIMER_HASHES[BYOM_DISCLAIMER_VERSION]["compressed"]
        actual = byom_disclaimer_compressed_sha256()
        assert actual == expected, (
            f"BYOM_DISCLAIMER_COMPRESSED text changed without a "
            f"version bump. SHA-256 is now {actual!r} (expected "
            f"{expected!r})."
        )


# ── ModelEntry composition + label derivation (items 3 + 24 / Q10) ──


def _make_entry(
    *,
    entry_id: str = "test_entry",
    display_name: str = "Test Entry",
    license_class: LicenseClass = LicenseClass.MIT,
    commercial_use_restriction_known: bool = False,
    bppicker_commercial_default_allowed: bool = True,
    commercial_unlock_requires_rights_assertion: bool = False,
    status: ModelStatus = ModelStatus.AVAILABLE,
    requires_explicit_ack: bool = False,
    default_for_kind: bool = False,
) -> ModelEntry:
    return ModelEntry(
        id=entry_id,
        display_name=display_name,
        kind="face_embedder",
        source_url="https://example.invalid/model.onnx",
        terms_url="https://example.invalid/LICENSE",
        terms_permalink_url=None,
        terms_retrieved_at="2026-06-02",
        license_summary="License summary used in tests",
        requires_explicit_ack=requires_explicit_ack,
        ack_text_version=CANONICAL_DISCLAIMER_VERSION,
        ack_text_sha256=canonical_disclaimer_sha256(),
        upstream_claimed_license_class=license_class,
        commercial_use_restriction_known=commercial_use_restriction_known,
        bppicker_commercial_default_allowed=bppicker_commercial_default_allowed,
        commercial_unlock_requires_rights_assertion=commercial_unlock_requires_rights_assertion,
        status=status,
        training_data="LFW-derived",
        weight_sha256="0" * 64,
        default_for_kind=default_for_kind,
    )


class TestRegistryCRUD:
    def test_register_then_get_returns_the_entry(self) -> None:
        entry = _make_entry(entry_id="my_entry")
        register_entry(entry)
        out = get_entry("my_entry")
        assert out is entry

    def test_get_unknown_returns_none(self) -> None:
        assert get_entry("not_registered") is None

    def test_list_entries_preserves_insertion_order(self) -> None:
        a = _make_entry(entry_id="a")
        b = _make_entry(entry_id="b")
        c = _make_entry(entry_id="c")
        register_entry(b)
        register_entry(a)
        register_entry(c)
        assert [e.id for e in list_entries()] == ["b", "a", "c"]

    def test_register_same_id_replaces(self) -> None:
        """Batch 8 — signed remote registry overlays updates onto the
        bundled baseline. Replacement on same id is intentional."""
        entry_v1 = _make_entry(entry_id="x", display_name="X v1")
        entry_v2 = _make_entry(entry_id="x", display_name="X v2")
        register_entry(entry_v1)
        register_entry(entry_v2)
        out = get_entry("x")
        assert out is not None
        assert out.display_name == "X v2"


class TestUiLabelDerivation:
    """Q10 — registry uses precise non-warranty field names; UI
    surfaces plain English. These tests pin the derivation so the
    UI cannot drift from the registry, and the registry stays free
    of legal-opinion-sounding language."""

    def test_simple_permissive_available_entry(self) -> None:
        entry = _make_entry(
            display_name="SFace (YuNet + SFace ONNX)",
            license_class=LicenseClass.APACHE_2_0,
            commercial_use_restriction_known=False,
            status=ModelStatus.AVAILABLE,
        )
        label = ui_label_for_entry(entry)
        assert "SFace" in label
        assert "Apache 2.0" in label
        assert "(commercial use: restricted)" not in label
        assert "(available)" not in label, (
            "AVAILABLE is the default state — no parenthetical badge "
            "should appear for it. Got: " + label
        )

    def test_restricted_entry_shows_commercial_restriction(self) -> None:
        entry = _make_entry(
            display_name="AdaFace IR-50 (MS1MV2)",
            license_class=LicenseClass.RESEARCH_NON_COMMERCIAL,
            commercial_use_restriction_known=True,
            bppicker_commercial_default_allowed=False,
            commercial_unlock_requires_rights_assertion=True,
            status=ModelStatus.AVAILABLE,
            requires_explicit_ack=True,
        )
        label = ui_label_for_entry(entry)
        assert "AdaFace" in label
        assert "Research / non-commercial" in label
        assert "(commercial use: restricted)" in label
        # Negative: no warranty-like phrasing in the UI.
        assert "safe" not in label.lower()
        assert "approved" not in label.lower()

    def test_withdrawn_entry_shows_status_badge(self) -> None:
        entry = _make_entry(
            display_name="Some Model",
            status=ModelStatus.WITHDRAWN_NO_NEW_DOWNLOADS,
        )
        label = ui_label_for_entry(entry)
        assert "(no longer offered for new downloads)" in label

    def test_legally_blocked_entry_shows_status_badge(self) -> None:
        entry = _make_entry(
            display_name="Blocked Model",
            status=ModelStatus.LEGALLY_BLOCKED,
        )
        label = ui_label_for_entry(entry)
        assert "(legally blocked)" in label

    def test_label_components_are_separated_by_single_spaces(self) -> None:
        """The composed label must not contain double spaces or
        stray punctuation. A picker row with "Model  —  License "
        looks broken."""
        entry = _make_entry(
            display_name="Model",
            license_class=LicenseClass.MIT,
            commercial_use_restriction_known=True,
            status=ModelStatus.DEPRECATED,
        )
        label = ui_label_for_entry(entry)
        assert "  " not in label, f"Double space in label: {label!r}"
        # No leading/trailing whitespace either.
        assert label == label.strip()
        # No empty parens.
        assert "()" not in label
        # Single dash separator only.
        assert label.count("—") == 1


class TestFieldNamesAreNonWarranty:
    """Item 24 — the rename's whole point was to remove
    legal-opinion-sounding language from the data model. These tests
    confirm the renamed fields are present and the warranty-like
    name is gone."""

    def test_renamed_fields_are_on_the_dataclass(self) -> None:
        entry = _make_entry()
        # The new non-warranty fields.
        assert hasattr(entry, "commercial_use_restriction_known")
        assert hasattr(entry, "upstream_claimed_license_class")
        assert hasattr(entry, "bppicker_commercial_default_allowed")
        assert hasattr(entry, "commercial_unlock_requires_rights_assertion")

    def test_warranty_like_field_name_is_not_present(self) -> None:
        """If a future refactor reintroduces commercial_safe, this
        test fails — and surfaces the rename intent in the diff."""
        entry = _make_entry()
        assert not hasattr(entry, "commercial_safe"), (
            "The commercial_safe field was intentionally removed in "
            "item 24 because 'safe' reads as a legal opinion. Use "
            "commercial_use_restriction_known + "
            "bppicker_commercial_default_allowed instead."
        )


class TestEvidentiaryFieldsPresentForAcceptanceLog:
    """Item 19 — restricted-model entries must carry the fields the
    acceptance log will later snapshot, so we can later prove what
    wording the user agreed to and what model file they pointed at."""

    def test_entry_carries_terms_permalink_slot(self) -> None:
        entry = _make_entry()
        # Permalink can be None for entries where no permalink form
        # exists upstream, but the slot itself must exist on the
        # dataclass.
        assert "terms_permalink_url" in {f.name for f in entry.__dataclass_fields__.values()}

    def test_entry_carries_ack_text_version_and_hash(self) -> None:
        entry = _make_entry()
        assert entry.ack_text_version
        assert re.fullmatch(r"[0-9a-f]{64}", entry.ack_text_sha256), (
            "ack_text_sha256 must be a 64-char hex SHA-256 string. Got: " + entry.ack_text_sha256
        )

    def test_entry_carries_weight_sha256_slot(self) -> None:
        """Weight integrity verification depends on the registered
        SHA-256 matching the downloaded bytes. Slot must exist; the
        value is empty for metadata-only / BYOM placeholder entries
        but the registry consumer can tell the difference."""
        entry = _make_entry()
        assert hasattr(entry, "weight_sha256")
        # Test fixture sets a 64-zero string; treat that as a valid
        # hex shape (production entries will set the real hash).
        assert len(entry.weight_sha256) in {0, 64}

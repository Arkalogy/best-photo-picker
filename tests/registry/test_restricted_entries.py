"""Parametrized policy-flag pins for every restricted-license
bundled entry.

Every entry in the picker that shows "Restricted-license models"
must have the right combination of flags for the runtime gates to
fire correctly:

* ``requires_explicit_ack=True``  — click-through dialog fires
* ``commercial_use_restriction_known=True``  — hard-block in
  commercial mode
* ``bppicker_commercial_default_allowed=False`` — no commercial use
  without separate rights
* ``commercial_unlock_requires_rights_assertion=True`` — escape
  hatch requires the rights checkbox
* ``status=AVAILABLE`` (default state; status tightening still
  available via the remote-registry overlay)
* ``ack_text_kind=canonical`` (canonical 4-checkbox dialog)

A future maintainer who flips one of these on any restricted entry
silently breaks the legal posture. The parametrize matrix here
catches the change at build time.

The narrative of each restricted entry — why it's restricted —
lives in the docstring above each entry in
``bpp/registry/builtins.py``. This test file just confirms the
flag combination is consistent across all of them.
"""

from __future__ import annotations

import pytest

from bpp.registry import (
    LicenseClass,
    ModelStatus,
    get_entry,
)

#: Every restricted-license entry in the bundled baseline.
#: Adding a new restricted entry means adding its id here.
RESTRICTED_ENTRY_IDS = (
    "insightface_buffalo_s",
    "ultralytics_yolov11n_pets",
    "nudenet_320n",
    "lama_inpaint_research",
)

#: MIT-permissive entries — bypass the click-through entirely
#: (Option B from the legal-posture discussion: only literal MIT
#: skips the acceptance dialog).
MIT_PERMISSIVE_ENTRY_IDS = (
    "insightface_scrfd_25g",
    "openai_clip_vit_b32_onnx",
)

#: Permissive-attribution entries — Apache 2.0, BSD, Boost. Still
#: permissive (commercial use allowed, no commercial-use
#: restriction), but require a one-time click-through so the user
#: has explicitly seen the attribution obligations (NOTICE file,
#: copyright preservation, license-text preservation).
ATTRIBUTION_PERMISSIVE_ENTRY_IDS = (
    "sface_yunet",
    "dlib_face_recognition_resnet_v1",
    "opencv_yunet",
)

#: Every permissive entry in the bundled baseline. Adding a new
#: permissive entry means adding its id to one of the two tuples
#: above (depending on whether its license is literal MIT or
#: attribution-bearing).
PERMISSIVE_ENTRY_IDS = MIT_PERMISSIVE_ENTRY_IDS + ATTRIBUTION_PERMISSIVE_ENTRY_IDS


# ── Existence + status ──


@pytest.mark.parametrize("entry_id", RESTRICTED_ENTRY_IDS + PERMISSIVE_ENTRY_IDS)
def test_entry_is_registered(entry_id: str) -> None:
    """The id is in the bundled baseline. If you intentionally
    removed an entry, also remove its id from the constant above."""
    entry = get_entry(entry_id)
    assert entry is not None, (
        f"{entry_id!r} is no longer in the bundled baseline. Update "
        f"RESTRICTED_ENTRY_IDS / PERMISSIVE_ENTRY_IDS in this file "
        "or restore the entry in bpp/registry/builtins.py."
    )


#: Face-related entry ids — these must declare produces_biometric_data=True.
#: Adding a new face entry (embedder OR detector) means adding its id here.
FACE_RELATED_ENTRY_IDS = (
    "sface_yunet",
    "dlib_face_recognition_resnet_v1",
    "insightface_buffalo_s",
    "insightface_scrfd_25g",
    "opencv_yunet",
)

#: Non-face entry ids — these must declare produces_biometric_data=False.
#: Showing the biometric responsibility block on a pet detector or
#: inpainter is overinclusive — the surface-parity tests catch the
#: mismarking.
NON_FACE_ENTRY_IDS = (
    "openai_clip_vit_b32_onnx",
    "ultralytics_yolov11n_pets",
    "nudenet_320n",
    "lama_inpaint_research",
)


@pytest.mark.parametrize("entry_id", FACE_RELATED_ENTRY_IDS)
def test_face_entry_declares_biometric_data(entry_id: str) -> None:
    """Face embedders and face detectors produce biometric data.
    The click-through dialog must render the
    biometric-responsibility paragraph (Colorado HB24-1130 + Texas
    CUBI citations) for these. Marking one False silently suppresses
    the legal notice."""
    entry = get_entry(entry_id)
    assert entry.produces_biometric_data is True, (
        f"{entry_id} is a face-related entry but does not declare "
        "produces_biometric_data=True. The biometric responsibility "
        "block will not render in the dialog."
    )


@pytest.mark.parametrize("entry_id", NON_FACE_ENTRY_IDS)
def test_non_face_entry_does_not_declare_biometric(
    entry_id: str,
) -> None:
    """Pet detectors, inpainters, nudity classifiers, semantic
    search do NOT produce biometric data. Showing the biometric
    block on these is overinclusive — confusing for the user and
    legally unjustified."""
    entry = get_entry(entry_id)
    assert entry.produces_biometric_data is False, (
        f"{entry_id} is not a face-related entry but declares "
        "produces_biometric_data=True. The biometric block will "
        "render on the dialog where it's not relevant."
    )


@pytest.mark.parametrize("entry_id", RESTRICTED_ENTRY_IDS + PERMISSIVE_ENTRY_IDS)
def test_entry_status_is_available_by_default(entry_id: str) -> None:
    """Every bundled entry starts in AVAILABLE. Status tightening
    (toward LEGALLY_BLOCKED) happens via the remote-registry overlay
    and is single-sig safe; the BASELINE always starts here."""
    entry = get_entry(entry_id)
    assert entry.status is ModelStatus.AVAILABLE


# ── Restricted-entry flag matrix ──


@pytest.mark.parametrize("entry_id", RESTRICTED_ENTRY_IDS)
class TestRestrictedFlags:
    def test_requires_explicit_ack(self, entry_id: str) -> None:
        entry = get_entry(entry_id)
        assert entry.requires_explicit_ack is True, (
            f"{entry_id} must require explicit ack so the click-through dialog fires."
        )

    def test_commercial_use_restriction_known(self, entry_id: str) -> None:
        entry = get_entry(entry_id)
        assert entry.commercial_use_restriction_known is True, (
            f"{entry_id} must declare its commercial-use restriction "
            "so the hard-block in commercial mode fires."
        )

    def test_commercial_default_disallowed(self, entry_id: str) -> None:
        entry = get_entry(entry_id)
        assert entry.bppicker_commercial_default_allowed is False, (
            f"{entry_id} must disallow commercial use by default."
        )

    def test_unlock_requires_rights_assertion(self, entry_id: str) -> None:
        entry = get_entry(entry_id)
        assert entry.commercial_unlock_requires_rights_assertion is True, (
            f"{entry_id} must require the 'I have separate rights' assertion in commercial mode."
        )

    def test_ack_text_kind_is_canonical(self, entry_id: str) -> None:
        entry = get_entry(entry_id)
        assert entry.ack_text_kind == "canonical", (
            f"{entry_id} must use the canonical 4-checkbox dialog. "
            "BYOM has a different (shorter) dialog reserved for "
            "user-supplied weights."
        )

    def test_license_class_is_non_permissive(self, entry_id: str) -> None:
        """Restricted entries must claim a non-permissive license
        class. Apache/MIT/BSD are permissive — a restricted entry
        with one of those classes is internally inconsistent."""
        entry = get_entry(entry_id)
        permissive = {
            LicenseClass.APACHE_2_0,
            LicenseClass.MIT,
            LicenseClass.BSD_3_CLAUSE,
            LicenseClass.BOOST_SOFTWARE_LICENSE,
        }
        assert entry.upstream_claimed_license_class not in permissive, (
            f"{entry_id} is marked restricted but claims a "
            f"permissive license class "
            f"{entry.upstream_claimed_license_class.value!r}. "
            "Either the flags are wrong or the license class is."
        )

    def test_terms_permalink_url_is_present(self, entry_id: str) -> None:
        """The acceptance log records the permalink the user
        agreed to. Without it, future upstream README rewording
        makes the row unverifiable."""
        entry = get_entry(entry_id)
        assert entry.terms_permalink_url is not None
        assert entry.terms_permalink_url.startswith("https://")


# ── Permissive-entry flag matrix ──


@pytest.mark.parametrize("entry_id", PERMISSIVE_ENTRY_IDS)
class TestPermissiveFlags:
    def test_commercial_default_allowed(self, entry_id: str) -> None:
        entry = get_entry(entry_id)
        assert entry.bppicker_commercial_default_allowed is True

    def test_no_commercial_use_restriction(self, entry_id: str) -> None:
        entry = get_entry(entry_id)
        assert entry.commercial_use_restriction_known is False


@pytest.mark.parametrize("entry_id", MIT_PERMISSIVE_ENTRY_IDS)
class TestMitPermissiveFlags:
    """MIT-licensed entries bypass the click-through entirely. Option
    B from the legal-posture discussion: only literal MIT is exempt."""

    def test_does_not_require_explicit_ack(self, entry_id: str) -> None:
        entry = get_entry(entry_id)
        assert entry.requires_explicit_ack is False, (
            f"{entry_id} is registered as MIT — the click-through must not fire."
        )

    def test_ack_text_kind_is_canonical_default(self, entry_id: str) -> None:
        # ack_text_kind has no effect when requires_explicit_ack=False,
        # but should remain the canonical default to keep the field
        # consistent across the baseline.
        entry = get_entry(entry_id)
        assert entry.ack_text_kind == "canonical"


@pytest.mark.parametrize("entry_id", ATTRIBUTION_PERMISSIVE_ENTRY_IDS)
class TestAttributionPermissiveFlags:
    """Apache 2.0, BSD, Boost — permissive licenses with attribution
    obligations. The click-through fires once so the user has
    explicitly seen the obligations before download."""

    def test_requires_explicit_ack(self, entry_id: str) -> None:
        entry = get_entry(entry_id)
        assert entry.requires_explicit_ack is True, (
            f"{entry_id} carries attribution obligations — the user "
            "must see them once before the model downloads."
        )

    def test_uses_permissive_attribution_disclaimer(self, entry_id: str) -> None:
        entry = get_entry(entry_id)
        assert entry.ack_text_kind == "permissive_attribution", (
            f"{entry_id} must use the shorter permissive-attribution "
            "disclaimer, NOT the canonical (restricted) disclaimer "
            "that asserts non-commercial restrictions."
        )

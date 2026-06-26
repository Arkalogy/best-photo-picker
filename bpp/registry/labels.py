"""Translate precise registry field values into user-facing plain English.

Q10 of the legal-posture rollout: the registry uses non-warranty
field names (``commercial_use_restriction_known``,
``upstream_claimed_license_class``, etc.) so the data model does not
read like a legal opinion. But users still need a readable picker
entry — surfacing raw enum values like
``research_non_commercial`` directly in Settings would make the
picker feel like a legal form and (slightly) reads as Arkalogy
asserting the precise classification.

This module owns the single function that translates every
field-to-UI mapping. The mapping is intentionally centralized so a
test can pin every enum value to a specific user-visible string —
preventing any drift between the registry and the picker UI without
relying on convention.

Three derivation helpers:

* :func:`plain_english_status_label` — translates :class:`ModelStatus`
  to its picker label.
* :func:`plain_english_license_label` — translates
  :class:`LicenseClass` to its picker label.
* :func:`ui_label_for_entry` — composes a full picker row label from
  a :class:`ModelEntry` (display name + license subtitle + status
  badge text). The composed string is what the Settings → Models
  panel renders for one row.

Why everything goes through a function rather than an attribute on
the enum

  Putting the UI string directly on the enum (e.g. as a ``label``
  property) would re-couple the data model and the presentation
  layer that the rename intended to separate. Keeping translations
  in this module preserves the data-model-is-not-an-opinion
  property of item 24 while still giving the UI one place to look.
"""

from __future__ import annotations

from bpp.registry.model_registry import LicenseClass, ModelEntry, ModelStatus

#: Subtitle displayed under the "Restricted-license models" group
#: header in the picker (item 4 + Q1). Same text shipped from one
#: place so the GUI picker, the CLI `bpp model list` output, and the
#: Settings → Models panel render identical wording.
RESTRICTED_GROUP_SUBTITLE = "Research-only / non-commercial unless you have separate rights"

#: Human-readable group title. Pairs with :data:`RESTRICTED_GROUP_SUBTITLE`.
RESTRICTED_GROUP_TITLE = "Restricted-license models"

#: Title + subtitle for the permissive group. Plain so the picker
#: groups permissive entries under a neutral header rather than
#: implying anything is "recommended" or "best" (trap T3).
PERMISSIVE_GROUP_TITLE = "Permissively-licensed models"
PERMISSIVE_GROUP_SUBTITLE = "Free for commercial and personal use"


def plain_english_status_label(status: ModelStatus) -> str:
    """Translate a :class:`ModelStatus` value to its picker label.

    Strings here are the user-visible badges shown next to a model
    entry. Every enum value MUST have an entry; the test suite
    asserts exhaustiveness so a future enum addition without a label
    fails CI.
    """
    return _STATUS_LABELS[status]


def plain_english_license_label(license_class: LicenseClass) -> str:
    """Translate a :class:`LicenseClass` value to its picker subtitle.

    Used by :func:`ui_label_for_entry` to compose the subtitle of a
    picker row. Like :func:`plain_english_status_label`, exhaustive
    coverage is enforced by tests.
    """
    return _LICENSE_LABELS[license_class]


def group_for_entry(entry: ModelEntry) -> tuple[str, str]:
    """Return ``(group_title, group_subtitle)`` for the picker group
    that ``entry`` belongs to.

    Item 4: the restricted-license group must always be labelled
    as such (not "experimental," not "recommended"), so the picker
    cannot accidentally read like a quality endorsement of
    restricted models. Pairs with :func:`ui_label_for_entry` for
    the per-row composition.
    """
    if entry.commercial_use_restriction_known:
        return RESTRICTED_GROUP_TITLE, RESTRICTED_GROUP_SUBTITLE
    return PERMISSIVE_GROUP_TITLE, PERMISSIVE_GROUP_SUBTITLE


def ui_label_for_entry(entry: ModelEntry) -> str:
    """Compose the full picker-row label for ``entry``.

    Returns a one-line string composed of:

    * the entry's ``display_name``;
    * a "—" separator;
    * the license subtitle from
      :func:`plain_english_license_label`;
    * a parenthetical commercial-use note when
      ``commercial_use_restriction_known`` is ``True``;
    * a parenthetical status badge when the entry is not
      :data:`ModelStatus.AVAILABLE`.

    The pieces are formatted so each component is independently
    omittable; a model with no commercial restriction and an
    AVAILABLE status renders simply as "Display Name — License."
    """
    parts: list[str] = [entry.display_name]
    parts.append("—")
    parts.append(plain_english_license_label(entry.upstream_claimed_license_class))
    if entry.commercial_use_restriction_known:
        parts.append("(commercial use: restricted)")
    if entry.status is not ModelStatus.AVAILABLE:
        parts.append(f"({plain_english_status_label(entry.status)})")
    return " ".join(parts)


# ── Mapping tables ──
#
# Kept as module-level constants so the test suite can iterate over
# every enum value and confirm a label is defined. Adding a new enum
# variant without an entry here causes a KeyError at lookup AND a
# test failure in test_model_registry.py:test_label_tables_cover_all_enum_values.

_STATUS_LABELS: dict[ModelStatus, str] = {
    ModelStatus.AVAILABLE: "available",
    ModelStatus.DEPRECATED: "deprecated",
    ModelStatus.WITHDRAWN_NO_NEW_DOWNLOADS: "no longer offered for new downloads",
    ModelStatus.LEGALLY_BLOCKED: "legally blocked",
}

_LICENSE_LABELS: dict[LicenseClass, str] = {
    LicenseClass.APACHE_2_0: "Apache 2.0",
    LicenseClass.MIT: "MIT",
    LicenseClass.BSD_3_CLAUSE: "BSD 3-Clause",
    LicenseClass.BOOST_SOFTWARE_LICENSE: "Boost Software License",
    LicenseClass.RESEARCH_NON_COMMERCIAL: "Research / non-commercial",
    LicenseClass.GPL_3_0: "GPL-3.0 (strong copyleft)",
    LicenseClass.AGPL_3_0: "AGPL-3.0 (network-use copyleft)",
    LicenseClass.UNKNOWN: "license unknown",
}

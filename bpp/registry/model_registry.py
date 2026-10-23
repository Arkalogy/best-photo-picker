"""Single source of truth for every ML model BPP touches.

The schema below covers the structured registry, commit permalinks +
acknowledgment snapshots, the multi-status lifecycle, and the
non-warranty field names + split commercial-mode flag. The policy it
encodes is described in ``MODEL_POLICY.md`` and
``docs/adr/0005-legal-posture-and-model-registry.md``.

What this is

  Every ML model BPP either ships permissively-licensed metadata for,
  points at via a first-run download dialog, or accepts via a user-
  supplied path is represented by exactly one :class:`ModelEntry`.
  The entry carries every field downstream code needs to decide how
  to behave: license posture, status, acknowledgment requirements,
  source URLs and snapshot hashes.

Why the field names look the way they do

  An earlier draft used ``commercial_safe: bool``. That phrasing is
  warranty-like — it reads as if Arkalogy is opining that the model is safe for commercial use,
  which is the exact framing we want to avoid. The renamed fields
  describe the underlying facts (does a restriction exist, what did
  upstream claim, what does bppicker permit by default) without
  asserting a legal conclusion. UI label derivation in
  :mod:`bpp.registry.labels` translates the precise field names to
  plain English at presentation time so users see "Commercial use:
  restricted per upstream license," not raw flags.

Status semantics

  ``ModelStatus`` replaces the prior boolean ``withdrawn`` flag.
  Four states distinguish behaviour for new downloads vs existing
  local copies:

  * ``available`` — new download allowed, existing local use allowed.
  * ``deprecated`` — new download warns the user; existing copies
    continue working.
  * ``withdrawn_no_new_downloads`` — no new download; existing local
    copies remain usable with a one-shot notice.
  * ``legally_blocked`` — no new download AND existing local copies
    refuse to load unless the user re-asserts separate rights or
    points at a BYOM file. The strongest state, used when upstream
    sends a takedown or the maintainer learns the model can no
    longer be lawfully redistributed in any form.

  ``status_behavior(status)`` returns the policy as a structured
  :class:`StatusBehavior` so call sites in download, selection and
  startup-scan paths don't have to repeat the table.

Acknowledgment evidence

  Restricted models carry ``ack_text_version`` and ``ack_text_sha256``
  so the click-through dialog and the acceptance log can later prove
  exactly what wording the user saw. The text itself lives in
  versioned constants elsewhere; the registry only stores the
  pointers and hashes that make the wording reproducible.

What lives elsewhere

  Download wiring (:mod:`bpp.utils.download`), the click-through
  dialog (:mod:`bpp.registry.acceptance`), the hard-block
  (:mod:`bpp.registry.policy`), the signed remote registry
  (:mod:`bpp.registry.overlay`) and BYOM (:mod:`bpp.registry.byom`)
  all read from this registry's fields. This module only owns the
  data model + status semantics; built-in entries live in
  :mod:`bpp.registry.builtins`.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum

from bpp.scoring._registry_base import _ScoringRegistry
from bpp.utils.logging import get_logger

_log = get_logger(__name__)


class LicenseClass(StrEnum):
    """SPDX-style identifier for the upstream-claimed license of the
    weights (or other artifact) the entry refers to.

    Stored on the entry; surfaced to users only via
    :func:`bpp.registry.labels.plain_english_license_label` so the
    UI doesn't expose raw enum values.
    """

    APACHE_2_0 = "apache_2_0"
    MIT = "mit"
    BSD_3_CLAUSE = "bsd_3_clause"
    BOOST_SOFTWARE_LICENSE = "boost_software_license"
    RESEARCH_NON_COMMERCIAL = "research_non_commercial"
    #: Strong copyleft. Attaches to derived works distributed externally;
    #: for a locally-installed user who never redistributes, no obligation.
    #: BPP treats these as restricted by default so the user is forced to
    #: acknowledge before downloading the weights.
    GPL_3_0 = "gpl_3_0"
    #: Strong copyleft + network use clause. Distributing modified
    #: software OR providing it as a network service triggers source-code
    #: disclosure. Same restricted-default treatment as GPL.
    AGPL_3_0 = "agpl_3_0"
    UNKNOWN = "unknown"


class ModelStatus(StrEnum):
    """Lifecycle state. Replaces the prior boolean ``withdrawn`` flag.

    The four-state enum lets a legal takedown actually change runtime
    behaviour rather than letting existing local copies continue
    silently. See :func:`status_behavior` for the policy table.
    """

    AVAILABLE = "available"
    DEPRECATED = "deprecated"
    WITHDRAWN_NO_NEW_DOWNLOADS = "withdrawn_no_new_downloads"
    LEGALLY_BLOCKED = "legally_blocked"


@dataclass(frozen=True)
class StatusBehavior:
    """Runtime policy for one :class:`ModelStatus`.

    ``new_download_allowed``: may a user initiate a fresh download?
    ``existing_local_use_allowed``: may already-installed copies be
        loaded by extraction / scoring?
    ``warn_on_use``: should the app show a one-shot notice the next
        time the user touches the model?
    ``requires_rights_assertion_to_use``: must the user re-affirm
        separate-rights before any further use? Only set for
        ``LEGALLY_BLOCKED``.

    Only ``new_download_allowed`` is read by runtime code today (the
    download gate and the model-picker filters). Whether a model may
    load is decided by :func:`bpp.registry.policy.check_model_load_allowed`,
    which is stricter than the two ``existing_local_use`` /
    ``rights_assertion`` columns suggest: ``LEGALLY_BLOCKED`` refuses
    every load with no override, and ``WITHDRAWN_NO_NEW_DOWNLOADS``
    refuses every load of an entry that requires explicit
    acknowledgment, including copies already on disk.
    """

    new_download_allowed: bool
    existing_local_use_allowed: bool
    warn_on_use: bool
    requires_rights_assertion_to_use: bool


_STATUS_BEHAVIOR: dict[ModelStatus, StatusBehavior] = {
    ModelStatus.AVAILABLE: StatusBehavior(
        new_download_allowed=True,
        existing_local_use_allowed=True,
        warn_on_use=False,
        requires_rights_assertion_to_use=False,
    ),
    ModelStatus.DEPRECATED: StatusBehavior(
        new_download_allowed=True,
        existing_local_use_allowed=True,
        warn_on_use=True,
        requires_rights_assertion_to_use=False,
    ),
    ModelStatus.WITHDRAWN_NO_NEW_DOWNLOADS: StatusBehavior(
        new_download_allowed=False,
        existing_local_use_allowed=True,
        warn_on_use=True,
        requires_rights_assertion_to_use=False,
    ),
    ModelStatus.LEGALLY_BLOCKED: StatusBehavior(
        new_download_allowed=False,
        existing_local_use_allowed=False,
        warn_on_use=True,
        requires_rights_assertion_to_use=True,
    ),
}


def status_behavior(status: ModelStatus) -> StatusBehavior:
    """Return the runtime policy for ``status``.

    Encapsulates the status behavior table so call sites in
    download, selection and startup-scan paths share one
    authoritative implementation rather than repeating the matrix.
    """
    return _STATUS_BEHAVIOR[status]


@dataclass(frozen=True)
class ModelEntry:
    """One ML model BPP either ships, points at, or accepts as BYOM.

    Each field's purpose:

    Identity:
        ``id`` — stable, machine-friendly key (e.g. ``"sface_yunet"``).
            Used as the foreign key on the acceptance log and as the
            ``model_id`` tag on derived embeddings/clusters so the
            derived-data purge on removal can find them later.
        ``display_name`` — human-readable title shown in pickers.
        ``kind`` — what surface the model plugs into
            (``"face_embedder"``, ``"face_detector"``, ``"semantic"``,
            ``"detection"``, ``"inpaint"``, etc.). UI groups by kind.

    Provenance:
        ``source_url`` — the model's upstream source: usually the
            publisher's project page, occasionally a direct download
            URL. The URL a loader actually fetches lives in that
            loader's module and can be a re-host (LaMa's weights come
            from the simple-lama-inpainting release assets).
        ``terms_url`` — upstream license / terms page (typically a
            README or LICENSE on GitHub). May change over time.
        ``terms_permalink_url`` — commit-pinned snapshot of
            ``terms_url`` so the acceptance log can refer back to the
            *exact* text the user agreed to. ``None`` only when no
            permalink form exists.
        ``terms_retrieved_at`` — ISO-8601 date the maintainer last
            reviewed the upstream terms. Future drift surfaces in
            the contributor PR review.
        ``license_summary`` — one-line plain-English description used
            by :mod:`bpp.registry.labels` to fill the picker's
            subtitle slot.

    Acknowledgment:
        ``requires_explicit_ack`` — restricted models = ``True``.
            Drives whether the click-through dialog fires before
            first download / use.
        ``ack_text_version`` — versioned identifier for the exact
            acknowledgment wording the user must accept. Stored on
            the acceptance-log row so we can later reproduce what was
            shown.
        ``ack_text_sha256`` — hex SHA-256 of the canonical
            acknowledgment text (canonical disclaimer + model-specific
            extras). Lets the registry and the acceptance log verify
            the same string was shown.

    License classification (non-warranty field names):
        ``upstream_claimed_license_class`` — what upstream says the
            license is. Note "claimed" — we are not opining; we are
            recording the upstream claim.
        ``commercial_use_restriction_known`` — does a known restriction
            exist on commercial use of this artifact? ``True`` when the
            upstream license forbids or restricts commercial use; the
            UI translates this to "Commercial use: restricted" via
            :mod:`bpp.registry.labels`.
        ``bppicker_commercial_default_allowed`` — does BPP, in its
            default configuration, let this model be used in
            self-declared commercial mode? Drives the commercial-use
            hard-block.
        ``commercial_unlock_requires_rights_assertion`` — can a user
            override the default block by asserting separate
            commercial rights? Separated from the previous field so
            "block always" and "block unless user asserts rights"
            cannot collapse into one ambiguous flag.

    Lifecycle:
        ``status`` — see :class:`ModelStatus`.

    Training data + integrity:
        ``training_data`` — short human-readable note ("MS1MV2",
            "WIDER FACE", "OpenCV LFW-based", "unknown / proprietary").
            Used in the picker subtitle and in the acceptance log.
        ``weight_sha256`` — hex SHA-256 of the canonical weight file.
            Verified after first-run download. ``""`` when the entry
            is metadata-only (e.g. BYOM placeholder, deprecated entry
            kept for derived-data lookups).

    Defaults:
        ``default_for_kind`` — is this entry BPP's default selection
            for its ``kind``? Exactly one entry per kind should set
            this to ``True`` (SFace is the default face embedder).
    """

    # Identity
    id: str
    display_name: str
    kind: str

    # Provenance
    source_url: str
    terms_url: str
    terms_permalink_url: str | None
    terms_retrieved_at: str
    license_summary: str

    # Acknowledgment
    requires_explicit_ack: bool
    ack_text_version: str
    ack_text_sha256: str

    # License classification
    upstream_claimed_license_class: LicenseClass
    commercial_use_restriction_known: bool
    bppicker_commercial_default_allowed: bool
    commercial_unlock_requires_rights_assertion: bool

    # Lifecycle
    status: ModelStatus

    # Training data + integrity
    training_data: str
    weight_sha256: str

    # Defaults
    default_for_kind: bool

    # Discriminator for the acknowledgment text family the click-
    # through dialog should render. Two values are recognised
    # today:
    #
    #   "canonical" — restricted third-party model, dialog renders
    #       CANONICAL_DISCLAIMER plus the four required checkboxes
    #       plus the commercial-use definition plus the biometric
    #       responsibility text.
    #
    #   "byom" — user-supplied Bring-Your-Own-Model file. Dialog
    #       renders BYOM_DISCLAIMER plus a single user-responsibility
    #       checkbox. The four restricted-model checkboxes do not
    #       apply because there is no upstream license to gate
    #       against — the user takes the rights chain entirely.
    #
    # Default is "canonical" so existing entries keep their behaviour
    # unchanged. The acceptance flow dispatches on this field.
    ack_text_kind: str = "canonical"

    #: True iff the model produces or consumes biometric data — face
    #: embeddings, facial geometry (landmarks/bbox), iris features,
    #: etc. When True, the click-through dialog renders the biometric
    #: responsibility text (Colorado HB24-1130 / Texas CUBI citations).
    #: When False, the block is suppressed. Examples:
    #:
    #:   * face_embedder: True (the whole point is identification)
    #:   * face_detector: True (facial geometry counts as biometric in
    #:     most jurisdictions even without an identity-resolving
    #:     embedding)
    #:   * pet_detector: False
    #:   * semantic_search (CLIP): False
    #:   * inpainter (LaMa): False
    #:   * nudity_classifier (NudeNet): False
    #:
    #: Defaults to False — new entries must opt in. Mismarking a
    #: face-related entry as False silently suppresses the
    #: biometric-responsibility legal notice and is treated as a
    #: legal-posture regression by the surface-parity tests.
    produces_biometric_data: bool = False

    #: Expected on-disk size of the model's primary downloadable
    #: artifact, in bytes. Surfaced in the Settings → Models picker's
    #: Size column when the model is registered but not yet downloaded
    #: (status "Catalog"), so the user knows how big the download is
    #: BEFORE accepting the click-through and triggering the fetch.
    #:
    #: ``0`` means "size unknown" — picker renders an em-dash. Set on
    #: entries whose download artifact has a stable size known at
    #: registration time (zip releases, ONNX files with pinned SHAs).
    #: For BYOM entries this stays 0 because the file comes from the
    #: user's disk.
    expected_download_size_bytes: int = 0

    #: True for a supporting model that runs together with its kind's
    #: default rather than as a fallback (the BlazeFace detectors run
    #: next to YuNet on every photo). The Settings → Models picker
    #: shows such a row as running, not as a backup.
    runs_alongside_default: bool = False

    #: True when the weights ship inside an installed library (the Haar
    #: cascade inside opencv-python), so they are always on disk and
    #: there is nothing to download.
    ships_with_runtime: bool = False


_REGISTRY: _ScoringRegistry[ModelEntry] = _ScoringRegistry("model entry", _log)


def register_entry(entry: ModelEntry) -> None:
    """Register a :class:`ModelEntry`. Idempotent on ``id``.

    Replacement of an existing entry is intentional — the signed
    remote registry overlays user-fetched updates onto the bundled
    baseline at startup; the replacement path runs here.
    """
    _log.debug(
        "Registered model entry %r kind=%s status=%s license=%s",
        entry.id,
        entry.kind,
        entry.status.value,
        entry.upstream_claimed_license_class.value,
    )
    _REGISTRY.register(entry, entry.id)
    # The license decision depends on the entry (status, ack text), so a
    # replaced entry must be re-checked, not served from a cached gate.
    from bpp.scoring.model_load_gate import reset_gates_for

    reset_gates_for(entry.id)


def get_entry(entry_id: str) -> ModelEntry | None:
    """Return the entry for ``entry_id``, or ``None`` if not registered."""
    return _REGISTRY.get(entry_id)


def list_entries() -> list[ModelEntry]:
    """Return every registered entry in insertion order."""
    return _REGISTRY.list_all()


def iter_entries() -> Iterator[ModelEntry]:
    """Iterator alternative for streaming use cases."""
    return _REGISTRY.iter_all()


def get_default_for_kind(kind: str) -> ModelEntry | None:
    """Return the registered entry that is marked default for ``kind``.

    The default-selection layer reads from here
    rather than inferring "use whichever model loads" from a code path
    that could quietly land on a restricted model. Exactly one entry
    per kind may have ``default_for_kind=True``; multiple defaults are
    a programming error that surfaces via the runtime check below.

    Returns ``None`` when no entry of the given kind is registered or
    none is marked as the default — callers should treat that as "no
    preference recorded" and apply their own fallback.
    """
    defaults: list[ModelEntry] = [
        e for e in _REGISTRY.iter_all() if e.kind == kind and e.default_for_kind
    ]
    if not defaults:
        return None
    if len(defaults) > 1:
        ids = ", ".join(e.id for e in defaults)
        raise RuntimeError(
            f"Multiple registered entries claim default_for_kind=True for "
            f"kind={kind!r}: [{ids}]. Exactly one entry per kind may be the "
            f"default. The most likely cause is a plugin or test that "
            f"registered a second default without clearing the first."
        )
    return defaults[0]


def _reset_registry_for_tests() -> None:
    """Empty the registry. Test-only — never call from production."""
    _REGISTRY._store.clear()

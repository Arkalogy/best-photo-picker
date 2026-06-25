"""Model registry + canonical disclaimer text + UI label derivation.

This package is the single source of truth for every ML model BPP
either ships, points at, or accepts via user-supplied paths. Every
download decision, license-display dialog, Settings panel, CLI prompt,
and README warning reads from here.

Why it's a package rather than a flat module:

* :mod:`model_registry` — the schema (``ModelEntry`` dataclass, status
  enum, license-class enum, registry CRUD).
* :mod:`disclaimers` — the canonical legal disclaimer text. One
  constant. Reused everywhere.
* :mod:`labels` — the field-to-plain-English derivation that
  translates internal registry field names into user-visible UI
  labels (separating data-model field names from UI strings so the
  registry can use precise non-warranty field names without
  surfacing them in the picker).

The registry replaces the earlier ad-hoc license_id field on
:class:`bpp.scoring.face_embedder_registry.FaceEmbedder` (kept for
back-compat). Embedder registration eventually plumbs through here
so the legal posture is enforced at the data-model layer rather than
per UI surface.

Established by Batch 1 of the face-embedder legal-posture rollout —
see ``pm-face-embedder-spike.md`` for the 24-item plan this
implements.
"""

from __future__ import annotations

from bpp.registry.acceptance import (
    AcceptanceDraft,
    AcceptanceError,
    confirm_acceptance,
    is_acceptance_valid_for,
    prepare_acceptance,
    utc_now_iso,
)
from bpp.registry.acceptance_log import (
    SCHEMA_VERSION,
    AcceptanceRow,
    append_revocation,
    find_latest_for_model,
    get_acceptance_log_path,
    has_accepted,
    list_acceptances,
)

# Seed the registry with built-in entries (SFace + dlib) so any
# consumer importing the registry sees the default-for-kind invariant
# satisfied. Imported at the bottom so the registry symbols above are
# already defined when builtins.py runs.
from bpp.registry.builtins import register_builtins
from bpp.registry.byom import (
    BYOMEntry,
    add_byom_entry,
    get_byom_entry,
    get_byom_store_path,
    iter_byom_model_entries,
    list_byom_entries,
    remove_byom_entry,
)
from bpp.registry.derived_data_purge import (
    DerivedDataSummary,
    count_derived_for_model,
    purge_derived_for_model,
)
from bpp.registry.disclaimers import (
    BPP_POSTURE_STATEMENT,
    BYOM_DISCLAIMER,
    BYOM_DISCLAIMER_COMPRESSED,
    BYOM_DISCLAIMER_VERSION,
    CANONICAL_DISCLAIMER,
    CANONICAL_DISCLAIMER_COMPRESSED,
    CANONICAL_DISCLAIMER_VERSION,
    byom_disclaimer_compressed_sha256,
    byom_disclaimer_sha256,
    canonical_disclaimer_compressed_sha256,
    canonical_disclaimer_sha256,
)
from bpp.registry.download_chokepoint import (
    BlockedAutoDownloadError,
    enforce_chokepoint,
    enter_registry_download,
    exit_registry_download,
    install_third_party_interceptions,
)
from bpp.registry.labels import (
    PERMISSIVE_GROUP_SUBTITLE,
    PERMISSIVE_GROUP_TITLE,
    RESTRICTED_GROUP_SUBTITLE,
    RESTRICTED_GROUP_TITLE,
    group_for_entry,
    plain_english_license_label,
    plain_english_status_label,
    ui_label_for_entry,
)
from bpp.registry.model_registry import (
    LicenseClass,
    ModelEntry,
    ModelStatus,
    StatusBehavior,
    get_default_for_kind,
    get_entry,
    iter_entries,
    list_entries,
    register_entry,
    status_behavior,
)
from bpp.registry.overlay import (
    DUAL_SIG_REQUIREMENT,
    OverlayApplicationResult,
    apply_overlay,
)
from bpp.registry.policy import (
    ModelLoadBlockedError,
    ModelLoadDecision,
    PolicyResult,
    assert_no_silent_reclassification,
    check_model_load_allowed,
    enforce_load_policy_for,
    raise_if_blocked,
)
from bpp.registry.remote_registry import (
    ALLOWED_HOSTS,
    DEFAULT_REMOTE_REGISTRY_URL,
    fetch_remote_manifest,
)
from bpp.registry.removal import (
    ModelRemovalError,
    RemovalResult,
    preview_removal,
    remove_model_with_derived_choice,
)
from bpp.registry.signed_manifest import (
    MANIFEST_SCHEMA_VERSION,
    ManifestVerificationError,
    ManifestVerificationResult,
    Signature,
    TrustedKey,
    canonical_manifest_bytes,
    sign_manifest,
    verify_manifest,
)
from bpp.registry.trusted_keys import (
    TRUSTED_KEYS,
    is_all_placeholder_keys,
    trusted_key_list,
)
from bpp.registry.use_context import (
    BIOMETRIC_RESPONSIBILITY_TEXT,
    COMMERCIAL_USE_DEFINITION,
    REQUIRED_ACK_CHECKBOXES,
    SEPARATE_RIGHTS_ASSERTION_TEMPLATE,
    USE_CONTEXT_TEXT_VERSION,
    UseContext,
    use_context_text_sha256,
)
from bpp.registry.use_context_store import (
    UseContextRecord,
    get_use_context,
    get_use_context_path,
    has_been_set,
    read_record,
    set_use_context,
)

register_builtins()

# Install the Batch 3 download chokepoint as soon as the registry
# package loads. Any package already imported by this point gets
# patched; later imports of known auto-downloaders are caught by the
# meta-path post-import hook. Re-imports of bpp.registry are no-ops.
install_third_party_interceptions()


def _try_apply_remote_overlay() -> None:
    """Batch 8 / item 12 + 23 — attempt to fetch + verify + apply
    the remote registry manifest.

    Best-effort: every failure (network, verification, decoder)
    logs at WARNING and falls back to the bundled baseline. The
    function is skipped entirely when the environment variable
    ``BPP_DISABLE_REMOTE_REGISTRY`` is set to a truthy value — useful
    for tests, CI runs, and air-gapped environments where the
    fetch attempt would just be wasted time.
    """
    import json
    import os

    if os.environ.get("BPP_DISABLE_REMOTE_REGISTRY"):
        return
    # If the bundled trust set is still all placeholders (i.e. this
    # checkout has not yet been re-keyed for a real release), the
    # verifier could never accept a remote manifest anyway — every
    # signature would fail the bundled-key check. Skipping the fetch
    # silently in this state avoids the inevitable 404 / verification
    # WARNING firing on every short-lived ``bpp`` CLI invocation.
    if is_all_placeholder_keys():
        return
    try:
        data = fetch_remote_manifest()
    except Exception:
        # Defensive — every expected failure inside fetch is already
        # caught and returns None. This catch handles the
        # genuinely-unexpected path (programming error) without
        # taking down BPP startup.
        from bpp.utils.logging import get_logger as _get_logger

        _get_logger(__name__).warning(
            "Unexpected error fetching remote registry; using bundled baseline only",
            exc_info=True,
        )
        return
    if data is None:
        return
    try:
        manifest = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        from bpp.utils.logging import get_logger as _get_logger

        _get_logger(__name__).warning(
            "Remote registry response is not valid JSON: %s (using bundled baseline)",
            exc,
        )
        return
    result = verify_manifest(manifest, trusted_keys=trusted_key_list())
    if not result.is_valid:
        from bpp.utils.logging import get_logger as _get_logger

        _get_logger(__name__).warning(
            "Remote registry manifest failed verification; using bundled baseline only. Errors: %s",
            result.errors,
        )
        return
    apply_overlay(
        list(result.entries),
        valid_signature_count=len(result.valid_signatures),
    )


_try_apply_remote_overlay()

__all__ = [
    "ALLOWED_HOSTS",
    "BIOMETRIC_RESPONSIBILITY_TEXT",
    "BPP_POSTURE_STATEMENT",
    "BYOM_DISCLAIMER",
    "BYOM_DISCLAIMER_COMPRESSED",
    "BYOM_DISCLAIMER_VERSION",
    "CANONICAL_DISCLAIMER",
    "CANONICAL_DISCLAIMER_COMPRESSED",
    "CANONICAL_DISCLAIMER_VERSION",
    "COMMERCIAL_USE_DEFINITION",
    "DEFAULT_REMOTE_REGISTRY_URL",
    "DUAL_SIG_REQUIREMENT",
    "MANIFEST_SCHEMA_VERSION",
    "PERMISSIVE_GROUP_SUBTITLE",
    "PERMISSIVE_GROUP_TITLE",
    "REQUIRED_ACK_CHECKBOXES",
    "RESTRICTED_GROUP_SUBTITLE",
    "RESTRICTED_GROUP_TITLE",
    "SCHEMA_VERSION",
    "SEPARATE_RIGHTS_ASSERTION_TEMPLATE",
    "TRUSTED_KEYS",
    "USE_CONTEXT_TEXT_VERSION",
    "AcceptanceDraft",
    "AcceptanceError",
    "AcceptanceRow",
    "BYOMEntry",
    "BlockedAutoDownloadError",
    "DerivedDataSummary",
    "LicenseClass",
    "ManifestVerificationError",
    "ManifestVerificationResult",
    "ModelEntry",
    "ModelLoadBlockedError",
    "ModelLoadDecision",
    "ModelRemovalError",
    "ModelStatus",
    "OverlayApplicationResult",
    "PolicyResult",
    "RemovalResult",
    "Signature",
    "StatusBehavior",
    "TrustedKey",
    "UseContext",
    "UseContextRecord",
    "add_byom_entry",
    "append_revocation",
    "apply_overlay",
    "assert_no_silent_reclassification",
    "byom_disclaimer_compressed_sha256",
    "byom_disclaimer_sha256",
    "canonical_disclaimer_compressed_sha256",
    "canonical_disclaimer_sha256",
    "canonical_manifest_bytes",
    "check_model_load_allowed",
    "confirm_acceptance",
    "count_derived_for_model",
    "enforce_chokepoint",
    "enforce_load_policy_for",
    "enter_registry_download",
    "exit_registry_download",
    "fetch_remote_manifest",
    "find_latest_for_model",
    "get_acceptance_log_path",
    "get_byom_entry",
    "get_byom_store_path",
    "get_default_for_kind",
    "get_entry",
    "get_use_context",
    "get_use_context_path",
    "group_for_entry",
    "has_accepted",
    "has_been_set",
    "install_third_party_interceptions",
    "is_acceptance_valid_for",
    "is_all_placeholder_keys",
    "iter_byom_model_entries",
    "iter_entries",
    "list_acceptances",
    "list_byom_entries",
    "list_entries",
    "plain_english_license_label",
    "plain_english_status_label",
    "prepare_acceptance",
    "preview_removal",
    "purge_derived_for_model",
    "raise_if_blocked",
    "read_record",
    "register_entry",
    "remove_byom_entry",
    "remove_model_with_derived_choice",
    "set_use_context",
    "sign_manifest",
    "status_behavior",
    "trusted_key_list",
    "ui_label_for_entry",
    "use_context_text_sha256",
    "utc_now_iso",
    "verify_manifest",
]

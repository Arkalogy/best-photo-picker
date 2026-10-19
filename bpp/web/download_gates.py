"""The refusals every model download runs first.

Shared by the Redownload endpoint (``bp_models``) and the catalog
weights endpoint (``bp_catalog``): a withdrawn or blocked registry
status, or a license the load gate would refuse, stops the download
before any file is touched. Not an endpoint module, so a blueprint
never imports another blueprint for a check.
"""

from __future__ import annotations

from bpp.errors import BppError, ConflictError
from bpp.scoring import model_lifecycle
from bpp.utils.logging import get_logger

log = get_logger(__name__)


def resolve_legal_id(name: str) -> str:
    """The legal-registry id a redownload file belongs to.

    Read from what the scoring module declares when it registers the
    file (``legal_id``, or its ModelSingleton's ``registry_id``) — the
    only record of the link. Fails CLOSED: a file with no declared
    registry entry is refused, so a download can never run ungated.
    """
    from bpp.scoring.model_base import ModelRegistry

    model_lifecycle.ensure_builtin_model_files_registered()
    declared = model_lifecycle.declared_legal_id(ModelRegistry.get(name))
    if declared is None:
        log.error("Redownload refused for %s: it declares no model-registry entry", name)
        raise BppError(
            f"Download refused for {name!r}",
            user_message=(
                f"Can't download {name} from here: its license check isn't wired up. "
                "Use Settings → Models to review and accept the model instead."
            ),
            diagnostic_message=f"resolve_legal_id: {name!r} declares no legal entry",
            name=name,
        )
    return declared


def enforce_download_status_gate(name: str, legal_id: str | None = None) -> None:
    """Refuse the download if the corresponding legal-registry entry's
    ``status`` says new downloads are not allowed.

    Closes the gap where a signed remote-registry
    overlay can flip an entry to ``WITHDRAWN_NO_NEW_DOWNLOADS`` or
    ``LEGALLY_BLOCKED`` but the Redownload button still re-fetches
    silently. Without this gate the signed manifest's takedown power
    is cosmetic — the user can always click Redownload to get the
    file back.

    """
    if legal_id is None:
        legal_id = resolve_legal_id(name)
    from bpp.registry import get_entry, status_behavior

    entry = get_entry(legal_id)
    if entry is None:
        return
    behavior = status_behavior(entry.status)
    if behavior.new_download_allowed:
        return
    log.info(
        "Download refused for %s (legal_id=%s) — status=%s",
        name,
        legal_id,
        entry.status.value,
    )
    raise BppError(
        f"Download refused for {name!r}",
        user_message=(
            f"This model is {entry.status.value.replace('_', ' ')} and new downloads are blocked."
        ),
        diagnostic_message=(
            f"enforce_download_status_gate: legal entry "
            f"{legal_id} has status {entry.status.value} "
            "(new_download_allowed=False)"
        ),
        name=name,
        legal_entry_id=legal_id,
        status=entry.status.value,
    )


def enforce_download_license_gate(name: str, legal_id: str) -> None:
    """Refuse BEFORE touching the existing file when the license gate
    would block the load (not accepted, accepted under an older text,
    or commercial mode without a rights assertion). ``download_file``
    re-checks, but by then the old copy would already be deleted."""
    from bpp.registry import ModelLoadBlockedError, enforce_load_policy_for

    try:
        enforce_load_policy_for(legal_id)
    except ModelLoadBlockedError as exc:
        log.info("Download refused for %s (legal_id=%s): %s", name, legal_id, exc)
        raise ConflictError(
            f"Download refused for {name!r}",
            user_message=(
                f"Accept the license for {name} in Settings → Models first "
                "(or re-accept it if its terms were updated), then download."
            ),
            diagnostic_message=f"enforce_download_license_gate: {exc}",
            name=name,
            legal_entry_id=legal_id,
        ) from exc

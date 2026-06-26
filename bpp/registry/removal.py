"""Model-removal orchestration.

Batch 7 / item 21 of the legal-posture rollout. One entry point
(:func:`remove_model_with_derived_choice`) drives the full
removal flow for both BYOM entries and registered (built-in /
remote-registry) entries. Couples the optional
derived-data purge so the user's "remove and forget" intent
becomes a single atomic operation.

What "remove" means per entry kind

* **BYOM entries** — Drop the row from the BYOM store
  (``~/.config/bpp/byom-models.json``). The user's actual file is
  left on disk; BYOM is a pointer abstraction.
* **Built-in entries** — Cannot be removed in-process (they are
  Python ModelEntry instances seeded by
  ``bpp.registry.builtins``). The removal flow refuses with a
  clear error directing the user to use status transitions in the
  remote registry overlay (Batch 8) when that path lands.

The optional derived-data purge fires for both kinds. Purging is
the default per Q8 ("purge by default, with opt-out"); CLI fails
closed when neither ``--purge-derived`` nor ``--keep-derived`` is
specified.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from bpp.registry.byom import get_byom_entry, remove_byom_entry
from bpp.registry.derived_data_purge import (
    DerivedDataSummary,
    count_derived_for_model,
    purge_derived_for_model,
)
from bpp.registry.model_registry import get_entry
from bpp.utils.logging import get_logger

_log = get_logger(__name__)


class ModelRemovalError(RuntimeError):
    """Raised when a model cannot be removed (e.g. attempting to
    remove a built-in entry, or the model id does not exist)."""


@dataclass(frozen=True)
class RemovalResult:
    """Structured outcome of the removal flow.

    ``model_id`` — what was removed.
    ``entry_kind`` — ``"byom"`` for BYOM entries, ``"built_in"`` for
        registered entries (the latter currently always errors but
        the result type allows future Batch-8 remote-overlay
        entries to be removed cleanly too).
    ``derived_summary`` — the count snapshot at the moment of
        removal. ``purged`` reports whether the derived data was
        actually deleted.
    """

    model_id: str
    entry_kind: str
    derived_summary: DerivedDataSummary
    purged: bool


def preview_removal(model_id: str, conn: sqlite3.Connection) -> DerivedDataSummary:
    """Return the derived-data summary for the removal modal.

    Pure read — no side effects. Drives the confirmation dialog so
    the user sees ``"this will delete N face embeddings across M
    photos"`` before clicking through.
    """
    return count_derived_for_model(model_id, conn)


def remove_model_with_derived_choice(
    model_id: str,
    *,
    purge_derived: bool,
    conn: sqlite3.Connection,
) -> RemovalResult:
    """Remove ``model_id`` and optionally purge derived embeddings.

    ``purge_derived`` is REQUIRED — there is no default. The CLI
    and Flask wrappers fail closed when the caller does not
    specify it explicitly. This matches the legal-posture spec's Q8
    pattern: defaults in the GUI (where the user has just seen the
    confirmation modal), explicit choice in the headless paths.

    Raises :class:`ModelRemovalError` when ``model_id`` is unknown
    or when the entry is a registered built-in that cannot be
    removed in-process. Caller is responsible for committing the
    transaction so the removal + the purge can be bundled into one
    atomic step.
    """
    summary = count_derived_for_model(model_id, conn)
    purged = False
    if purge_derived:
        deleted = purge_derived_for_model(model_id, conn)
        purged = deleted > 0

    # BYOM ids carry the ``byom_`` prefix so the dispatch is
    # unambiguous. Try BYOM first; fall through to built-in lookup
    # only when the id does not match.
    if model_id.startswith("byom_"):
        if get_byom_entry(model_id) is None:
            raise ModelRemovalError(
                f"No BYOM entry with id {model_id!r}. Use "
                "`bpp model byom list` to see registered entries."
            )
        if not remove_byom_entry(model_id):
            # Race: the entry vanished between get and remove. Treat
            # as success — the user wanted it gone, it is gone.
            _log.warning(
                "BYOM entry %s vanished between read and delete; treating removal as successful",
                model_id,
            )
        return RemovalResult(
            model_id=model_id,
            entry_kind="byom",
            derived_summary=summary,
            purged=purged,
        )

    # Built-in / future remote-registry entry.
    if get_entry(model_id) is None:
        raise ModelRemovalError(
            f"No registered entry with id {model_id!r}. Use "
            "`bpp model list` to see registered entries."
        )
    raise ModelRemovalError(
        f"Entry {model_id!r} is a built-in registry entry and cannot "
        "be removed in-process. Built-in entries follow the upstream "
        "lifecycle (Batch 8's signed remote-registry overlay drives "
        "status changes when a model is withdrawn). The derived-data "
        "purge ran successfully if requested, but the registry entry "
        "remains. To stop using a built-in model, set "
        "face_embedding_method to a different value in Settings."
    )

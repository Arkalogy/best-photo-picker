"""Find and remove face_embeddings rows produced by a given model.

Batch 7 / item 21 of the legal-posture rollout. Schema v41 added
``face_embeddings.producing_model_id``; this module is the read /
delete API the model-removal flow uses.

Two functions:

* :func:`count_derived_for_model` — how many face_embeddings rows
  did model X produce? Surfaced by the GUI confirmation modal so
  the user sees ``"removing this will delete N face embeddings
  and M clusters"`` before clicking through.
* :func:`purge_derived_for_model` — actually delete those rows.
  Returns the count of deleted rows so the caller can confirm the
  action matched what the count said.

Both functions skip ``producing_model_id IS NULL`` rows. NULL
means the row was produced before schema v41 and we cannot tell
which model produced it; purging by NULL would wipe every
historical row on first run.

Cluster handling

A face_embeddings row's ``cluster_id`` points at the cluster the
face belongs to. Deleting embeddings does NOT delete clusters
directly — a cluster is a logical grouping computed from the
embeddings, not a stored entity (the ``face_clusters`` table that
existed in earlier schemas was retired by P5b). The cluster-count
in the confirmation modal is computed as the number of distinct
``cluster_id`` values represented by the soon-to-be-deleted rows;
after the purge those clusters either lose members or vanish
entirely depending on whether other models also contributed
faces to them. The next clustering pass reflects the new state.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from bpp.utils.logging import get_logger

_log = get_logger(__name__)


@dataclass(frozen=True)
class DerivedDataSummary:
    """Counts surfaced by :func:`count_derived_for_model` so the
    confirmation modal renders the right numbers.

    ``embeddings`` — face_embeddings rows produced by the model.
    ``distinct_clusters`` — distinct cluster_id values represented
        by those rows. NOT the count of clusters that will be
        deleted (clusters are logical groupings); rather, the count
        of clusters that will lose members.
    ``distinct_photos`` — distinct photos affected. Useful for the
        biometric-privacy framing (Colorado HB24-1130, Texas CUBI)
        so the user sees "5,432 photos will lose face data" rather
        than just a row count.
    """

    embeddings: int
    distinct_clusters: int
    distinct_photos: int


def count_derived_for_model(model_id: str, conn: sqlite3.Connection) -> DerivedDataSummary:
    """Return how many derived rows model ``model_id`` produced.

    Pure read — no writes. Safe to call repeatedly from the
    confirmation-modal preview path.
    """
    if not model_id:
        return DerivedDataSummary(0, 0, 0)
    row = conn.execute(
        "SELECT COUNT(*) AS n_embeddings, "
        "       COUNT(DISTINCT cluster_id) AS n_clusters, "
        "       COUNT(DISTINCT photo_id) AS n_photos "
        "FROM face_embeddings "
        "WHERE producing_model_id = ? AND producing_model_id IS NOT NULL",
        (model_id,),
    ).fetchone()
    if row is None:
        return DerivedDataSummary(0, 0, 0)
    return DerivedDataSummary(
        embeddings=int(row[0] or 0),
        distinct_clusters=int(row[1] or 0),
        distinct_photos=int(row[2] or 0),
    )


def purge_derived_for_model(model_id: str, conn: sqlite3.Connection) -> int:
    """Delete every face_embeddings row produced by ``model_id``.

    Returns the row count deleted. Caller is responsible for
    committing the transaction — keeping the DELETE in the
    caller's transaction lets the model-removal orchestration
    bundle "remove the model" and "purge the derived data" into
    one atomic step.

    Skips ``producing_model_id IS NULL`` rows so a first run after
    the v41 migration does not wipe pre-v41 history.
    """
    if not model_id:
        return 0
    cur = conn.execute(
        "DELETE FROM face_embeddings "
        "WHERE producing_model_id = ? AND producing_model_id IS NOT NULL",
        (model_id,),
    )
    n = cur.rowcount or 0
    if n:
        _log.info(
            "Purged %d face_embeddings rows produced by %s (Batch 7 / item 21)",
            n,
            model_id,
        )
    return n

"""Pure DB helpers shared by the face-cluster mutation endpoints.

These helpers — none of which depend on Flask — live here so they
are testable without spinning up the app, keeping the
bp_faces_manage blueprint thin.

What lives here:
  - face_count synchronisation (per-face / per-cluster / per-photo)
  - identity propagation after a cluster merge
  - distance-based propagation of similar faces into a target cluster
  - cleanup of stale smart_person albums for deleted clusters

These helpers preserve their leading underscore so the `from
bpp.web.bp_faces_manage import _propagate_cluster`-style call sites
in tests + the blueprint itself keep finding them via the re-export
shim below.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

import numpy as np

from bpp.constants import CLUSTER_DISMISSED
from bpp.db.dialect import dialect
from bpp.db.face_embedding_safety import decode_embeddings_filtered
from bpp.utils.logging import get_logger

log = get_logger(__name__)


def sync_face_count(conn: sqlite3.Connection, face_id: int) -> None:
    """Update face_count on the photo owning this face to match actual embeddings."""
    row = conn.execute("SELECT photo_id FROM face_embeddings WHERE id=?", (face_id,)).fetchone()
    if not row:
        return
    sync_face_counts_for_photos(conn, [row["photo_id"]])


def sync_face_counts_for_clusters(conn: sqlite3.Connection, cluster_ids: list[int]) -> None:
    """Update face_count for all photos that had faces in the given clusters."""
    if not cluster_ids:
        return
    ph = ", ".join(["?"] * len(cluster_ids))
    rows = conn.execute(
        f"SELECT DISTINCT photo_id FROM face_embeddings WHERE cluster_id IN ({ph})",
        cluster_ids,
    ).fetchall()
    sync_face_counts_for_photos(conn, [r["photo_id"] for r in rows])


def sync_face_counts_for_photos(conn: sqlite3.Connection, photo_ids: list[int]) -> None:
    """Recount active faces and update face_count for the given photos.

    Single correlated-subquery UPDATE — O(1) round trip regardless of batch size.
    """
    if not photo_ids:
        return
    placeholders = ",".join("?" * len(photo_ids))
    conn.execute(
        f"UPDATE photos SET face_count = ("
        f" SELECT COUNT(*) FROM face_embeddings"
        f" WHERE photo_id = photos.id AND cluster_id >= 0"
        f") WHERE id IN ({placeholders})",
        photo_ids,
    )
    conn.commit()


def propagate_identity_on_merge(conn: sqlite3.Connection, primary_cluster_id: int) -> None:
    """After merge, propagate the primary cluster's identity to all its faces.

    Picks the most-common labeled identity in the primary cluster and
    applies it to any face in that cluster that doesn't already have
    one. No-op if the schema doesn't have the identity column yet
    (pre-migration startup).
    """
    cols = dialect.column_names(conn, "face_embeddings")
    if "identity" not in cols:
        return
    # Majority vote among labeled faces in the primary cluster
    row = conn.execute(
        "SELECT identity, COUNT(*) as cnt FROM face_embeddings "
        "WHERE cluster_id = ? AND identity IS NOT NULL "
        "GROUP BY identity ORDER BY cnt DESC LIMIT 1",
        (primary_cluster_id,),
    ).fetchone()
    if row and row[0]:
        conn.execute(
            "UPDATE face_embeddings SET identity = ? WHERE cluster_id = ? AND identity IS NULL",
            (row[0], primary_cluster_id),
        )


def propagate_cluster(
    conn: sqlite3.Connection,
    ref_embedding: np.ndarray,
    target_cluster_id: int,
    threshold: float,
    exclude_face_ids: set[int] | None = None,
) -> int:
    """Find similar unassigned/unnamed faces and absorb them into ``target_cluster_id``.

    Returns the number of faces moved into the target cluster. Skips
    faces in clusters the user has explicitly named (so a tag for
    "Alex" doesn't get pulled into a different cluster's propagation
    by virtue of embedding similarity).
    """
    # P5b: indexed shadow-column lookup.
    from bpp.db.albums import get_smart_person_cluster_name_map

    protected_clusters: set[Any] = {target_cluster_id}
    for cid, name in get_smart_person_cluster_name_map(conn).items():
        if name and not re.match(r"^Person \d+$", name):
            protected_clusters.add(cid)

    exclude_cids = list(protected_clusters | {CLUSTER_DISMISSED})
    cid_ph = ",".join(["?"] * len(exclude_cids))
    params: list[Any] = list(exclude_cids)

    exclude_clause = ""
    if exclude_face_ids:
        id_ph = ",".join(["?"] * len(exclude_face_ids))
        exclude_clause = f" AND id NOT IN ({id_ph})"
        params.extend(exclude_face_ids)

    cand_filtered = conn.execute(
        "SELECT id, cluster_id, embedding FROM face_embeddings "
        f"WHERE cluster_id NOT IN ({cid_ph}){exclude_clause}",
        params,
    ).fetchall()

    if not cand_filtered:
        return 0

    # Protection A: filter corrupt embedding BLOBs (wrong size,
    # non-finite, zero-norm) BEFORE np.stack — a single bad row
    # crashes the whole endpoint with ValueError. The Jun-2 demo lib
    # incident was exactly this.
    ids, embs = decode_embeddings_filtered(
        ((r["id"], r["embedding"]) for r in cand_filtered),
        where="face_cluster_ops.candidate_matrix",
    )
    if not embs:
        return 0
    matrix = np.stack(embs)
    dists = np.linalg.norm(matrix - ref_embedding, axis=1)
    matches = [ids[i] for i in range(len(ids)) if dists[i] < threshold]
    if matches:
        conn.executemany(
            "UPDATE face_embeddings SET cluster_id=? WHERE id=?",
            [(target_cluster_id, mid) for mid in matches],
        )
        conn.commit()
    return len(matches)


def cleanup_person_albums(conn: sqlite3.Connection, cluster_ids: list[int]) -> None:
    """Remove smart_person albums for the given cluster IDs."""
    if not cluster_ids:
        return
    # P5b: indexed shadow-column lookup. The prior pattern serialized
    # each cluster_id into a rule_json string and matched on equality —
    # works correctly but pays a string-encoding + scan per cluster.
    placeholders = ",".join("?" * len(cluster_ids))
    album_ids = [
        r[0]
        for r in conn.execute(
            f"SELECT id FROM albums WHERE album_type='smart_person' "
            f"AND smart_person_cluster_id IN ({placeholders})",
            list(cluster_ids),
        ).fetchall()
    ]
    if album_ids:
        ph = ",".join("?" * len(album_ids))
        conn.execute(f"DELETE FROM album_photos WHERE album_id IN ({ph})", album_ids)
        conn.execute(f"DELETE FROM albums WHERE id IN ({ph})", album_ids)
    conn.commit()

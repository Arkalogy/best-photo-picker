"""Read-only face cluster queries used by the API surface.

These functions answer "what's in the face cluster table right
now?" and are used by HTTP handlers, status endpoints, and the
analyzer to decide whether to kick off a clustering pass. None of
them mutate state; all are tolerant of a missing or unmigrated
face_embeddings table (returns empty / falsy rather than raising
during early startup).

Re-exported from face_worker so existing imports
(``from bpp.web.face_worker import has_face_data``, etc.) keep
working unchanged.
"""

from __future__ import annotations

import os
import sqlite3
from typing import Any

import numpy as np

from bpp.constants import active_photo_sql
from bpp.db.face_embedding_safety import active_embedding_dim, decode_embedding
from bpp.errors import ResourceExhaustedError as _ResourceExhaustedError
from bpp.scoring.face_cluster import pick_representative
from bpp.utils.logging import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Face embedding load cap — parallel to CLIP_EMBEDDING_MAX_ROWS in bpp/db/clip.py.
#
# Several face-cluster mutation flows (notably api_faces_restore) load all
# unassigned + per-cluster face embeddings into a single np.stack matrix to
# compute centroid + distance math. SFace produces 128-dim float32 embeddings
# (512 bytes per row). Peak memory is matrix + dict + an (N, M) distance
# buffer ≈ 3x the matrix; on a 1M-face library that's ~1.5 GB before the OS
# kills the process.
#
# Knobs (checked in order):
#  1. Per-library DB setting face_max_override = "bypass" — user explicitly
#     enabled the override despite being over the cap. Surfaced in Settings.
#  2. BPP_FACE_MAX_PHOTOS env var — raises / lowers the numeric cap at
#     server-start time. CLI / dev users get this path; Tauri users get
#     the DB override.
#  3. Default 500_000 — comfortable on a 16 GB machine (~750 MB peak).
# ---------------------------------------------------------------------------
SFACE_EMBEDDING_BYTES = 128 * 4  # 128-dim float32

_face_env_max = os.environ.get("BPP_FACE_MAX_PHOTOS", "")
FACE_EMBEDDINGS_MAX_ROWS: int = int(_face_env_max) if _face_env_max.isdigit() else 500_000

FACE_MAX_OVERRIDE_KEY = "face_max_override"
FACE_MAX_OVERRIDE_BYPASS = "bypass"


def _face_cap_overridden(conn: sqlite3.Connection) -> bool:
    """Return True when the per-library override is set to 'bypass'.

    Imported lazily to avoid a circular import between this module and
    bpp.db.settings.
    """
    from bpp.db.settings import get_setting

    return get_setting(conn, FACE_MAX_OVERRIDE_KEY) == FACE_MAX_OVERRIDE_BYPASS


class FaceEmbeddingsTooLarge(_ResourceExhaustedError, RuntimeError):
    """Raised when a face-embedding load would exceed FACE_EMBEDDINGS_MAX_ROWS.

    Surfaces count + cap so callers can return a user-friendly error
    (typically 503 + descriptive message → in-app toast) instead of
    OOM-killing the server.

    Inherits :class:`ResourceExhaustedError` so the 503 envelope is
    automatic; also inherits ``RuntimeError`` so existing
    ``except RuntimeError`` catches keep working.
    """

    code = "face_embeddings_too_large"

    def __init__(self, count: int, cap: int) -> None:
        self.count = count
        self.cap = cap
        # Peak is ~3x the embedding matrix (matrix + dict + distance buffer).
        dict_mb = count * SFACE_EMBEDDING_BYTES / (1024 * 1024)
        peak_mb = dict_mb * 3
        super().__init__(
            f"Face embedding load: {count} rows, above the {cap} cap. "
            f"Peak would be ~{peak_mb:.0f} MB (matrix + dict + distance buffer "
            f"≈ 3x {dict_mb:.0f} MB). Refusing to load to avoid OOM. "
            f"Override in Settings → Faces, or raise BPP_FACE_MAX_PHOTOS."
        )

    def __reduce__(self) -> tuple:
        """Make this subclass survive pickle across the
        subprocess boundary.

        Default ``Exception.__reduce__`` invokes ``cls(*self.args)``
        where ``self.args == (message,)`` — that's a TypeError here
        because ``__init__`` requires ``(count, cap)``. The subprocess
        runner uses ``multiprocessing.Queue`` to ship exceptions back
        to the parent on a fatal_error message; without ``__reduce__``,
        a raised ``FaceEmbeddingsTooLarge`` becomes a queue-side
        TypeError and the parent sees an opaque crash instead of the
        actionable 503 envelope.
        """
        return (self.__class__, (self.count, self.cap))


def assert_face_load_cap(conn: sqlite3.Connection, count: int) -> None:
    """Raise FaceEmbeddingsTooLarge if count exceeds the cap and no override is set."""
    if count > FACE_EMBEDDINGS_MAX_ROWS and not _face_cap_overridden(conn):
        raise FaceEmbeddingsTooLarge(count, FACE_EMBEDDINGS_MAX_ROWS)


def load_face_cluster_map(conn: sqlite3.Connection) -> dict[str, list[int]]:
    """Load filepath → [cluster_ids] mapping from face_embeddings."""
    try:
        rows = conn.execute(
            "SELECT p.filepath, fe.cluster_id "
            "FROM face_embeddings fe "
            "JOIN photos p ON p.id = fe.photo_id "
            "WHERE fe.cluster_id >= 0"
        ).fetchall()
    except sqlite3.OperationalError as e:
        log.debug("face_embeddings table not ready, skipping: %s", e)
        return {}

    result: dict[str, list[int]] = {}
    for fp, cid in rows:
        result.setdefault(fp, []).append(cid)
    return result


def load_face_clusters(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Load clustered face data shaped for the API response.

    Returns one entry per cluster_id with face/photo counts plus a
    representative face: the best-quality face when the cluster has
    quality scores (the same pick the Groups view makes), otherwise the
    cluster-level pick_representative heuristic over the embeddings.
    Only that fallback decodes embeddings, so the People list is counts
    and paths for a library whose faces carry quality scores. Rows whose
    stored embedding has the wrong byte size (the one corruption that
    has happened: another model's dimension mixed in) are left out of
    the counts in SQL, as the decoding readers leave them out.
    Manually tagged photos are merged into their target cluster's
    filepath set so the response reflects the user's tagging.
    """
    try:
        blob_len = active_embedding_dim(conn) * 4  # float32
        rows = conn.execute(
            "SELECT p.filepath, fe.face_index, fe.bbox_x, fe.bbox_y, fe.bbox_w, fe.bbox_h, "
            "fe.cluster_id, fe.quality FROM face_embeddings fe "
            "JOIN photos p ON p.id = fe.photo_id "
            f"WHERE fe.cluster_id >= 0 AND {active_photo_sql('p')} "
            "AND length(fe.embedding) = ? "
            "ORDER BY fe.cluster_id",
            (blob_len,),
        ).fetchall()
    except sqlite3.OperationalError as e:
        log.debug("face_embeddings table not ready, skipping: %s", e)
        return []

    if not rows:
        return []

    clusters: dict[int, list[dict]] = {}
    for fp, fi, bx, by, bw, bh, cid, quality in rows:
        clusters.setdefault(cid, []).append(
            {"filepath": fp, "face_index": fi, "bbox": (bx, by, bw, bh), "quality": quality}
        )

    # Manual person tags
    try:
        tag_rows = conn.execute(
            "SELECT p.filepath, pt.cluster_id "
            "FROM photo_person_tags pt "
            "JOIN photos p ON p.id = pt.photo_id "
            "WHERE p.deleted_at IS NULL"
        ).fetchall()
    except sqlite3.OperationalError as e:
        log.debug("photo_person_tags table not ready, skipping: %s", e)
        tag_rows = []
    tagged: dict[int, set[str]] = {}
    for fp, cid in tag_rows:
        tagged.setdefault(cid, set()).add(fp)

    unscored = [cid for cid, faces in clusters.items() if all(f["quality"] is None for f in faces)]
    centroid_pick = _representatives_by_centroid(conn, unscored)

    result = []
    for cid, faces in clusters.items():
        if cid in centroid_pick:
            rep = next(
                (f for f in faces if (f["filepath"], f["face_index"]) == centroid_pick[cid]),
                faces[0],
            )
        else:
            rep = max(faces, key=lambda f: f["quality"] if f["quality"] is not None else -1.0)
        filepaths = {f["filepath"] for f in faces}
        if cid in tagged:
            filepaths |= tagged[cid]
        result.append(
            {
                "cluster_id": cid,
                "face_count": len(faces),
                "photo_count": len(filepaths),
                "representative": {
                    "filepath": rep["filepath"],
                    "face_index": rep["face_index"],
                    "bbox": rep["bbox"],
                },
                "filepaths": list(filepaths),
            }
        )

    # Most frequent person first
    result.sort(key=lambda c: -c["photo_count"])
    return result


def _representatives_by_centroid(
    conn: sqlite3.Connection, cluster_ids: list[int]
) -> dict[int, tuple[str, int]]:
    """``{cluster_id: (filepath, face_index)}`` of the face nearest each
    cluster's centroid, for clusters without quality scores. Decodes only
    those clusters' embeddings."""
    if not cluster_ids:
        return {}
    from bpp.db.sql_batches import in_batches

    dim = active_embedding_dim(conn)
    faces: dict[int, list[tuple[str, int, np.ndarray]]] = {}
    for ph, batch in in_batches(cluster_ids):
        rows = conn.execute(
            "SELECT p.filepath, fe.face_index, fe.embedding, fe.cluster_id "
            "FROM face_embeddings fe JOIN photos p ON p.id = fe.photo_id "
            f"WHERE fe.cluster_id IN ({ph}) AND {active_photo_sql('p')}",
            batch,
        ).fetchall()
        for fp, fi, blob, cid in rows:
            # Read-side decode defense: a corrupt BLOB (wrong size, non-finite,
            # zero-norm) is skipped instead of crashing the People panel.
            emb = decode_embedding(blob, where="face_queries.fetch_clusters", dim=dim)
            if emb is not None:
                faces.setdefault(cid, []).append((fp, fi, emb))
    picks: dict[int, tuple[str, int]] = {}
    for cid, items in faces.items():
        idx = pick_representative([e for _fp, _fi, e in items])
        picks[cid] = (items[idx][0], items[idx][1])
    return picks


def has_face_data(conn: sqlite3.Connection) -> bool:
    """Whether any face embedding rows exist."""
    try:
        row = conn.execute("SELECT COUNT(*) FROM face_embeddings").fetchone()
        return row is not None and row[0] > 0
    except sqlite3.OperationalError as e:
        log.debug("face_embeddings table not ready, skipping: %s", e)
        return False


def count_stale_faces(conn: sqlite3.Connection) -> int:
    """Embeddings with NULL quality (legacy rows that need re-extraction)."""
    try:
        row = conn.execute("SELECT COUNT(*) FROM face_embeddings WHERE quality IS NULL").fetchone()
        return row[0] if row else 0
    except sqlite3.OperationalError:
        return 0


def needs_face_clustering(conn: sqlite3.Connection) -> bool:
    """Embeddings exist but none have been clustered yet."""
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN cluster_id >= 0 THEN 1 ELSE 0 END) AS clustered "
            "FROM face_embeddings"
        ).fetchone()
        if not row:
            return False
        total, clustered = row[0], row[1] or 0
        return total > 0 and clustered == 0
    except sqlite3.OperationalError as e:
        log.debug("face_embeddings table not ready, skipping: %s", e)
        return False

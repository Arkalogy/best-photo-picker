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

from bpp.constants import active_photo_sql
from bpp.db.face_embedding_safety import decode_embedding
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

    P7: inherits :class:`ResourceExhaustedError` so the 503 envelope
    is automatic; also inherits ``RuntimeError`` so pre-P7
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
        """T1.5: make this subclass survive pickle across the
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
    representative face (chosen by quality if quality scores exist,
    otherwise by the cluster-level pick_representative heuristic).
    Manually tagged photos are merged into their target cluster's
    filepath set so the response reflects the user's tagging.
    """
    try:
        rows = conn.execute(
            "SELECT p.filepath, fe.face_index, fe.bbox_x, fe.bbox_y, fe.bbox_w, fe.bbox_h, "
            "fe.embedding, fe.cluster_id, fe.quality FROM face_embeddings fe "
            "JOIN photos p ON p.id = fe.photo_id "
            f"WHERE fe.cluster_id >= 0 AND {active_photo_sql('p')} "
            "ORDER BY fe.cluster_id"
        ).fetchall()
    except sqlite3.OperationalError as e:
        log.debug("face_embeddings table not ready, skipping: %s", e)
        return []

    if not rows:
        return []

    clusters: dict[int, list[dict]] = {}
    for fp, fi, bx, by, bw, bh, emb_blob, cid, quality in rows:
        # Protection A: skip rows whose stored BLOB is corrupt (wrong
        # size, non-finite, zero-norm). Without this guard a single
        # bad row propagated into np.stack callers and crashed the
        # whole faces/clusters endpoint (Jun-2 demo lib incident).
        emb = decode_embedding(emb_blob, where="face_queries.fetch_clusters")
        if emb is None:
            continue
        clusters.setdefault(cid, []).append(
            {
                "filepath": fp,
                "face_index": fi,
                "bbox": (bx, by, bw, bh),
                "embedding": emb,
                "quality": quality,
            }
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

    result = []
    for cid, faces in clusters.items():
        embeddings = [f["embedding"] for f in faces]
        qualities = [f.get("quality") for f in faces]
        if any(q is not None for q in qualities):
            rep_idx = pick_representative(embeddings, qualities=qualities)
        else:
            rep_idx = pick_representative(embeddings)
        rep = faces[rep_idx]
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

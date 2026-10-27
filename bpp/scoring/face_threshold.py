"""The face grouping distance for the library's stored faces.

Each embedder spreads faces differently, so a distance that groups well
for one merges strangers for another. The value is therefore kept per
embedder: the user's own pick (saved by Regroup) for the embedder that
made the stored faces, or that embedder's measured default
(``FaceEmbedder.cluster_threshold``). Picking another model in Settings
changes nothing here until an analysis has re-made the faces with it, and
a switch never inherits the old model's pick.
"""

from __future__ import annotations

import sqlite3

from bpp.constants import FACE_CLUSTER_THRESHOLD_FALLBACK
from bpp.db.settings import set_setting

#: Range the Regroup slider offers; a saved value outside it is ignored.
THRESHOLD_MIN = 0.3
THRESHOLD_MAX = 1.2


#: Recorded by analysis when it makes faces (bpp.web.face_method_reconcile).
_EXTRACTED_METHOD_KEY = "face_embedding_method_extracted"


def _setting(conn: sqlite3.Connection, key: str) -> str | None:
    # By position: callers hand plain and Row-factory connections alike.
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _producer_method(conn: sqlite3.Connection) -> str | None:
    """The embedder named by most stored faces' producing model (rows from
    before that column carry none)."""
    from bpp.scoring.face_embedder_registry import list_embedders

    row = conn.execute(
        "SELECT producing_model_id FROM face_embeddings WHERE producing_model_id IS NOT NULL"
        " GROUP BY producing_model_id ORDER BY COUNT(*) DESC LIMIT 1"
    ).fetchone()
    if not row:
        return None
    by_registry = {e.registry_id: e.name for e in list_embedders()}
    return by_registry.get(row[0], row[0])


def faces_method(conn: sqlite3.Connection) -> str:
    """The embedder that made the library's stored faces: the one analysis
    recorded; for a library from before that record, the producer the
    faces carry, else the active one (``remember_faces_method`` pins it
    before a model pick can change that). Never the model picked for the
    next analysis."""
    recorded = _setting(conn, _EXTRACTED_METHOD_KEY)
    if recorded:
        return recorded
    try:
        producer = _producer_method(conn)
    except sqlite3.OperationalError:
        producer = None  # a schema without the column
    if producer:
        return producer
    from bpp.scoring.face_embed import embedding_method

    return embedding_method(conn)


def remember_faces_method(conn: sqlite3.Connection) -> None:
    """Before the picked model changes: record which embedder made the
    stored faces, if nothing recorded it yet (a library last analysed by an
    older version), so their grouping distance does not follow the pick
    before analysis has re-made them. Does NOT commit."""
    if _setting(conn, _EXTRACTED_METHOD_KEY):
        return
    if not conn.execute("SELECT 1 FROM face_embeddings LIMIT 1").fetchone():
        return  # no faces: the next analysis records its own
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        (_EXTRACTED_METHOD_KEY, faces_method(conn)),
    )


def _key(method: str) -> str:
    return f"face_cluster_threshold.{method}"


def default_threshold(method: str) -> float:
    """The embedder's measured default (the shared fallback for unknown ones)."""
    from bpp.scoring.face_embedder_registry import get_embedder

    embedder = get_embedder(method)
    return embedder.cluster_threshold if embedder is not None else FACE_CLUSTER_THRESHOLD_FALLBACK


def _saved(conn: sqlite3.Connection, method: str) -> float | None:
    raw = _setting(conn, _key(method))
    try:
        value = float(raw) if raw is not None else None
    except ValueError:
        return None
    if value is None or not THRESHOLD_MIN <= value <= THRESHOLD_MAX:
        return None
    return value


def face_threshold(conn: sqlite3.Connection, method: str | None = None) -> float:
    """Grouping distance for ``method`` (default: the stored faces' embedder)."""
    if method is None:
        method = faces_method(conn)
    saved = _saved(conn, method)
    return saved if saved is not None else default_threshold(method)


def save_face_threshold(conn: sqlite3.Connection, value: float, method: str | None = None) -> None:
    """Remember the user's pick for ``method`` (default: the stored faces' embedder)."""
    if method is None:
        method = faces_method(conn)
    set_setting(conn, _key(method), value)

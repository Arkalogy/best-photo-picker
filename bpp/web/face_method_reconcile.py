"""Phase 1 of face extraction: notice a face-model change and clear the
old model's rows.

Split out of :mod:`bpp.web.face_extraction_phases` for the 500-LOC cap;
that module re-exports :func:`reconcile_method`.

A change is read from what the rows were actually made with (the
recorded extracted method and the rows' producer tags), never from the
``face_embedding_method`` setting alone: Settings saves the user's pick
before analysis runs. Without a change, only rows of the wrong size are
removed — a size mismatch alone never clears a library's faces.
"""

from __future__ import annotations

import sqlite3

from bpp.utils.logging import get_logger

log = get_logger(__name__)


#: Settings key recording the method the stored rows were made with.
#: ``face_embedding_method`` is the user's pick, saved by Settings
#: before analysis runs, so it can't tell whether the rows match it.
EXTRACTED_METHOD_KEY = "face_embedding_method_extracted"


def _tags_of_other_embedders(current_method: str) -> list[str]:
    """Producer tags (registry ids and, for older rows, bare method
    names) of every known embedder except ``current_method``."""
    from bpp.scoring.face_embedder_registry import list_embedders

    own = {current_method}
    tags: list[str] = []
    for e in list_embedders():
        if e.name == current_method:
            own.add(e.registry_id)
    for e in list_embedders():
        tags += [t for t in (e.name, e.registry_id) if t not in own]
    return tags


def _has_column(conn: sqlite3.Connection, column: str) -> bool:
    return column in {r[1] for r in conn.execute("PRAGMA table_info(face_embeddings)")}


def _rows_from_another_embedder(
    conn: sqlite3.Connection, current_method: str, *, judge_untagged: bool
) -> bool:
    """True when stored rows were evidently made by another embedder:
    some row carries another known embedder's tag, or (``judge_untagged``,
    for libraries that never recorded their extracted method) the
    library's usual row size doesn't fit ``current_method``."""
    from bpp.scoring.face_embedder_registry import embedding_dim_for

    nbytes = embedding_dim_for(current_method) * 4
    if not _has_column(conn, "producing_model_id"):
        return judge_untagged and _usual_size(conn, "") not in (None, nbytes)
    others = _tags_of_other_embedders(current_method)
    if others and _first(
        conn,
        "SELECT 1 FROM face_embeddings WHERE producing_model_id IN "
        f"({','.join('?' * len(others))}) LIMIT 1",
        tuple(others),
    ):
        return True
    # The library's usual row size decides: a few wrong-size rows are
    # debris (removed on their own), not a model switch.
    return judge_untagged and _usual_size(conn, "") not in (None, nbytes)


def _usual_size(conn: sqlite3.Connection, where: str) -> int | None:
    """Most common embedding byte length among the selected rows."""
    row = conn.execute(
        f"SELECT length(embedding) FROM face_embeddings {where} "
        "GROUP BY length(embedding) ORDER BY COUNT(*) DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else None


def _first(conn: sqlite3.Connection, sql: str, params: tuple) -> bool:
    return conn.execute(sql, params).fetchone() is not None


def _delete_misfit_rows(conn: sqlite3.Connection, current_method: str) -> int:
    """Delete only the rows that can't belong to ``current_method``'s
    embeddings (wrong size — corrupt debris or a stray hand-drawn face);
    those photos get re-extracted. Never the whole table."""
    from bpp.scoring.face_embedder_registry import embedding_dim_for

    cur = conn.execute(
        "DELETE FROM face_embeddings WHERE length(embedding) != ?",
        (embedding_dim_for(current_method) * 4,),
    )
    n = cur.rowcount or 0
    if n:
        log.warning("Removed %d face row(s) of the wrong size for %s", n, current_method)
    return n


def detect_model_switch(conn: sqlite3.Connection, current_method: str) -> tuple[str | None, bool]:
    """``(stored_method, switching)`` — whether analysing with
    ``current_method`` would clear the existing faces. The one test both
    the wipe and the pre-wipe "can the new model run?" refusal use."""
    try:
        # Libraries analysed before the extracted-method key existed fall
        # back to the setting (the rows check below covers what that misses).
        stored_method = None
        for key in (EXTRACTED_METHOD_KEY, "face_embedding_method"):
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            if row and row[0]:
                stored_method = row[0]
                break
    except Exception:
        log.warning("Failed to read the extracted face embedding method", exc_info=True)
        stored_method = None
    extracted_recorded = (
        conn.execute("SELECT 1 FROM settings WHERE key=?", (EXTRACTED_METHOD_KEY,)).fetchone()
        is not None
    )
    switching = (
        bool(stored_method) and stored_method != current_method
    ) or _rows_from_another_embedder(conn, current_method, judge_untagged=not extracted_recorded)
    return stored_method, switching


def reconcile_method(
    conn: sqlite3.Connection,
    current_method: str,
) -> tuple[str | None, bool, dict[int, set[int]] | None]:
    """Phase 1: detect an embedder change and wipe if needed.

    Returns ``(stored_method, method_changed, old_cluster_photos)``.
    ``stored_method`` is the method the rows were last extracted with.
    ``old_cluster_photos`` is captured only when the method changed (the
    only case in which the wipe destroys the cluster→photo mapping that
    the identity-writer phase needs to remap names against). The
    preloader phase also captures it conditionally on stale-photo
    presence; the orchestrator merges the two.

    A change is detected from the recorded extracted method and the
    rows' producer tags, not from ``face_embedding_method`` — the picker
    saves that before analysis, so it already equals ``current_method``.
    Without a change, only rows of the wrong size are removed (debris);
    the table is never wiped for a size mismatch alone.

    Side effects: ``DELETE FROM face_embeddings`` if the method changed,
    with the person names parked in a pending record in the same
    transaction (``face_name_placement.begin_switch``); records
    ``current_method`` as both the setting and the extracted method.
    """
    stored_method, method_changed = detect_model_switch(conn, current_method)
    old_cluster_photos: dict[int, set[int]] | None = None
    if not method_changed:
        _delete_misfit_rows(conn, current_method)
    if method_changed:
        log.info(
            "Embedding method changed (%s → %s) — clearing old embeddings",
            stored_method or "earlier rows",
            current_method,
        )
        from bpp.db.face_name_placement import begin_switch
        from bpp.web.face_extraction_phases import _snapshot_cluster_photos

        old_cluster_photos = _snapshot_cluster_photos(conn)
    try:
        if method_changed:
            # The names are parked in the same transaction as the wipe: a
            # crash between the two cannot lose them, and the end of the
            # run places them from this record, not from memory.
            begin_switch(conn, current_method, old_cluster_photos or {})
            conn.execute("DELETE FROM face_embeddings")
        conn.executemany(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            [("face_embedding_method", current_method), (EXTRACTED_METHOD_KEY, current_method)],
        )
        conn.commit()
    except BaseException:
        conn.rollback()  # never a half-done park-and-wipe on the connection
        raise
    return stored_method, method_changed, old_cluster_photos

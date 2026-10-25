"""The end of a face-model switch: every name is placed from the complete
clusters of the finished run (:func:`~bpp.db.face_name_placement.place_final`),
the names nothing matched are kept as people without faces (their original
photos on the album, their manual tags stashed), and the person albums are
rebuilt. Split out of :mod:`bpp.db.face_name_placement` for the 500-LOC
cap; that module re-exports :func:`finish_switch`."""

from __future__ import annotations

import sqlite3

from bpp.constants import CLUSTER_UNPLACED
from bpp.db.face_name_placement import (
    _UNPLACED,
    _person_album_rows,
    _set_album_cluster,
    place_final,
)
from bpp.db.face_switch_record import (
    PendingNames,
    _save_pending,
    alive_photo_ids,
    hold_albums,
    load_pending,
    set_album_evidence,
    stash_unplaced_tags,
    uncovered_photos,
)
from bpp.utils.logging import get_logger

log = get_logger(__name__)


def finish_switch(
    conn: sqlite3.Connection, new_cluster_photos: dict[int, set[int]] | None = None
) -> int:
    """End the pending switch. With ``new_cluster_photos`` (every batch of
    the run succeeded) the names are placed from those complete clusters
    and the person albums rebuilt; without (recovery gave up) nothing is
    placed. Either way the rest are kept as people without faces
    (``CLUSTER_UNPLACED``) for the smart-album refresh to place by photo
    overlap when their photos appear, reported (one warning each, from the
    server process), and the record is cleared. One transaction: a failure
    rolls it all back and the switch stays pending. Returns how many names
    stayed unplaced."""
    pending = load_pending(conn)
    if pending is None:
        return 0
    if new_cluster_photos is not None:
        waiting = uncovered_photos(conn, pending)
        if waiting:
            # A run over part of the library (one folder): the rest has not
            # been processed under the new model yet, so the names wait.
            log.info(
                "Face-model switch continues: %d photo(s) not processed yet; names stay parked",
                len(waiting),
            )
            return 0
    try:
        if new_cluster_photos is not None:
            place_final(conn, pending, new_cluster_photos)
        unplaced = _keep_unplaced(conn, pending)
        _keep_nameless_tags(conn, pending)
        _save_pending(conn, None)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    # Reported only once it is true (the activity feed shows these).
    reason = f"the face-model switch to {pending.to_method}"
    for name in sorted({*pending.dropped, *unplaced.values()}):
        log.warning(_UNPLACED, name, reason)
    if new_cluster_photos is not None:  # a completed run, not a give-up
        _rebuild_person_albums(conn)
    return len(unplaced)


def finish_switch_if_nothing_to_extract(conn: sqlite3.Connection) -> None:
    """A run found nothing to extract: if a face-model switch is pending
    and no photo in the library has a face to extract, the switch ends
    (names kept, guard lifted); otherwise the face extraction that covers
    those photos ends it. Best-effort, like the run's own finish."""
    from bpp.db.face_switch_record import switch_pending

    try:
        if not switch_pending(conn):
            return
        # The photos a face run is given count (not hidden ones, missing
        # files or Live Photo sidecars, which it does not process).
        has_faces = conn.execute(
            "SELECT 1 FROM photos WHERE COALESCE(face_count, 0) > 0 AND deleted_at IS NULL "
            "AND hidden_at IS NULL AND missing = 0 AND is_live_photo_sidecar = 0 LIMIT 1"
        ).fetchone()
        if has_faces is None:
            finish_switch(conn, {})
    except Exception:
        log.warning("Could not end the face-model switch after an empty run", exc_info=True)


def _keep_unplaced(conn: sqlite3.Connection, pending: PendingNames) -> dict:
    """Keep the names nothing matched as people without faces; returns them."""
    unplaced = pending.unplaced_names()
    alive_albums = {aid for aid, _name, _cid in _person_album_rows(conn)}
    kept = {k: pending.albums[k] for k in unplaced if pending.albums.get(k) in alive_albums}
    _set_album_cluster(conn, [(aid, CLUSTER_UNPLACED) for aid in kept.values()])
    # Their manual tags wait on the album until the refresh places it.
    stash_unplaced_tags(
        conn, {aid: pending.tags[k] for k, aid in kept.items() if pending.tags.get(k)}
    )
    # Their album lists their ORIGINAL evidence (their face photos, or their
    # tagged photos for a person known by tags alone, as the placement
    # matched on), so the refresh places them by it and by nothing else.
    photos = (
        alive_photo_ids(
            conn, set().union(*(pending.evidence(k) | pending.tags.get(k, set()) for k in kept))
        )
        if kept
        else set()
    )
    set_album_evidence(
        conn,
        {
            aid: (
                pending.old_cluster_photos[k] & pending.solo & photos
                if pending.old_cluster_photos.get(k)
                else pending.tags.get(k, set()) & photos
            )
            for k, aid in kept.items()
        },
    )
    # Kept back because a photo of theirs could not be read: no automatic
    # placement (the refresh included) until that photo has a face again.
    hold_albums(conn, {aid: pending.evidence(k) & pending.failed for k, aid in kept.items()})
    for k, name in unplaced.items():
        if k not in kept and pending.tags.get(k):
            log.info(
                "'%s' was deleted during the switch; its %d manual tag(s) go with it",
                name,
                len(pending.tags[k]),
            )
    return unplaced


def _keep_nameless_tags(conn: sqlite3.Connection, pending: PendingNames) -> None:
    """Manual tags of a cluster nobody named that matched nothing (or a
    give-up, where nothing was matched): kept as a tag-only person on a
    fresh cluster id with a default album, never dropped."""
    from bpp.db.face_cluster_ids import next_cluster_id
    from bpp.db.face_name_placement import _rule

    nameless = {k: p for k, p in pending.unplaced_tags().items() if k not in pending.names}
    alive = alive_photo_ids(conn, set().union(*nameless.values())) if nameless else set()
    nameless = {k: p & alive for k, p in nameless.items() if p & alive}
    if not nameless:
        return
    first = next_cluster_id(conn)
    fresh = {k: first + i for i, k in enumerate(sorted(nameless, key=str))}
    conn.executemany(
        "INSERT OR IGNORE INTO photo_person_tags (photo_id, cluster_id) VALUES (?, ?)",
        [(pid, fresh[k]) for k, pids in nameless.items() for pid in sorted(pids)],
    )
    conn.executemany(
        "INSERT INTO albums (name, album_type, rule_json) VALUES (?, 'smart_person', ?)",
        [(f"Person {cid + 1}", _rule(cid)) for cid in fresh.values()],
    )
    log.info("Kept the manual tags of %d unnamed cluster(s) on new ids", len(nameless))


def _rebuild_person_albums(conn: sqlite3.Connection) -> None:
    """Rebuild the person albums for the final placement on the server, so
    their contents are right whether or not a browser is still listening.
    Best-effort: the names and tags are already committed, and the next
    refresh repairs the lists; a failure rolls its partial writes back so
    no later commit on this connection picks them up."""
    try:
        from bpp.db.smart_albums import get_affected_album_types, refresh_smart_albums

        refresh_smart_albums(conn, kinds=get_affected_album_types("face_cluster"))
        conn.commit()
    except Exception:
        conn.rollback()
        log.warning(
            "Could not refresh the person albums after the face-model switch", exc_info=True
        )

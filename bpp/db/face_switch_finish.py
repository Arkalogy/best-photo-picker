"""The end of a face-model switch: every name is placed from the complete
clusters of the finished run (:func:`~bpp.db.face_name_placement.place_final`),
the names nothing matched are kept as people without faces (their original
photos on the album, their manual tags stashed), and the person albums are
rebuilt. Split out of :mod:`bpp.db.face_name_placement` for the 500-LOC
cap; that module re-exports :func:`finish_switch`."""

from __future__ import annotations

import sqlite3

from bpp.db.face_name_placement import _UNPLACED, _person_album_rows
from bpp.db.face_switch_record import (
    PendingNames,
    _save_pending,
    alive_photo_ids,
    held_albums,
    load_pending,
    switch_pending,
    uncovered_photos,
)
from bpp.db.people_placement import Person, keep_nameless_on_fresh_ids, place_people
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
        if switch_pending(conn):
            # A record that cannot be read would keep face edits locked for
            # good: drop it (the people stay parked, kept by their albums).
            log.warning("Clearing a damaged face-model switch record")
            _save_pending(conn, None)
            conn.commit()
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
        people = _people_of(conn, pending)
        alive_albums = {aid for aid, _n, _c in _person_album_rows(conn)}
        deleted_people = [p for p in people if not p.nameless and p.album_id not in alive_albums]
        outcome = place_people(conn, people, new_cluster_photos or {})
        keep_nameless_on_fresh_ids(conn, outcome.nameless)
        _save_pending(conn, None)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    for p in deleted_people:
        if p.key not in outcome.placed and p.tags:
            log.info(
                "'%s' was deleted during the switch; its %d manual tag(s) go with it",
                p.name,
                len(p.tags),
            )
    unplaced = {p.key: p.name for p in outcome.unplaced}
    dropped = [name for _aid, name, _cid in outcome.displaced]
    # Reported only once it is true (the activity feed shows these).
    reason = f"the face-model switch to {pending.to_method}"
    for name in sorted({*pending.dropped, *dropped, *unplaced.values()}):
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


def _people_of(conn: sqlite3.Connection, pending: PendingNames) -> list[Person]:
    """The switch's names and tagged clusters as :class:`Person` entries.

    Evidence is each name's old face photos (their tagged photos for a
    person known by tags alone). A name whose photos include one the new
    model could not read is held (its own face there is missing; a group
    shot's other face could take the name), as is one still held from an
    earlier switch. If kept aside, the album keeps the photos that showed
    them alone before the switch (their tags if they had no faces).
    """
    alive_albums = {aid for aid, _n, _c in _person_album_rows(conn)}
    held = held_albums(conn, [a for a in pending.albums.values() if a in alive_albums])
    keys = list(pending.names) + [k for k in pending.tags if k not in pending.names]
    everything = (
        set().union(*(pending.evidence(k) | pending.tags.get(k, set()) for k in keys))
        if keys
        else set()
    )
    alive = alive_photo_ids(conn, everything)
    people = []
    for k in keys:
        evidence = pending.evidence(k)
        faces = pending.old_cluster_photos.get(k, set())
        aid = pending.albums.get(k) if k in pending.names else None
        people.append(
            Person(
                key=k,
                name=pending.names.get(k, str(k)),
                album_id=aid,
                evidence=evidence,
                tags=set(pending.tags.get(k, set())),
                keep=(faces & pending.solo & alive)
                if faces
                else (pending.tags.get(k, set()) & alive),
                held=bool(evidence & pending.failed) or aid in held,
                hold_for=evidence & pending.failed,
                nameless=k not in pending.names,
            )
        )
    return people


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

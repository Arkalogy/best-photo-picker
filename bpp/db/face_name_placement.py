"""Placing people's names onto clusters whose ids changed.

Two events renumber face clusters: a **recluster** (new threshold over the
same embeddings) and a **face-model switch** (the rows are wiped and
re-extracted, in chunks of photos). In both, a person album's
``cluster_id`` stops meaning the person it was named for.

The rules here keep one invariant: *a name never points at a cluster it
was not matched to*. Albums are moved, never renamed onto another album,
and every move goes through a parking id so the order of the moves does
not matter (two names that swap ids, or a name moving onto the id another
name just left, both work). A name with no matching cluster is parked, or
kept as a person without faces (``CLUSTER_UNPLACED``) that a later refresh
places when its photos turn up; it is never left on a stranger and never
deleted by this module.

Recluster path — ``face_name_relocate.relocate_albums_and_tags``, called
by ``bpp.db.face_identity_remap._remap_names_and_tags`` once the old→new
map is known; :func:`move_named_albums` is the shared move primitive,
also used by identity reconstruction and the smart-album refresh's
orphan transfer.

Switch path — :func:`begin_switch` runs inside the wipe transaction and
parks every named album (and lifts the manual tags) into a pending record
in ``settings``; nothing is placed while the run's batches go by (people
show as default "Person N" albums meanwhile); :func:`finish_switch` runs
when every batch of the run succeeded: :func:`place_final` places every
name from the complete evidence, then the rest become unplaced people.
Meanwhile :func:`switch_pending` lets the endpoints refuse recluster,
merge and rename, which would otherwise work on parked names; the
endpoints that start an extraction stay allowed, since that is what
finishes the switch. ``smart_album_people`` skips parked albums when
it looks for orphans, so a refresh between chunks leaves them alone, and
treats unplaced ones as orphans to place by photo overlap.

Matching reuses ``face_identity_remap._match_named_clusters`` and
``_match_tag_clusters`` so every path agrees on the overlap rule.
"""

from __future__ import annotations

import json
import sqlite3

from bpp.constants import CLUSTER_UNPLACED
from bpp.db.face_switch_record import (  # noqa: F401  (re-exported for callers and tests)
    PENDING_KEY,
    Key,
    PendingNames,
    _save_pending,
    albums_with_stashed_tags,
    alive_photo_ids,
    held_albums,
    load_pending,
    pop_unplaced_tags,
    set_album_evidence,
    single_face_photos,
    stash_unplaced_tags,
    switch_pending,
)
from bpp.db.sql_batches import rows_in
from bpp.utils.logging import get_logger

log = get_logger(__name__)

#: Tag-only clusters follow their photos when the overlap is at least this.
TAG_MIN_JACCARD = 0.1

_PARK_BASE = 1000


#: Record keys of entries carried over from an earlier switch are typed
#: strings ("album:<id>" for a name, "tags:<cluster>" for a tag-only
#: cluster); a current cluster's key is its int id. No range arithmetic,
#: so no collision at any album or cluster id, and carrying an entry again
#: keeps its key.
def _carry_key_for_album(album_id: int) -> str:
    return f"album:{album_id}"


def _carry_key_for_tags(key: int | str) -> str:
    return key if isinstance(key, str) else f"tags:{key}"


#: Warning the server process emits once per affected name; the activity
#: feed's humanizer (activity-humanize.mjs) turns it into a user-facing line
#: (a chunk subprocess's log never reaches that feed).
_UNPLACED = "Could not place the name '%s' after %s; kept as a person without faces"


def park_id(album_id: int) -> int:
    """A cluster id no real cluster can have, unique per album."""
    return -(_PARK_BASE + album_id)


def is_parked(cluster_id: int) -> bool:
    """A parking id (see :func:`park_id`); ``CLUSTER_UNPLACED`` is not one."""
    return cluster_id <= -_PARK_BASE


def _rule(cid: int, album_id: int | None = None) -> str:
    """The smart_person rule for a cluster. Every unplaced album carries
    ``CLUSTER_UNPLACED``, so its rule names the album too: the albums table
    is unique on (album_type, rule_json)."""
    rule: dict[str, int] = {"cluster_id": cid}
    if cid == CLUSTER_UNPLACED and album_id is not None:
        rule["album"] = album_id
    return json.dumps(rule, sort_keys=True)


# ── Shared primitives ────────────────────────────────────────────────


def _person_album_rows(conn: sqlite3.Connection) -> list[tuple[int, str, int]]:
    """``[(album_id, name, cluster_id)]`` for every smart_person album,
    parked and unplaced ones included."""
    try:
        rows = conn.execute(
            "SELECT id, name, smart_person_cluster_id FROM albums "
            "WHERE album_type='smart_person' AND smart_person_cluster_id IS NOT NULL"
        ).fetchall()
    except sqlite3.OperationalError as e:  # a library without albums yet
        log.debug("albums table not ready, no person albums: %s", e)
        return []
    return [(int(aid), name, int(cid)) for aid, name, cid in rows]


def _person_albums(conn: sqlite3.Connection) -> dict[int, tuple[int, str]]:
    """``{cluster_id: (album_id, name)}`` — the occupant of each REAL
    cluster id. Several albums share ``CLUSTER_UNPLACED``; only one is kept
    here, so use :func:`_person_album_rows` to see them all."""
    return {cid: (aid, name) for aid, name, cid in _person_album_rows(conn)}


def _delete_albums(conn: sqlite3.Connection, album_ids: list[int]) -> None:
    from bpp.db.smart_album_people import _delete_orphan_albums

    _delete_orphan_albums(conn, album_ids)


def _set_album_cluster(conn: sqlite3.Connection, moves: list[tuple[int, int]]) -> None:
    """``moves`` = ``[(album_id, cluster_id)]``; rule_json and the shadow
    column are written together (the trigger would sync them anyway)."""
    if moves:
        conn.executemany(
            "UPDATE albums SET rule_json=?, smart_person_cluster_id=? WHERE id=?",
            [(_rule(cid, aid), cid, aid) for aid, cid in moves],
        )


def move_named_albums(
    conn: sqlite3.Connection, moves: list[tuple[int, int]], *, displace_named: bool = False
) -> tuple[list[tuple[int, int]], list[str]]:
    """Move person albums to new cluster ids: ``moves`` = ``[(album_id,
    new_cluster_id)]``. Two passes (park, then place) so the unique
    (album_type, rule_json) index never sees two albums on one id,
    whatever the order of the moves. Two moves onto one id: the first
    wins, the second is skipped.

    An album already sitting on a destination and not itself moving away:
    a default "Person N" is removed; a user-named one becomes an unplaced
    person (``CLUSTER_UNPLACED``, kept with its name) when
    ``displace_named`` (the mover has overlap evidence, the sitter matched
    nothing) and otherwise keeps its place, the move onto it being
    skipped. The accepted set is settled before any write: a move whose
    destination is held by a mover that was itself rejected is rejected
    too, so a blocked chain never leaves a half-applied state. Returns
    ``(moves applied, [(album_id, name, cluster it left)] of the user-named
    albums displaced)``; the caller stashes their tags and reports them.
    """
    from bpp.db.face_identity_remap import _is_default_person_name

    rows = _person_album_rows(conn)
    by_cid = {cid: (aid, name) for aid, name, cid in rows}
    names = {aid: name for aid, name, _cid in rows}  # unplaced ones included
    accepted: dict[int, int] = {}
    taken: set[int] = set()
    for aid, new_cid in moves:
        if new_cid in taken or aid in accepted:
            log.warning(
                "Not moving '%s' onto cluster %d: another move took it", names.get(aid), new_cid
            )
            continue
        accepted[aid] = new_cid
        taken.add(new_cid)

    displaced: list[int] = []
    displaced_named: list[tuple[int, str, int]] = []
    while True:
        rejected = None
        displaced, displaced_named = [], []
        for aid, new_cid in accepted.items():
            other = by_cid.get(new_cid)
            if not other or other[0] == aid or other[0] in accepted:
                continue  # free, or its occupant moves away in this same pass
            if _is_default_person_name(other[1]):
                displaced.append(other[0])
            elif displace_named:
                displaced_named.append((other[0], other[1], new_cid))
            else:
                log.warning(
                    "Not moving '%s' onto cluster %d: '%s' is already there",
                    names.get(aid),
                    new_cid,
                    other[1],
                )
                rejected = aid
                break
        if rejected is None:
            break
        del accepted[rejected]  # and re-check: others may have relied on it moving

    applied = list(accepted.items())
    _delete_albums(conn, displaced)
    _set_album_cluster(conn, [(aid, CLUSTER_UNPLACED) for aid, _n, _c in displaced_named])
    _set_album_cluster(conn, [(aid, park_id(aid)) for aid, _ in applied])
    _set_album_cluster(conn, applied)
    return applied, displaced_named


# ── Model switch: park, place per chunk, finish ──────────────────────


def begin_switch(
    conn: sqlite3.Connection, to_method: str, old_cluster_photos: dict[int, set[int]]
) -> None:
    """Park every person name before the wipe. Does NOT commit: the caller
    commits this together with the ``DELETE FROM face_embeddings`` so a
    crash between the two cannot lose the names.

    Default "Person N" albums are removed (the refresh recreates them for
    the new clusters); user-named albums are moved to a parking id; the
    manual tags are lifted into the record and removed from the table so
    no new cluster inherits another person's tags. Every name and tag of
    an earlier, unfinished switch (a Retry or a second switch before it
    completed) is carried over with its original evidence, under synthetic
    keys, so switching twice loses nothing.
    """
    from bpp.db.face_feedback import clear_cluster_feedback
    from bpp.db.face_identity_remap import _is_default_person_name

    clear_cluster_feedback(conn)  # the groups they name are about to go
    previous = load_pending(conn)
    names: dict[Key, str] = {}
    albums: dict[Key, int] = {}
    evidence: dict[Key, set[int]] = dict(old_cluster_photos)
    tags: dict[Key, set[int]] = {}
    dropped: list[str] = []
    defaults: list[int] = []
    carried: set[int] = set()
    from_unplaced: dict[int, Key] = {}  # album id → key, for the batched reads below
    if previous is not None:
        # The previous switch never finished (names are only placed when a
        # run completes): carry ALL its names and tags with their original
        # evidence (the current clusters are partial). Keys
        # stay the same however often an entry is carried: a name by its
        # album id, a tag-only cluster by its first cluster id (typed
        # strings, so they cannot collide with a current cluster's int key).
        for key, name in previous.names.items():
            aid = previous.albums.get(key)
            if aid is None:
                continue
            new_key = _carry_key_for_album(aid)
            names[new_key], albums[new_key] = name, aid
            evidence[new_key] = previous.old_cluster_photos.get(key, set())
            if key in previous.tags:
                tags[new_key] = previous.tags[key]
            carried.add(aid)
        for key, pids in previous.tags.items():
            if key not in previous.names:  # a tagged cluster nobody named
                new_key = _carry_key_for_tags(key)
                tags[new_key] = pids
                faces = previous.old_cluster_photos.get(key)
                if faces:
                    evidence[new_key] = faces  # identified by its faces first, tags second
        dropped = list(previous.dropped)
    try:
        for pid, cid in conn.execute("SELECT photo_id, cluster_id FROM photo_person_tags"):
            tags.setdefault(int(cid), set()).add(int(pid))
        conn.execute("DELETE FROM photo_person_tags")
    except sqlite3.OperationalError as e:
        log.debug("photo_person_tags table not ready, skipping: %s", e)
    rows = _person_album_rows(conn)
    stashed = albums_with_stashed_tags(conn, [aid for aid, _n, _cid in rows])
    for aid, name, cid in rows:
        if aid in carried:
            continue
        if _is_default_person_name(name) and cid not in tags and aid not in stashed:
            defaults.append(aid)  # nothing the user put in; the refresh remakes it
        elif cid < 0:
            # A name an earlier switch could not place (or left parked with
            # no record): its album's photos are its evidence. Parked here
            # so the refresh does not move it onto a cluster this switch
            # then gives to someone else.
            key = _carry_key_for_album(aid)
            names[key], albums[key] = name, aid
            from_unplaced[aid] = key
        else:
            names[cid] = name
            albums[cid] = aid
    if from_unplaced:
        aids = list(from_unplaced)
        for aid, pid in rows_in(
            conn, "SELECT album_id, photo_id FROM album_photos WHERE album_id IN ({ph})", aids
        ):
            evidence.setdefault(from_unplaced[int(aid)], set()).add(int(pid))
        for aid, pids in pop_unplaced_tags(conn, aids).items():
            tags[from_unplaced[aid]] = pids
    _delete_albums(conn, defaults)
    _set_album_cluster(conn, [(aid, park_id(aid)) for aid in albums.values()])
    # Which evidence photos held a single face, read before the wipe: only
    # those may re-place a person the switch leaves unplaced.
    solo = single_face_photos(conn, set().union(*evidence.values()) if evidence else set())
    if previous is not None:
        solo |= previous.solo
    _save_pending(
        conn,
        PendingNames(
            to_method=to_method,
            old_cluster_photos=evidence,
            names=names,
            tags=tags,
            albums=albums,
            dropped=dropped,
            solo=solo,
        ),
    )
    log.info(
        "Face-model switch to %s: parked %d name(s) (%d carried over), %d tagged cluster(s)",
        to_method,
        len(names),
        len(carried),
        len(tags),
    )


def finish_switch(
    conn: sqlite3.Connection, new_cluster_photos: dict[int, set[int]] | None = None
) -> int:
    """End the switch (see :func:`bpp.db.face_switch_finish.finish_switch`,
    its own module for the 500-LOC cap; imported lazily, as it imports this)."""
    from bpp.db.face_switch_finish import finish_switch as _finish

    return _finish(conn, new_cluster_photos)

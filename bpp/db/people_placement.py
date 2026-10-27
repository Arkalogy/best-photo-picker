"""One decide-and-apply step for putting people's names on face clusters.

Four events need it: a re-clustering, the end of a face-model switch, the
smart-album refresh placing a person kept aside earlier, and identity
reconstruction from labelled faces. Each builds a list of :class:`Person`
(what identifies them, their manual tags, the photos to keep if they stay
unplaced) and calls :func:`place_people`, which

1. decides: named people first, by the shared matcher (strongest overlap
   first, a tie decided for no one, no falling back to a weaker cluster);
   then tagged clusters nobody named, which never take a cluster a named
   person has evidence in;
2. applies: albums move in one order-independent pass, manual tags are
   written on the cluster their person got, and everyone not placed is
   kept as a person without faces (``CLUSTER_UNPLACED``) with their tags
   stashed on the album and their evidence as its photos, never deleted.

Runs inside the caller's transaction; never commits.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from bpp.constants import CLUSTER_UNPLACED
from bpp.utils.logging import get_logger

log = get_logger(__name__)

#: Tag-only clusters follow their photos when the overlap is at least this.
TAG_MIN_JACCARD = 0.1


@dataclass
class Person:
    """Someone whose name (or manual tags) must land on a cluster.

    ``evidence`` is matched against the clusters (face row ids or photo
    ids: the same unit as the cluster map the caller passes). ``tags`` are
    manual-tag photo ids to write where the person lands (already lifted
    off the table by the caller). ``keep`` is the album's photo list if
    the person stays unplaced. ``nameless`` marks a tagged cluster nobody
    named (matched last, by its tags' overlap); a named person may have no
    album (``album_id`` None: they compete for clusters but nothing moves).
    ``held`` people are never placed automatically.
    """

    key: object
    name: str
    album_id: int | None
    evidence: set[int]
    tags: set[int] = field(default_factory=set)
    keep: set[int] = field(default_factory=set)
    held: bool = False
    hold_for: set[int] = field(default_factory=set)  # photos that hold them, to record
    nameless: bool = False


@dataclass
class Outcome:
    placed: dict[object, int]  # key -> cluster id
    unplaced: list[Person]  # named people kept aside
    nameless: list[Person]  # tagged clusters nobody named that matched nothing
    displaced: list[tuple[int, str, int]]  # albums made meanwhile that a name moved off


def decide(
    people: list[Person], clusters: dict[int, set[int]], *, claimed: set[int] | None = None
) -> dict[object, int]:
    """``{person key: cluster id}`` for the people the evidence places."""
    from bpp.db.face_name_matching import _match_named_clusters, _match_tag_clusters

    remap: dict = {}
    taken = set(claimed or ())
    named = [p for p in people if not p.nameless and not p.held]
    _match_named_clusters(
        {p.key: p.name for p in named}, {p.key: p.evidence for p in named}, clusters, remap, taken
    )
    # A cluster a named person has evidence in but was not given (a tie,
    # or a hold) stays free: that person may still be placed there.
    waiting = set().union(
        *(p.evidence for p in people if not p.nameless and not p.held and p.key not in remap)
    )
    taken |= {cid for cid, ev in clusters.items() if ev & waiting}
    nameless = [p for p in people if p.nameless]
    _match_tag_clusters(
        {p.key for p in nameless},
        {p.key: p.evidence for p in nameless},
        clusters,
        remap,
        taken,
        TAG_MIN_JACCARD,
    )
    return remap


def place_people(
    conn: sqlite3.Connection,
    people: list[Person],
    clusters: dict[int, set[int]],
    *,
    displace_named: bool = True,
    keep_unplaced: bool = True,
    claimed: set[int] | None = None,
    restore_stash: bool = False,
) -> Outcome:
    """Decide and apply (see the module docstring). With ``keep_unplaced``
    False the people not placed are left exactly as they are (the refresh
    re-tries people already kept aside)."""
    remap = decide(people, clusters, claimed=claimed)
    return apply(
        conn,
        people,
        remap,
        displace_named=displace_named,
        keep_unplaced=keep_unplaced,
        restore_stash=restore_stash,
    )


def apply(
    conn: sqlite3.Connection,
    people: list[Person],
    remap: dict[object, int],
    *,
    displace_named: bool = True,
    keep_unplaced: bool = True,
    restore_stash: bool = False,
) -> Outcome:
    from bpp.db.face_name_placement import _person_album_rows, _set_album_cluster, move_named_albums
    from bpp.db.face_switch_record import (
        alive_photo_ids,
        hold_albums,
        restore_unplaced_tags,
        set_album_evidence,
        stash_unplaced_tags,
    )

    existing = {aid for aid, _n, _c in _person_album_rows(conn)}
    by_key = {p.key: p for p in people}
    moves = []
    for key, cid in remap.items():
        p = by_key[key]
        if p.nameless or p.album_id is None:
            continue
        if p.album_id in existing:
            moves.append((p.album_id, cid))
        else:
            log.info(
                "Person '%s' was deleted meanwhile; cluster %d keeps its default name", p.name, cid
            )
    applied, displaced = move_named_albums(conn, moves, displace_named=displace_named)
    landed = {aid: cid for aid, cid in applied}
    # Placed: a tag-only cluster, a moved album, or a person with no album
    # (deleted meanwhile, or never had one): their tags land where matched.
    placed = {
        k: c
        for k, c in remap.items()
        if by_key[k].nameless or by_key[k].album_id in landed or by_key[k].album_id not in existing
    }
    if displaced:
        _keep_displaced(conn, displaced)
    # Placed people's tags land on their cluster (a photo deleted meanwhile
    # has no row to point at; its tag is gone with it). Stashed tags of a
    # person kept aside earlier come back too.
    alive = alive_photo_ids(
        conn, set().union(*(by_key[k].tags for k in placed)) if placed else set()
    )
    rows = [(pid, cid) for k, cid in placed.items() for pid in sorted(by_key[k].tags & alive)]
    if rows:
        conn.executemany(
            "INSERT OR IGNORE INTO photo_person_tags (photo_id, cluster_id) VALUES (?, ?)", rows
        )
    if restore_stash:  # a person kept aside earlier: their stashed tags come back
        restore_unplaced_tags(conn, {aid: cid for aid, cid in landed.items()})

    unplaced = [
        p for p in people if not p.nameless and p.key not in placed and p.album_id in existing
    ]
    nameless = [p for p in people if p.nameless and p.key not in placed]
    if keep_unplaced and unplaced:
        _set_album_cluster(conn, [(p.album_id, CLUSTER_UNPLACED) for p in unplaced])
        stash_unplaced_tags(conn, {p.album_id: p.tags for p in unplaced if p.tags})
        set_album_evidence(conn, {p.album_id: p.keep for p in unplaced})
        hold_albums(conn, {p.album_id: p.hold_for for p in unplaced if p.hold_for})
    return Outcome(placed=placed, unplaced=unplaced, nameless=nameless, displaced=displaced)


def _keep_displaced(conn: sqlite3.Connection, displaced: list[tuple[int, str, int]]) -> None:
    """A user-named album sitting on a cluster a name took: kept unplaced,
    its tags stashed (off the cluster, which is someone else now) and its
    tagged photos as its photo list."""
    from bpp.db.face_name_relocate import _tags_of
    from bpp.db.face_switch_record import set_album_evidence, stash_unplaced_tags

    tags = _tags_of(conn, [cid for _a, _n, cid in displaced])
    stash_unplaced_tags(conn, {aid: tags.get(cid, set()) for aid, _n, cid in displaced})
    conn.executemany(
        "DELETE FROM photo_person_tags WHERE photo_id=? AND cluster_id=?",
        [(pid, cid) for _a, _n, cid in displaced for pid in tags.get(cid, ())],
    )
    set_album_evidence(conn, {aid: tags.get(cid, set()) for aid, _n, cid in displaced})


def keep_nameless_on_fresh_ids(
    conn: sqlite3.Connection,
    nameless: list[Person],
    albums: dict[object, int] | None = None,
) -> dict[object, int]:
    """Tagged clusters nobody named that matched nothing: their manual tags
    move to a fresh cluster id each, never dropped. Their existing default
    album (``albums``: key -> album id) moves with them; one without gets
    a new default album. Returns ``{key: new cluster id}``."""
    from bpp.db.face_cluster_ids import next_cluster_id
    from bpp.db.face_name_placement import _rule, _set_album_cluster
    from bpp.db.face_switch_record import alive_photo_ids

    alive = alive_photo_ids(conn, set().union(*(p.tags for p in nameless)) if nameless else set())
    keep = [p for p in nameless if p.tags & alive]
    if not keep:
        return {}
    first = next_cluster_id(conn)
    fresh = {p.key: first + i for i, p in enumerate(sorted(keep, key=lambda p: str(p.key)))}
    conn.executemany(
        "INSERT OR IGNORE INTO photo_person_tags (photo_id, cluster_id) VALUES (?, ?)",
        [(pid, fresh[p.key]) for p in keep for pid in sorted(p.tags & alive)],
    )
    albums = albums or {}
    _set_album_cluster(conn, [(albums[k], cid) for k, cid in fresh.items() if k in albums])
    conn.executemany(
        "INSERT INTO albums (name, album_type, rule_json) VALUES (?, 'smart_person', ?)",
        [(f"Person {cid + 1}", _rule(cid)) for k, cid in fresh.items() if k not in albums],
    )
    log.info("Kept the manual tags of %d unnamed cluster(s) on new ids", len(keep))
    return fresh

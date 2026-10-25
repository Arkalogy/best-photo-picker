"""The recluster path of name placement: apply a known old→new cluster map
to the person albums and the manual tags. Split out of
:mod:`bpp.db.face_name_placement` for the 500-LOC cap; that module
re-exports these for its callers."""

from __future__ import annotations

import sqlite3

from bpp.constants import CLUSTER_UNPLACED
from bpp.db.face_cluster_ids import highest_cluster_id
from bpp.db.face_name_placement import (
    _UNPLACED,
    _person_albums,
    _rule,
    _set_album_cluster,
    move_named_albums,
)
from bpp.db.face_switch_record import (
    alive_photo_ids,
    group_photos,
    set_album_evidence,
    stash_unplaced_tags,
)
from bpp.utils.logging import get_logger

log = get_logger(__name__)

_TAG_TEMP_BASE = 1_000_000


def _move_tags(
    conn: sqlite3.Connection,
    pairs: list[tuple[int, int]],
    stale: set[int],
    *,
    clear_targets: bool = True,
) -> None:
    """Move photo_person_tags rows ``old → new`` for every pair, in two
    passes through per-target temporary ids. Rows left on an old id or
    on a ``stale`` cluster belong to clusters that no longer mean that
    person and are removed; so are rows already on a target id when
    ``clear_targets`` (after a renumbering the target's old tags are
    stale too; after an orphan transfer they are current and kept)."""
    if pairs:
        conn.executemany(
            "UPDATE OR IGNORE photo_person_tags SET cluster_id=? WHERE cluster_id=?",
            [(-(_TAG_TEMP_BASE + new), old) for old, new in pairs],
        )
    gone = {old for old, _ in pairs} | stale
    if clear_targets:
        gone |= {new for _, new in pairs}
    if gone:
        ph = ",".join("?" * len(gone))
        conn.execute(f"DELETE FROM photo_person_tags WHERE cluster_id IN ({ph})", sorted(gone))
    if pairs:
        temps = [-(_TAG_TEMP_BASE + new) for _, new in pairs]
        conn.executemany(
            "UPDATE OR IGNORE photo_person_tags SET cluster_id=? WHERE cluster_id=?",
            [(new, temp) for (_, new), temp in zip(pairs, temps, strict=True)],
        )
        ph = ",".join("?" * len(temps))
        conn.execute(f"DELETE FROM photo_person_tags WHERE cluster_id IN ({ph})", temps)


def relocate_albums_and_tags(
    conn: sqlite3.Connection,
    remap: dict[int, int],
    named_clusters: dict[int, str],
    tag_cids: set[int],
    old_cluster_photos: dict[int, set[int]],
    new_cluster_ids: set[int],
) -> None:
    """Apply ``remap`` (old cluster id → new) to the named person albums
    and the manual tags. Runs inside the caller's transaction.

    * A named album whose old id is remapped moves to the new id
      (:func:`move_named_albums`, displacing what sits there).
    * A named album with no match whose old id now denotes a *different*
      cluster (a tag-only person's included) becomes an unplaced person
      (kept, with its manual tags stashed on the album) rather than being
      left on a stranger. One whose old id no longer exists at all is kept
      where it is (harmless, and a later refresh may still place it).
    """
    albums = _person_albums(conn)
    moves: list[tuple[int, int]] = []
    for old_cid, name in named_clusters.items():
        new_cid = remap.get(old_cid)
        entry = albums.get(old_cid)
        if new_cid is None or new_cid == old_cid or entry is None:
            continue
        moves.append((entry[0], new_cid))
        log.info("Remapped person '%s': cluster %d → %d", name, old_cid, new_cid)

    targets = {new for _, new in moves}
    stale: list[tuple[int, str, int]] = []  # (album id, name, old cluster id)
    for old_cid, name in named_clusters.items():
        entry = albums.get(old_cid)
        if entry is None or remap.get(old_cid) is not None:
            continue
        # Its id now means a different cluster, or its faces matched nothing
        # (the id vanished): either way its album keeps its face evidence,
        # unplaced, rather than a list a refresh would place it by wrongly.
        # A person known by tags alone, on an id nothing reuses, stays put.
        reused = old_cid in new_cluster_ids and old_cid not in targets
        had_faces = bool(old_cluster_photos.get(old_cid))
        if reused or had_faces:
            stale.append((entry[0], name, old_cid))
    _set_album_cluster(conn, [(aid, CLUSTER_UNPLACED) for aid, _n, _c in stale])
    evicted = _evict_tag_only_identities(
        conn, remap, targets | new_cluster_ids, tag_cids, named_clusters
    )
    _, displaced = move_named_albums(conn, moves, displace_named=True)
    unplaced = stale + displaced
    for _aid, name, _cid in unplaced:
        log.warning(_UNPLACED, name, "re-clustering")
    # Their manual tags wait on the album; the rows at their old ids go
    # with the renumbering below.
    tags_by_cid = _tags_of(conn, [cid for _a, _n, cid in unplaced])
    stash_unplaced_tags(conn, {aid: tags_by_cid.get(cid, set()) for aid, _n, cid in unplaced})
    # Their album lists their FACE photos from before (tags only for a
    # person known by tags alone), not a tagged group photo whose other
    # face now owns their old number: the refresh places them by it.
    wanted = (
        set().union(
            *(
                set(old_cluster_photos.get(c, ())) | tags_by_cid.get(c, set())
                for _a, _n, c in unplaced
            )
        )
        if unplaced
        else set()
    )
    alive = alive_photo_ids(conn, wanted)
    # Group photos (two or more faces on record) are left out: they cannot
    # say whose face is whose. A photo whose face is gone stays (it shows
    # no one else, and the refresh checks it again when a face returns).
    group = group_photos(conn, wanted)
    set_album_evidence(
        conn,
        {
            aid: (
                set(old_cluster_photos[cid]) & alive - group
                if old_cluster_photos.get(cid)
                else tags_by_cid.get(cid, set()) & alive
            )
            for aid, _n, cid in unplaced
        },
    )
    stale_cids = {cid for _a, _n, cid in unplaced}
    pairs = [(o, n) for o, n in remap.items() if o != n and o in tag_cids]
    _move_tags(conn, pairs + evicted, stale_cids)


def _evict_tag_only_identities(
    conn: sqlite3.Connection,
    remap: dict[int, int],
    taken_ids: set[int],
    tag_cids: set[int],
    named_clusters: dict[int, str],
) -> list[tuple[int, int]]:
    """A person known only by manual tags (no faces, so the renumbering
    could not follow them) whose cluster id now holds other faces (a
    moved person, or a new cluster): move them, tags and default album,
    to a fresh id nobody uses, instead of leaving their tags on the
    newcomer. One with no album gets a default one there, so the tags are
    not swept up as orphans. A user-named one is handled by the
    displacement rule (kept unplaced, tags stashed). Returns the ``(old
    id, fresh id)`` tag moves."""
    taken = taken_ids | {n for o, n in remap.items() if o != n}
    victims = sorted(c for c in taken if c in tag_cids and c not in remap)
    victims = [c for c in victims if c not in named_clusters]
    if not victims:
        return []
    next_id = max(highest_cluster_id(conn), *remap.values(), *taken) + 1
    fresh = {c: next_id + i for i, c in enumerate(victims)}
    albums = _person_albums(conn)
    _set_album_cluster(conn, [(albums[c][0], fresh[c]) for c in victims if c in albums])
    conn.executemany(
        "INSERT INTO albums (name, album_type, rule_json) VALUES (?, 'smart_person', ?)",
        [(f"Person {fresh[c] + 1}", _rule(fresh[c])) for c in victims if c not in albums],
    )
    for c in victims:
        log.info("Tag-only person on cluster %d moved to %d: the id now holds others", c, fresh[c])
    return list(fresh.items())


def _tags_of(conn: sqlite3.Connection, cluster_ids: list[int]) -> dict[int, set[int]]:
    """``{cluster_id: tagged photo ids}`` for these clusters, in one query."""
    out: dict[int, set[int]] = {}
    if cluster_ids:
        ph = ",".join("?" * len(cluster_ids))
        for pid, cid in conn.execute(
            f"SELECT photo_id, cluster_id FROM photo_person_tags WHERE cluster_id IN ({ph})",
            cluster_ids,
        ):
            out.setdefault(int(cid), set()).add(int(pid))
    return out

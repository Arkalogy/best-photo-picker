"""The recluster path of name placement: apply a known old→new cluster map
to the person albums and the manual tags. Split out of
:mod:`bpp.db.face_name_placement` for the 500-LOC cap; that module
re-exports these for its callers."""

from __future__ import annotations

import sqlite3

from bpp.db.face_name_placement import (
    _UNPLACED,
)
from bpp.db.face_switch_record import (
    alive_photo_ids,
    group_photos,
)
from bpp.db.sql_batches import in_batches, rows_in
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
        for ph, batch in in_batches(sorted(gone)):
            conn.execute(f"DELETE FROM photo_person_tags WHERE cluster_id IN ({ph})", batch)
    if pairs:
        temps = [-(_TAG_TEMP_BASE + new) for _, new in pairs]
        conn.executemany(
            "UPDATE OR IGNORE photo_person_tags SET cluster_id=? WHERE cluster_id=?",
            [(new, temp) for (_, new), temp in zip(pairs, temps, strict=True)],
        )
        for ph, batch in in_batches(temps):
            conn.execute(f"DELETE FROM photo_person_tags WHERE cluster_id IN ({ph})", batch)


def renumber_people(
    conn: sqlite3.Connection,
    old_clusters: dict[int, set[int]],
    new_clusters: dict[int, set[int]],
    evidence_photos: dict[int, set[int]],
) -> None:
    """Put every person back on the new cluster numbers through the shared
    step (:func:`bpp.db.people_placement.place_people`). Runs inside the
    caller's transaction.

    The manual tags are lifted off the table first and written back
    wherever their person lands. A named person is matched by their old
    faces; one known by tags alone keeps their number unless a new cluster
    now uses it. A tagged cluster nobody named follows its faces, keeps a
    free number, or moves to a fresh one; its tags are never dropped.
    Anyone not placed is kept aside with their face photos minus group
    photos (their tags if they had no faces) and their tags stashed.
    """
    from bpp.db.face_identity_remap import _is_default_person_name
    from bpp.db.face_name_placement import _person_album_rows
    from bpp.db.people_placement import Person, apply, decide, keep_nameless_on_fresh_ids

    rows = _person_album_rows(conn)
    named = {
        cid: (aid, name)
        for aid, name, cid in rows
        if cid >= 0 and not _is_default_person_name(name)
    }
    default_album = {
        cid: aid for aid, name, cid in rows if cid >= 0 and _is_default_person_name(name)
    }
    lifted: dict[int, set[int]] = {}
    try:
        for pid, cid in conn.execute("SELECT photo_id, cluster_id FROM photo_person_tags"):
            lifted.setdefault(int(cid), set()).add(int(pid))
        conn.execute("DELETE FROM photo_person_tags")
    except sqlite3.OperationalError as e:  # a library without the tag table yet
        log.debug("photo_person_tags not ready, renumbering without tags: %s", e)
    everything = (
        set().union(*evidence_photos.values(), *lifted.values())
        if (evidence_photos or lifted)
        else set()
    )
    alive = alive_photo_ids(conn, everything)
    group = group_photos(conn, set().union(*evidence_photos.values()) if evidence_photos else set())

    people: list[Person] = []
    stay: list[tuple[int, int]] = []
    for cid, (aid, name) in named.items():
        faces = old_clusters.get(cid, set())
        tags = lifted.pop(cid, set())
        if not faces and cid not in new_clusters:
            stay += [(pid, cid) for pid in tags]  # known by tags alone, number still free
            continue
        photos = evidence_photos.get(cid, set())
        people.append(
            Person(
                key=cid,
                name=name,
                album_id=aid,
                evidence=faces,
                tags=tags,
                keep=(photos & alive - group) if faces else (tags & alive),
            )
        )
    for cid, tags in lifted.items():
        faces = old_clusters.get(cid, set())
        if not faces and cid not in new_clusters:
            stay += [(pid, cid) for pid in tags]
            continue
        people.append(
            Person(
                key=("tags", cid),
                name=f"cluster {cid}",
                album_id=None,
                evidence=faces,
                tags=tags,
                nameless=True,
            )
        )

    remap = decide(people, new_clusters)
    # A tagged cluster nobody named that matched nothing keeps its number
    # (and album) while no new cluster uses it; one whose number now
    # belongs to a new cluster leaves first, with its default album, so a
    # name arriving there does not remove that album.
    unmatched = [p for p in people if p.nameless and p.key not in remap]
    stay += [(pid, p.key[1]) for p in unmatched if p.key[1] not in new_clusters for pid in p.tags]
    if stay:  # written before fresh ids are handed out, so none collides with them
        conn.executemany(
            "INSERT OR IGNORE INTO photo_person_tags (photo_id, cluster_id) VALUES (?, ?)", stay
        )
    leaving = [p for p in unmatched if p.key[1] in new_clusters]
    movable = {p.key: default_album[p.key[1]] for p in leaving if p.key[1] in default_album}
    fresh = keep_nameless_on_fresh_ids(conn, leaving, movable)
    for p in leaving:
        if p.key in fresh:
            log.info(
                "Tag-only person on cluster %d moved to %d: the id now holds others",
                p.key[1],
                fresh[p.key],
            )
    outcome = apply(conn, people, remap)
    # The summary the raw activity log has always carried.
    named_count = sum(1 for p in people if not p.nameless)
    moved = {k: c for k, c in outcome.placed.items() if not isinstance(k, tuple) and k != c}
    for k, c in moved.items():
        log.info("Remapped person '%s': cluster %d → %d", named[k][1], k, c)
    if named_count and not any(not isinstance(k, tuple) for k in outcome.placed):
        log.warning("No cluster remapping found — %d named clusters orphaned", named_count)
    else:
        log.info(
            "Remapping %d clusters (%d named, %d tags) to new IDs",
            len(outcome.placed),
            sum(1 for k in outcome.placed if not isinstance(k, tuple)),
            sum(1 for k in outcome.placed if isinstance(k, tuple)),
        )
    for name in sorted({p.name for p in outcome.unplaced} | {n for _a, n, _c in outcome.displaced}):
        log.warning(_UNPLACED, name, "re-clustering")


def _tags_of(conn: sqlite3.Connection, cluster_ids: list[int]) -> dict[int, set[int]]:
    """``{cluster_id: tagged photo ids}`` for these clusters, in batches."""
    out: dict[int, set[int]] = {}
    for pid, cid in rows_in(
        conn,
        "SELECT photo_id, cluster_id FROM photo_person_tags WHERE cluster_id IN ({ph})",
        cluster_ids,
    ):
        out.setdefault(int(cid), set()).add(int(pid))
    return out

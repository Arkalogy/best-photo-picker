"""Smart-album refresher for ``smart_person`` albums.

Person albums are the largest single cluster of smart-album logic —
they handle face_embeddings → cluster mapping, manual photo_person_tags,
orphan-cluster detection + name transfer when re-clustering shifts
cluster ids, and tag-only clusters (users who tag a photo before face
detection has run).

Extracted from ``bpp.db.smart_albums`` during the v0.1 cleanup.
Re-exported from ``bpp.db.smart_albums`` for back-compat.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from bpp.constants import ACTIVE_PHOTO_SQL
from bpp.db.dialect import dialect
from bpp.utils.logging import get_logger

log = get_logger(__name__)

_ACTIVE = ACTIVE_PHOTO_SQL


def _is_default_person_name(name: str) -> bool:
    """Return True if the name is an auto-generated default like 'Person 5'."""
    return bool(re.match(r"^Person \d+$", name))


def _ensure_smart_album(*args: Any, **kwargs: Any) -> Any:
    """Lazy delegate to ``smart_albums._ensure_smart_album``.

    Imported inside the function call to break the circular import:
    smart_albums imports this module's ``_refresh_person_albums`` for
    the registry, and these helpers in turn invoke _ensure_smart_album.
    """
    from bpp.db.smart_albums import _ensure_smart_album as _impl

    return _impl(*args, **kwargs)


def _load_person_tags(conn: sqlite3.Connection) -> dict[int, set[int]]:
    """Return ``{cluster_id: {photo_id, ...}}`` for active manually-tagged photos."""
    try:
        tag_rows = conn.execute(
            "SELECT pt.cluster_id, pt.photo_id FROM photo_person_tags pt "
            "JOIN photos p ON p.id = pt.photo_id "
            f"WHERE p.{_ACTIVE}"
        ).fetchall()
    except sqlite3.OperationalError as e:
        log.debug("photo_person_tags table not ready, skipping: %s", e)
        return {}
    out: dict[int, set[int]] = {}
    for cid, pid in tag_rows:
        out.setdefault(cid, set()).add(pid)
    return out


def _load_cluster_photo_map(
    conn: sqlite3.Connection, cluster_ids: list[int]
) -> dict[int, set[int]]:
    """Bulk-fetch ``{cluster_id: {photo_id, ...}}`` for the given clusters."""
    if not cluster_ids:
        return {}
    mapping_rows = conn.execute(
        "SELECT fe.cluster_id, fe.photo_id FROM face_embeddings fe "
        "JOIN photos p ON p.id = fe.photo_id "
        f"WHERE fe.cluster_id >= 0 AND p.{_ACTIVE}"
    ).fetchall()
    out: dict[int, set[int]] = {}
    for cid, pid in mapping_rows:
        out.setdefault(cid, set()).add(pid)
    return out


def _create_clusters_with_embeddings(
    conn: sqlite3.Connection,
    rows: list[Any],
    cluster_photo_map: dict[int, set[int]],
    tagged_by_cluster: dict[int, set[int]],
) -> set[int]:
    """Step 1: create/update albums for clusters that have face embeddings.

    Returns the set of cluster_ids that were touched (passed back to
    Step 2 so it can detect orphans by their absence).
    """
    embedding_cluster_ids: set[int] = set()
    for cluster_id, _count in rows:
        photo_ids = cluster_photo_map.get(cluster_id, set()).copy()
        if cluster_id in tagged_by_cluster:
            photo_ids |= tagged_by_cluster[cluster_id]
        if not photo_ids:
            continue
        embedding_cluster_ids.add(cluster_id)
        _ensure_smart_album(
            conn,
            name=f"Person {cluster_id + 1}",
            album_type="smart_person",
            rule={"cluster_id": cluster_id},
            photo_ids=list(photo_ids),
        )
    return embedding_cluster_ids


def _collect_orphan_albums(
    conn: sqlite3.Connection,
    embedding_cluster_ids: set[int],
) -> tuple[list[int], list[tuple[int, str, int]]]:
    """Find smart_person albums whose cluster no longer has embeddings.

    Returns ``(orphan_album_ids, user_named_orphans)`` where
    ``user_named_orphans`` is the subset whose name isn't a default
    "Person N" — those need name-transfer treatment in step 2.
    """
    # P5b: read the shadow column directly. The previous load-and-parse
    # pattern hit every row's rule_json text; the shadow column gives
    # the same data via the indexed lookup path.
    existing = conn.execute(
        "SELECT id, name, smart_person_cluster_id AS cid FROM albums "
        "WHERE album_type='smart_person'"
    ).fetchall()
    orphan_album_ids: list[int] = []
    user_named: list[tuple[int, str, int]] = []
    for album_id, album_name, cid in existing:
        if cid is not None and cid not in embedding_cluster_ids:
            orphan_album_ids.append(album_id)
            if not _is_default_person_name(album_name):
                user_named.append((album_id, album_name, cid))
    return orphan_album_ids, user_named


def _gather_orphan_photo_lists(
    conn: sqlite3.Connection,
    user_named_orphans: list[tuple[int, str, int]],
) -> list[tuple[str, int, list[int]]]:
    """Look up the photo_ids of each user-named orphan album, batched.

    The result feeds best-overlap matching for name transfer.
    """
    if not user_named_orphans:
        return []
    named_ids = [a[0] for a in user_named_orphans]
    ph = ",".join("?" * len(named_ids))
    album_photos_map: dict[int, list[int]] = {}
    for r in conn.execute(
        f"SELECT album_id, photo_id FROM album_photos WHERE album_id IN ({ph})",
        named_ids,
    ).fetchall():
        album_photos_map.setdefault(r[0], []).append(r[1])
    out: list[tuple[int, str, int, list[int]]] = []
    for album_id, album_name, cid in user_named_orphans:
        old_pids = album_photos_map.get(album_id, [])
        if old_pids:
            out.append((album_id, album_name, cid, old_pids))
    return out


def _delete_orphan_albums(conn: sqlite3.Connection, orphan_album_ids: list[int]) -> None:
    """Batch-delete every orphan album row + its album_photos rows."""
    if not orphan_album_ids:
        return
    ph = ",".join("?" * len(orphan_album_ids))
    conn.execute(f"DELETE FROM album_photos WHERE album_id IN ({ph})", orphan_album_ids)
    conn.execute(f"DELETE FROM albums WHERE id IN ({ph})", orphan_album_ids)


def _transfer_orphan_names(
    conn: sqlite3.Connection,
    orphan_names: list[tuple[int, str, int, list[int]]],
    cluster_photo_map: dict[int, set[int]],
) -> set[int]:
    """Transfer user-given names from orphan albums to the best-overlap
    surviving cluster. Returns the album ids whose names WERE transferred
    — the orchestrator must keep (not delete) the rest, so an unmatched
    name survives until a refresh that can place it.

    Uses best-overlap matching: find the new cluster containing the
    most photos from the old named cluster. No minimum threshold —
    if someone took the time to name a person, preserve that name
    as long as any photos match. Sorted by old-cluster size so
    larger clusters claim first.
    """

    transferred: set[int] = set()
    claimed_cids: set[int] = set()
    orphan_names.sort(key=lambda x: -len(x[3]))
    for album_id, name, old_cid, old_pids in orphan_names:
        old_set = set(old_pids)
        best_cid = None
        best_overlap = 0
        for cid, pids in cluster_photo_map.items():
            if cid in claimed_cids:
                continue
            overlap = len(old_set & pids)
            if overlap > best_overlap:
                best_overlap = overlap
                best_cid = cid
        if best_cid is not None and best_overlap > 0:
            claimed_cids.add(best_cid)
            transferred.add(album_id)
            # P5b: indexed shadow-column lookup.
            conn.execute(
                "UPDATE albums SET name=? WHERE album_type='smart_person' "
                "AND smart_person_cluster_id=?",
                (name, best_cid),
            )
            # Remap photo_person_tags so future tag lookups follow the new cluster.
            conn.execute(
                "UPDATE photo_person_tags SET cluster_id=? WHERE cluster_id=?",
                (best_cid, old_cid),
            )
    return transferred


def _create_tag_only_clusters(conn: sqlite3.Connection, embedding_cluster_ids: set[int]) -> None:
    """Step 3: create albums for clusters that only have manual tags
    (no face embeddings). Re-reads tags because step 2's name-transfer
    may have remapped cluster_ids.

    Tag-only person albums are an intentional feature — a user can tag
    a photo as belonging to a person before face detection has run on
    that photo (or at all). Even one tag is sufficient. Stale-tag
    cleanup happens at extraction-retry time (see
    `bpp.web.bp_faces.api_faces_retry`), not here.
    """
    tagged_by_cluster_updated = _load_person_tags(conn)
    active_cluster_ids = set(embedding_cluster_ids)
    for cid, pids in tagged_by_cluster_updated.items():
        if cid not in active_cluster_ids and pids:
            active_cluster_ids.add(cid)
            _ensure_smart_album(
                conn,
                name=f"Person {cid + 1}",
                album_type="smart_person",
                rule={"cluster_id": cid},
                photo_ids=list(pids),
            )


def _refresh_person_albums(conn: sqlite3.Connection) -> None:
    """Create smart albums per face cluster, clean up stale ones.

    M12.d: split into named phases (load → step 1 create → step 2
    orphan/transfer → step 3 tag-only). The orchestrator stays in
    this function so the order + commit boundary are obvious.
    """
    try:
        rows = conn.execute(
            "SELECT fe.cluster_id, COUNT(DISTINCT fe.photo_id) as cnt "
            "FROM face_embeddings fe "
            "JOIN photos p ON p.id = fe.photo_id "
            f"WHERE fe.cluster_id >= 0 AND p.{_ACTIVE} "
            "GROUP BY fe.cluster_id HAVING cnt >= 1 "
            "ORDER BY cnt DESC"
        ).fetchall()
    except sqlite3.OperationalError as e:
        log.debug("face_embeddings table not ready, skipping: %s", e)
        return

    # Guard: zero clusters while person albums exist means the face data
    # was just wiped or extraction is mid-flight — NOT that every person
    # vanished. Refreshing in that state orphans every album with nothing
    # to transfer names onto (2026-06-11 incident: a hash-computation
    # refresh fired 9 minutes into a faces/retry wipe and deleted all six
    # named people). Skip; the post-extraction refresh reconciles.
    if not rows:
        has_person_albums = conn.execute(
            "SELECT 1 FROM albums WHERE album_type='smart_person' LIMIT 1"
        ).fetchone()
        if has_person_albums:
            log.info(
                "No face clusters but person albums exist — skipping person-album "
                "refresh (faces wiped or extraction in flight)"
            )
            return

    tagged_by_cluster = _load_person_tags(conn)
    cluster_photo_map = _load_cluster_photo_map(conn, [r[0] for r in rows])

    # Step 1: create/update albums for clusters that actually have embeddings.
    embedding_cluster_ids = _create_clusters_with_embeddings(
        conn, rows, cluster_photo_map, tagged_by_cluster
    )

    # Step 2: detect orphaned named albums + transfer names BEFORE
    # creating tag-only albums. Stale manual tags would otherwise keep
    # old clusters "active" and block name transfer.
    #
    # Transfer runs BEFORE deletion, and a user-named orphan whose name
    # found no overlap target is KEPT — a stale album is recoverable,
    # a deleted name is not.
    orphan_album_ids, user_named_orphans = _collect_orphan_albums(conn, embedding_cluster_ids)
    orphan_names = _gather_orphan_photo_lists(conn, user_named_orphans)
    transferred_ids = _transfer_orphan_names(conn, orphan_names, cluster_photo_map)
    untransferred_named = {a[0] for a in user_named_orphans} - transferred_ids
    if untransferred_named:
        log.warning(
            "Keeping %d user-named person album(s) with no overlap target "
            "(album ids %s) — names preserved on their stale clusters",
            len(untransferred_named),
            sorted(untransferred_named),
        )
    _delete_orphan_albums(conn, [aid for aid in orphan_album_ids if aid not in untransferred_named])

    # Step 3: create albums for clusters that only have manual tags.
    _create_tag_only_clusters(conn, embedding_cluster_ids)

    conn.commit()


def _on_rename_smart_person(conn: sqlite3.Connection, album: dict[str, Any], new_name: str) -> None:
    """smart_person rename: propagate the new identity label to all
    face_embeddings rows in this person's cluster.

    Skips default "Person N" names so the identity column doesn't get
    polluted with auto-generated placeholders.
    """
    rule = album.get("rule") or {}
    cid = rule.get("cluster_id")
    if cid is None:
        log.warning(
            "smart_person rename: skipping identity propagation"
            " — album %s has no cluster_id (rule=%r)",
            album.get("id"),
            rule,
        )
        return
    cols = dialect.column_names(conn, "face_embeddings")
    if "identity" not in cols:
        return
    if new_name.startswith("Person ") and new_name[7:].isdigit():
        return
    conn.execute(
        "UPDATE face_embeddings SET identity = ? WHERE cluster_id = ?",
        (new_name, cid),
    )
    conn.commit()
    log.info("Propagated identity %r to cluster %d", new_name, cid)

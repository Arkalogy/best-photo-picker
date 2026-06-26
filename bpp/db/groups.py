"""Face co-occurrence detection and group management."""

from __future__ import annotations

import sqlite3

from bpp.constants import ACTIVE_PHOTO_SQL, FACE_MIN_PHOTOS
from bpp.utils.logging import get_logger

log = get_logger(__name__)

_ACTIVE = ACTIVE_PHOTO_SQL


def group_min_photos(conn: sqlite3.Connection) -> int:
    """The user-set 'photos shared before a group forms' threshold.

    Stored in the library settings table (Settings → Group detection);
    falls back to the DEFAULTS value. Floored at 1.
    """
    from bpp.config import DEFAULTS
    from bpp.db.settings import get_setting

    raw = get_setting(conn, "group_min_photos")
    try:
        return max(1, int(raw)) if raw is not None else int(DEFAULTS["group_min_photos"])
    except (TypeError, ValueError):
        return int(DEFAULTS["group_min_photos"])


def _significant_clusters(conn: sqlite3.Connection) -> set[int]:
    """Clusters that count for group detection: named OR >= FACE_MIN_PHOTOS
    photos — the same rule the People view uses for its Included tab.

    Without this gate, every unmerged cluster fragment ("Person 75", 3
    faces of someone who already has a named cluster) forms junk groups
    like "Person 2 & Person 75" that drown out the real ones.
    """
    from bpp.db.albums import get_smart_person_cluster_name_map

    counts = dict(
        conn.execute(
            "SELECT fe.cluster_id, COUNT(DISTINCT fe.photo_id) "
            "FROM face_embeddings fe "
            "JOIN photos p ON p.id = fe.photo_id "
            f"WHERE fe.cluster_id >= 0 AND p.{_ACTIVE} "
            "GROUP BY fe.cluster_id"
        ).fetchall()
    )
    names = get_smart_person_cluster_name_map(conn)
    significant = set()
    for cid, n_photos in counts.items():
        # "Named" means a real name — the auto "Person N" albums don't count.
        named = names.get(cid) not in (None, f"Person {cid + 1}")
        if named or n_photos >= FACE_MIN_PHOTOS:
            significant.add(cid)
    return significant


def compute_cooccurrence(
    conn: sqlite3.Connection, min_photos: int = 3
) -> list[tuple[int, int, int]]:
    """Return pairs of face clusters that appear together in photos.

    Returns list of (cluster_a, cluster_b, shared_photo_count) sorted by
    count descending. Only includes pairs with >= min_photos shared photos.
    """
    try:
        rows = conn.execute(
            "SELECT fe1.cluster_id, fe2.cluster_id, "
            "  COUNT(DISTINCT fe1.photo_id) AS shared "
            "FROM face_embeddings fe1 "
            "JOIN face_embeddings fe2 "
            "  ON fe1.photo_id = fe2.photo_id "
            "  AND fe1.cluster_id < fe2.cluster_id "
            "JOIN photos p ON p.id = fe1.photo_id "
            f"WHERE fe1.cluster_id >= 0 AND fe2.cluster_id >= 0 AND p.{_ACTIVE} "
            "GROUP BY fe1.cluster_id, fe2.cluster_id "
            "HAVING shared >= ? "
            "ORDER BY shared DESC",
            (min_photos,),
        ).fetchall()
    except sqlite3.OperationalError as e:
        log.debug("Table not ready, skipping: %s", e)
        return []
    return [(r[0], r[1], r[2]) for r in rows]


def detect_groups(conn: sqlite3.Connection, min_photos: int = 3) -> list[dict]:
    """Detect groups of people who appear together frequently.

    Uses greedy clique expansion: starts with highest co-occurring pair,
    tries adding members who co-occur with ALL existing members.
    Returns groups sorted by shared photo count descending.

    Each group dict has:
      - members: list[int] — cluster IDs
      - photo_count: int — photos where ALL members co-occur
      - member_names: list[str|None] — display names (from albums)
    """
    pairs = compute_cooccurrence(conn, min_photos)
    if not pairs:
        return []

    # Groups only form between significant people (named or >= FACE_MIN_PHOTOS
    # photos) — fragments awaiting a merge don't get cards.
    significant = _significant_clusters(conn)
    pairs = [(a, b, c) for a, b, c in pairs if a in significant and b in significant]
    if not pairs:
        return []

    # Build co-occurrence lookup: (min_id, max_id) -> count
    cooc: dict[tuple[int, int], int] = {}
    all_clusters: set[int] = set()
    for a, b, count in pairs:
        cooc[(a, b)] = count
        all_clusters.add(a)
        all_clusters.add(b)

    # Greedy clique expansion
    found_groups: list[frozenset[int]] = []
    used_pairs: set[tuple[int, int]] = set()

    for a, b, _count in pairs:
        if (a, b) in used_pairs:
            continue

        group = {a, b}
        # Try expanding with other clusters
        for c in sorted(all_clusters - group):
            # Check c co-occurs with ALL current group members
            if all(cooc.get((min(c, m), max(c, m)), 0) >= min_photos for m in group):
                group.add(c)

        fs = frozenset(group)
        # Skip if this group is a subset of an already-found group
        if any(fs <= existing for existing in found_groups):
            continue
        # Remove any existing groups that are subsets of this new one
        found_groups = [g for g in found_groups if not g < fs]
        found_groups.append(fs)

        # Mark pairs as used
        members = sorted(group)
        for i, m1 in enumerate(members):
            for m2 in members[i + 1 :]:
                used_pairs.add((m1, m2))

    # Build result with photo counts
    results = []
    for group in found_groups:
        members = sorted(group)
        photo_count = _count_group_photos(conn, members)
        if photo_count >= min_photos:
            results.append(
                {
                    "members": members,
                    "photo_count": photo_count,
                }
            )

    # Resurface high-value pairs the clique expansion swallowed: the
    # 5-person family clique may co-occur in only 9 photos while the
    # Leo & AZ pair inside it shares 592 — that pair is its own group.
    # A pair stays suppressed only when no containing clique loses to it
    # on photo count (e.g. a trio that appears in exactly the same photos).
    kept_sets = {frozenset(g["members"]) for g in results}
    for a, b, shared in pairs:
        fs = frozenset((a, b))
        if fs in kept_sets:
            continue
        containing_counts = [g["photo_count"] for g in results if fs <= frozenset(g["members"])]
        if containing_counts and max(containing_counts) >= shared:
            continue
        results.append({"members": sorted(fs), "photo_count": shared})
        kept_sets.add(fs)

    results.sort(key=lambda g: g["photo_count"], reverse=True)
    return results


def get_group_photo_ids(conn: sqlite3.Connection, cluster_ids: list[int]) -> list[int]:
    """Return photo IDs where ALL given face clusters appear."""
    if not cluster_ids:
        return []
    if len(cluster_ids) == 1:
        rows = conn.execute(
            "SELECT DISTINCT fe.photo_id FROM face_embeddings fe "
            "JOIN photos p ON p.id = fe.photo_id "
            f"WHERE fe.cluster_id = ? AND p.{_ACTIVE}",
            (cluster_ids[0],),
        ).fetchall()
        return [r[0] for r in rows]

    # INTERSECT across all members
    parts = []
    params: list[int] = []
    for cid in cluster_ids:
        parts.append(
            "SELECT DISTINCT fe.photo_id FROM face_embeddings fe "
            "JOIN photos p ON p.id = fe.photo_id "
            f"WHERE fe.cluster_id = ? AND p.{_ACTIVE}"
        )
        params.append(cid)
    query = " INTERSECT ".join(parts)
    rows = conn.execute(query, params).fetchall()
    return [r[0] for r in rows]


def _count_group_photos(conn: sqlite3.Connection, cluster_ids: list[int]) -> int:
    """Count photos where ALL given clusters have faces."""
    return len(get_group_photo_ids(conn, cluster_ids))


def has_group_data(conn: sqlite3.Connection) -> bool:
    """Return True if there are any co-occurring face pairs."""
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM ("
            "  SELECT fe1.photo_id "
            "  FROM face_embeddings fe1 "
            "  JOIN face_embeddings fe2 "
            "    ON fe1.photo_id = fe2.photo_id "
            "    AND fe1.cluster_id < fe2.cluster_id "
            "  WHERE fe1.cluster_id >= 0 AND fe2.cluster_id >= 0 "
            "  LIMIT 1"
            ")"
        ).fetchone()
        return bool(row and row[0] > 0)
    except sqlite3.OperationalError as e:
        log.debug("Table not ready, skipping: %s", e)
        return False

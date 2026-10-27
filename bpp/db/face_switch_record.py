"""The pending record of a face-model switch: what still has to be put
back, stored as JSON in ``settings`` so the chunk subprocesses, the
server and a restart all read one truth. Split out of
:mod:`bpp.db.face_name_placement` for the 500-LOC cap."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from bpp.db.sql_batches import in_batches, rows_in
from bpp.utils.json_utils import safe_json_loads

#: ``settings`` key holding the pending record of a model switch.
PENDING_KEY = "face_switch_pending"

#: A record key: a current cluster id, or a typed string for an entry carried
#: over from an earlier switch ("album:<id>", "tags:<cluster>").
Key = int | str


def _key(raw: object) -> Key:
    return raw if isinstance(raw, str) else int(raw)  # type: ignore[arg-type]


@dataclass
class PendingNames:
    """What a model switch still has to put back, as stored in settings.
    Keys are the old cluster ids, or typed strings ("album:<id>",
    "tags:<cluster>") for entries carried over from an earlier switch."""

    to_method: str
    old_cluster_photos: dict[Key, set[int]]
    names: dict[Key, str]  # key → user-given name
    tags: dict[Key, set[int]]  # key → manually tagged photo ids
    albums: dict[Key, int]  # key → parked album id
    placed: dict[Key, int] = field(default_factory=dict)  # key → new cluster id
    dropped: list[str] = field(default_factory=list)  # names removed on the way
    solo: set[int] = field(default_factory=set)  # evidence photos that held one face before
    covered: set[int] = field(default_factory=set)  # photos processed under the new model
    failed: set[int] = field(default_factory=set)  # photos it could not process

    def to_json(self) -> str:
        return json.dumps(
            {
                "to_method": self.to_method,
                "old": [[cid, sorted(pids)] for cid, pids in self.old_cluster_photos.items()],
                "names": [[cid, name] for cid, name in self.names.items()],
                "tags": [[cid, sorted(pids)] for cid, pids in self.tags.items()],
                "albums": [[cid, aid] for cid, aid in self.albums.items()],
                "placed": [[old, new] for old, new in self.placed.items()],
                "dropped": list(self.dropped),
                "solo": sorted(self.solo),
                "covered": sorted(self.covered),
                "failed": sorted(self.failed),
            }
        )

    @classmethod
    def from_json(cls, raw: str) -> PendingNames | None:
        data = safe_json_loads(raw, default=None, expected_type=dict, context=PENDING_KEY)
        if not isinstance(data, dict) or not isinstance(data.get("to_method"), str):
            return None
        try:
            return cls(
                to_method=data["to_method"],
                old_cluster_photos={_key(c): {int(p) for p in ps} for c, ps in data.get("old", [])},
                names={_key(c): str(n) for c, n in data.get("names", [])},
                tags={_key(c): {int(p) for p in ps} for c, ps in data.get("tags", [])},
                albums={_key(c): int(a) for c, a in data.get("albums", [])},
                placed={_key(o): int(n) for o, n in data.get("placed", [])},
                dropped=[str(n) for n in data.get("dropped", [])],
                solo={int(p) for p in data.get("solo", [])},
                covered={int(p) for p in data.get("covered", [])},
                failed={int(p) for p in data.get("failed", [])},
            )
        except (TypeError, ValueError) as e:
            import logging

            logging.getLogger(__name__).warning("Face-model switch record is damaged: %s", e)
            return None

    def unplaced_names(self) -> dict[Key, str]:
        return {c: n for c, n in self.names.items() if c not in self.placed}

    def unplaced_tags(self) -> dict[Key, set[int]]:
        return {c: p for c, p in self.tags.items() if c not in self.placed}

    def evidence(self, key: Key) -> set[int]:
        """The photos that identify a pending name: its faces, or for a
        person known only by manual tags, the tagged photos."""
        return self.old_cluster_photos.get(key) or self.tags.get(key, set())


def record_coverage(conn: sqlite3.Connection, processed: set[int], failed: set[int]) -> None:
    """A batch of the switch's run went through: these photos were
    processed under the new model (a photo with no face included), these
    could not be. Does not commit (the batch's phase commits)."""
    pending = load_pending(conn)
    if pending is None:
        return
    pending.covered |= processed - failed
    pending.failed = (pending.failed | failed) - pending.covered
    _save_pending(conn, pending)
    release_holds(conn, processed - failed)


#: The photos a switch must have processed before it can end: every photo
#: with a face to extract that a face run is given (the analysis list
#: leaves out hidden photos; trashed, missing files and Live Photo sidecars
#: are not waited for either). A hidden photo's faces wait until it is
#: shown again; the switch does not.
CANDIDATE_PHOTOS_SQL = (
    "SELECT id FROM photos WHERE COALESCE(face_count, 0) > 0 AND deleted_at IS NULL "
    "AND hidden_at IS NULL AND missing = 0 AND is_live_photo_sidecar = 0"
)


def uncovered_photos(conn: sqlite3.Connection, pending: PendingNames) -> set[int]:
    """Candidate photos the switch has neither processed nor tried."""
    return (
        {int(r[0]) for r in conn.execute(CANDIDATE_PHOTOS_SQL)} - pending.covered - pending.failed
    )


def load_pending(conn: sqlite3.Connection) -> PendingNames | None:
    try:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (PENDING_KEY,)).fetchone()
    except sqlite3.OperationalError:
        return None
    return PendingNames.from_json(row[0]) if row and row[0] else None


def switch_pending(conn: sqlite3.Connection) -> bool:
    """True while a model switch still has names to place. Cheap: it is
    asked on every face mutation, so the record is not parsed."""
    try:
        row = conn.execute("SELECT 1 FROM settings WHERE key=?", (PENDING_KEY,)).fetchone()
    except sqlite3.OperationalError:
        return False
    return row is not None and row[0] == 1


def _save_pending(conn: sqlite3.Connection, pending: PendingNames | None) -> None:
    if pending is None:
        conn.execute("DELETE FROM settings WHERE key=?", (PENDING_KEY,))
    else:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (PENDING_KEY, pending.to_json()),
        )


#: Where an unplaced person's manual tags wait (``albums.config_json``,
#: a key the album API hides and ``update_album`` preserves) until a
#: refresh places the album; the tag rows need a cluster id.
STASHED_TAGS = "unplaced_tags"

#: Photos that kept a person from being placed at the end of a switch (the
#: new model could not read them): no automatic placement while any of
#: them still has no face on record. Kept in ``albums.config_json`` like
#: the stash, hidden from the API and preserved by ``update_album``.
HELD_FOR = "held_for"

#: The album config keys the switch owns.
INTERNAL_CONFIG_KEYS = (STASHED_TAGS, HELD_FOR)

#: True when an albums row's config holds a hold.
HAS_HOLD_SQL = (
    "(CASE WHEN json_valid(config_json) "
    f"THEN json_type(config_json, '$.{HELD_FOR}') END IS NOT NULL)"
)


def _album_configs(conn: sqlite3.Connection, album_ids: list[int]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for aid, raw in rows_in(
        conn, "SELECT id, config_json FROM albums WHERE id IN ({ph})", album_ids
    ):
        cfg = safe_json_loads(raw, default=None, expected_type=dict, context="album config")
        out[int(aid)] = dict(cfg) if isinstance(cfg, dict) else {}
    return out


def _write_configs(conn: sqlite3.Connection, cfgs: dict[int, dict]) -> None:
    if not cfgs:
        return  # nothing to write (and nothing to prepare on a bare schema)
    conn.executemany(
        "UPDATE albums SET config_json=? WHERE id=?",
        [(json.dumps(cfg) if cfg else None, aid) for aid, cfg in cfgs.items()],
    )


def stash_unplaced_tags(conn: sqlite3.Connection, tags_by_album: dict[int, set[int]]) -> None:
    """Keep each album's manual tags on the album while it has no cluster."""
    tags_by_album = {aid: pids for aid, pids in tags_by_album.items() if pids}
    cfgs = _album_configs(conn, list(tags_by_album))
    for aid, pids in tags_by_album.items():
        cfg = cfgs.setdefault(aid, {})
        cfg[STASHED_TAGS] = sorted(set(cfg.get(STASHED_TAGS, [])) | pids)
    _write_configs(conn, cfgs)


def pop_unplaced_tags(conn: sqlite3.Connection, album_ids: list[int]) -> dict[int, set[int]]:
    """Take the stashed tags off these albums. ``{album_id: photo ids}``
    for the albums that had any."""
    cfgs = _album_configs(conn, album_ids)
    popped = {
        aid: {int(p) for p in cfg.pop(STASHED_TAGS)}
        for aid, cfg in cfgs.items()
        if STASHED_TAGS in cfg
    }
    _write_configs(conn, {aid: cfgs[aid] for aid in popped})
    return popped


def single_face_photos(conn: sqlite3.Connection, photo_ids: set[int]) -> set[int]:
    """Which of these photos hold exactly one face row. A dismissed face
    (a false detection the user threw out) does not count; an unassigned
    one does, since it may be a real person in the shot. Only such photos
    say whose face is whose: a person is re-placed later by them alone."""
    from bpp.constants import CLUSTER_DISMISSED

    return {
        int(r[0])
        for r in rows_in(
            conn,
            "SELECT photo_id FROM face_embeddings WHERE photo_id IN ({ph}) "
            "AND cluster_id != ? GROUP BY photo_id HAVING COUNT(*) = 1",
            photo_ids,
            [CLUSTER_DISMISSED],
        )
    }


def group_photos(conn: sqlite3.Connection, photo_ids: set[int]) -> set[int]:
    """Which of these photos hold two or more face rows (dismissed ones
    not counted)."""
    from bpp.constants import CLUSTER_DISMISSED

    return {
        int(r[0])
        for r in rows_in(
            conn,
            "SELECT photo_id FROM face_embeddings WHERE photo_id IN ({ph}) "
            "AND cluster_id != ? GROUP BY photo_id HAVING COUNT(*) > 1",
            photo_ids,
            [CLUSTER_DISMISSED],
        )
    }


def alive_photo_ids(conn: sqlite3.Connection, photo_ids: set[int]) -> set[int]:
    """The subset of these photo ids that still have a ``photos`` row (a tag
    may only point at one of those), in batches."""
    return {int(r[0]) for r in rows_in(conn, "SELECT id FROM photos WHERE id IN ({ph})", photo_ids)}


def set_album_evidence(
    conn: sqlite3.Connection, photos_by_album: dict[int, set[int]], *, active_only: bool = False
) -> None:
    """Set each album's photo list to exactly these photos (ones whose row
    is gone skipped), even when none is left: an unplaced person's list is
    the evidence a refresh places them by, and a stale list would place
    them on whoever those photos show now. Trashed or hidden photos stay,
    since both can be undone and are the person's evidence all the same;
    ``active_only`` (a placed album the user sees) keeps active photos only."""
    if not photos_by_album:
        return
    alive = alive_photo_ids(conn, set().union(*photos_by_album.values()))
    if active_only and alive:
        from bpp.constants import ACTIVE_PHOTO_SQL

        alive = {
            int(r[0])
            for r in rows_in(
                conn, f"SELECT id FROM photos WHERE id IN ({{ph}}) AND {ACTIVE_PHOTO_SQL}", alive
            )
        }
    for ph, batch in in_batches(sorted(photos_by_album)):
        conn.execute(f"DELETE FROM album_photos WHERE album_id IN ({ph})", batch)
    conn.executemany(
        "INSERT OR IGNORE INTO album_photos (album_id, photo_id) VALUES (?, ?)",
        [(aid, pid) for aid, pids in photos_by_album.items() for pid in sorted(pids & alive)],
    )


#: True when an albums row's config holds stashed tags. The one predicate
#: for it, shared by the switch and the refresh so they never disagree.
HAS_STASH_SQL = (
    f"(CASE WHEN json_valid(config_json) "
    f"THEN json_type(config_json, '$.{STASHED_TAGS}') END IS NOT NULL)"
)


def hold_albums(conn: sqlite3.Connection, photos_by_album: dict[int, set[int]]) -> None:
    """Keep these albums from automatic placement until these photos have a
    face on record again (see :data:`HELD_FOR`)."""
    photos_by_album = {aid: pids for aid, pids in photos_by_album.items() if pids}
    cfgs = _album_configs(conn, list(photos_by_album))
    for aid, pids in photos_by_album.items():
        cfgs.setdefault(aid, {})[HELD_FOR] = sorted(pids)
    _write_configs(conn, cfgs)


def held_albums(conn: sqlite3.Connection, album_ids: list[int]) -> set[int]:
    """Which of these albums are still held: a held photo still in the
    library (not trashed, not missing) has no face row and no switch has
    processed it since (see :func:`release_holds`)."""
    held: dict[int, set[int]] = {}
    for aid, cfg in _album_configs(conn, album_ids).items():
        if cfg.get(HELD_FOR):
            held[aid] = {int(p) for p in cfg[HELD_FOR]}
    if not held:
        return set()
    pids = set().union(*held.values())
    with_faces = {
        int(r[0])
        for r in rows_in(
            conn, "SELECT DISTINCT photo_id FROM face_embeddings WHERE photo_id IN ({ph})", pids
        )
    }
    in_library = {
        int(r[0])
        for r in rows_in(
            conn,
            "SELECT id FROM photos WHERE id IN ({ph}) AND deleted_at IS NULL AND missing = 0",
            pids,
        )
    }
    return {aid for aid, photos in held.items() if (photos & in_library) - with_faces}


def release_holds(conn: sqlite3.Connection, processed: set[int]) -> None:
    """Photos a run processed (read fine, faces or none): they no longer
    hold anyone. Does not commit."""
    if not processed:
        return
    rows = conn.execute(
        f"SELECT id, config_json FROM albums WHERE album_type='smart_person' AND {HAS_HOLD_SQL}"
    ).fetchall()
    cfgs: dict[int, dict] = {}
    for aid, raw in rows:
        cfg = safe_json_loads(raw, default=None, expected_type=dict, context="album config")
        if not isinstance(cfg, dict):
            continue
        left = [p for p in cfg.get(HELD_FOR, []) if int(p) not in processed]
        if left != cfg.get(HELD_FOR):
            if left:
                cfg[HELD_FOR] = left
            else:
                cfg.pop(HELD_FOR, None)
            cfgs[int(aid)] = cfg
    _write_configs(conn, cfgs)


def albums_with_stashed_tags(conn: sqlite3.Connection, album_ids: list[int]) -> set[int]:
    """Which of these albums hold stashed manual tags (an unplaced person)."""
    return {
        int(r[0])
        for r in rows_in(
            conn, f"SELECT id FROM albums WHERE id IN ({{ph}}) AND {HAS_STASH_SQL}", album_ids
        )
    }


def restore_unplaced_tags(conn: sqlite3.Connection, placed: dict[int, int]) -> int:
    """Albums placed on clusters (``{album_id: cluster_id}``, by the
    smart-album refresh): put their stashed manual tags back. Returns how
    many tag rows were written."""
    rows = [
        (pid, placed[aid])
        for aid, pids in pop_unplaced_tags(conn, list(placed)).items()
        for pid in sorted(pids)
    ]
    # A tagged photo deleted meanwhile has no row to point at (the tag
    # table's foreign key would refuse it): its tag is gone with it.
    alive = alive_photo_ids(conn, {pid for pid, _ in rows})
    rows = [(pid, cid) for pid, cid in rows if pid in alive]
    if rows:
        conn.executemany(
            "INSERT OR IGNORE INTO photo_person_tags (photo_id, cluster_id) VALUES (?, ?)", rows
        )
        # The refresh built the album's photo list before this ran: the
        # tagged photos join it now, not at the next refresh.
        by_cluster = {cid: aid for aid, cid in placed.items()}
        conn.executemany(
            "INSERT OR IGNORE INTO album_photos (album_id, photo_id) VALUES (?, ?)",
            [(by_cluster[cid], pid) for pid, cid in rows],
        )
    return len(rows)

"""Smart-album refresher + query for the ``smart_sensitive`` album.

The "Sensitive" album is the audit surface for the NudeNet flag: every
photo the model (or the user, via ``sensitive_override``) considers
sensitive, in one place, so false positives/negatives are easy to spot
and correct. It is also the landing surface for the analysis-completion
alert ("12 photos may be sensitive — review").

The membership predicate is ``SENSITIVE_PHOTO_SQL`` from
``bpp/constants.py`` — the same fragment every other surface uses, and
the SQL twin of ``is_sensitive_item`` in ``bpp/web/photo_dict.py``.
Degrades to absent: when nothing qualifies (NudeNet not installed, or a
clean library) the album is removed, mirroring Duplicates.

Own module (not ``smart_album_refreshers``) per the domain-module
pattern set by ``smart_album_people`` / ``smart_album_pets`` — the
refreshers file is at the 500-LOC cap.
"""

from __future__ import annotations

import sqlite3

from bpp.constants import ACTIVE_PHOTO_SQL, sensitive_photo_sql
from bpp.db.settings import resolve_sensitive_threshold
from bpp.utils.logging import get_logger

log = get_logger(__name__)

_ACTIVE = ACTIVE_PHOTO_SQL

ALBUM_TYPE_SENSITIVE = "smart_sensitive"
_SENSITIVE_RULE = {"sensitive": True}


def _sensitive_photo_ids(conn: sqlite3.Connection) -> list[int]:
    predicate = sensitive_photo_sql(resolve_sensitive_threshold(conn))
    rows = conn.execute(f"SELECT id FROM photos WHERE {predicate} AND {_ACTIVE}").fetchall()
    return [r[0] for r in rows]


def _get_sensitive_ids(conn: sqlite3.Connection, _rule: dict) -> list[int]:
    """Return IDs of active photos currently flagged sensitive."""
    return _sensitive_photo_ids(conn)


def _refresh_sensitive_album(conn: sqlite3.Connection) -> None:
    """Create or remove the 'Sensitive' smart album."""
    from bpp.db.smart_album_refreshers import (
        _ensure_smart_album,
        _remove_smart_album_if_exists,
    )

    photo_ids = _sensitive_photo_ids(conn)
    if photo_ids:
        _ensure_smart_album(
            conn,
            name="Sensitive",
            album_type=ALBUM_TYPE_SENSITIVE,
            rule=_SENSITIVE_RULE,
            photo_ids=photo_ids,
        )
    else:
        _remove_smart_album_if_exists(conn, ALBUM_TYPE_SENSITIVE, _SENSITIVE_RULE)

"""Content hashes by path, for the content-addressed caches."""

from __future__ import annotations

from typing import Any


def get_sha256_map(conn: Any, filepaths: list[str]) -> dict[str, str]:
    """Return ``{filepath: truncated_sha256}`` for inpaint cache key lookup."""
    if not filepaths:
        return {}
    from bpp.constants import HASH_PREFIX_LEN
    from bpp.db.sql_batches import in_batches

    result: dict[str, str] = {}
    for placeholders, batch in in_batches(filepaths):
        rows = conn.execute(
            f"SELECT filepath, sha256 FROM photos WHERE filepath IN ({placeholders})",
            batch,
        ).fetchall()
        for r in rows:
            if r[1]:
                result[r[0]] = r[1][:HASH_PREFIX_LEN]
    return result

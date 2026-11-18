"""Content hashes by path, for the content-addressed caches."""

from __future__ import annotations

import re
from typing import Any

from bpp.constants import HASH_PREFIX_LEN

_SHA256_RE = re.compile(r"[0-9a-f]{64}")


def content_cache_key(sha256: object) -> str | None:
    """The cache-name prefix for a stored content hash, or None.

    The ``sha256`` column is read back from the library database, which
    travels with the library folder. Only a well-formed hash may become
    part of a file name: anything else (a path, a short or upper-case
    value) would point the content-addressed cache outside its folder.
    """
    if isinstance(sha256, str) and _SHA256_RE.fullmatch(sha256):
        return sha256[:HASH_PREFIX_LEN]
    return None


def get_sha256_map(conn: Any, filepaths: list[str]) -> dict[str, str]:
    """Return ``{filepath: truncated_sha256}`` for inpaint cache key lookup."""
    if not filepaths:
        return {}
    from bpp.db.sql_batches import in_batches

    result: dict[str, str] = {}
    for placeholders, batch in in_batches(filepaths):
        rows = conn.execute(
            f"SELECT filepath, sha256 FROM photos WHERE filepath IN ({placeholders})",
            batch,
        ).fetchall()
        for r in rows:
            key = content_cache_key(r[1])
            if key is not None:
                result[r[0]] = key
    return result

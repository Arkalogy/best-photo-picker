"""Content hashes by path, for the content-addressed caches."""

from __future__ import annotations

from typing import Any


def get_sha256_map(conn: Any, filepaths: list[str]) -> dict[str, str]:
    """Return ``{filepath: truncated_sha256}`` for inpaint cache key lookup."""
    if not filepaths:
        return {}
    from bpp.constants import HASH_PREFIX_LEN, SQL_BATCH_SIZE

    result: dict[str, str] = {}
    for i in range(0, len(filepaths), SQL_BATCH_SIZE):
        batch = filepaths[i : i + SQL_BATCH_SIZE]
        placeholders = ",".join("?" * len(batch))
        rows = conn.execute(
            f"SELECT filepath, sha256 FROM photos WHERE filepath IN ({placeholders})",
            batch,
        ).fetchall()
        for r in rows:
            if r[1]:
                result[r[0]] = r[1][:HASH_PREFIX_LEN]
    return result

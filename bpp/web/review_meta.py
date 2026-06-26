"""Photo metadata for the face-review popups.

The face-review surfaces (People review "Is this X?" and the "Same
person?" pair wizard) show tiny face crops the user can't judge without
context. Attaching the source photo's filename, timestamp, and score to
each representative crop makes the decision legible — the same context the
compare overlay already shows for dedup/Moments pruning.

One batched query keyed by filepath; never call this inside a loop.
"""

from __future__ import annotations

import sqlite3
from typing import Any


def photo_meta_by_filepaths(
    conn: sqlite3.Connection, filepaths: list[str]
) -> dict[str, dict[str, Any]]:
    """Map filepath -> {filename, date, score} for the given photos.

    Deduplicates and drops falsy paths. Missing rows are simply absent
    from the result (callers treat a missing entry as "no meta").
    """
    fps = [fp for fp in dict.fromkeys(filepaths) if fp]
    if not fps:
        return {}
    placeholders = ",".join("?" * len(fps))
    rows = conn.execute(
        f"SELECT filepath, original_filename, date, aggregate_score "
        f"FROM photos WHERE filepath IN ({placeholders})",
        fps,
    ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        fp = r["filepath"]
        out[fp] = {
            "filename": r["original_filename"] or fp.rsplit("/", 1)[-1],
            "date": r["date"],
            "score": r["aggregate_score"],
        }
    return out


def attach_photo_meta(rep: dict[str, Any], meta: dict[str, dict[str, Any]]) -> None:
    """Copy a representative's {filename, date, score} onto `rep` in place.

    `rep` must carry a `filepath`; no-op if it's missing or unknown.
    """
    info = meta.get(rep.get("filepath", ""))
    if not info:
        return
    rep["filename"] = info["filename"]
    rep["date"] = info["date"]
    rep["score"] = info["score"]

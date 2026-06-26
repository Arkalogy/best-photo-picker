"""Sidecar metadata writers — XMP, JSON manifest, and the score→rating /
score→label mapping the XMP template consumes.

Extracted from ``bpp.output.export`` during the v0.1 cleanup.
Re-exported from ``bpp.output.export`` for back-compat.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

from bpp import APP_NAME
from bpp.utils.logging import get_logger

log = get_logger(__name__)


def _swap_ext(path: str, new_ext: str) -> str:
    """Swap the extension on *path* to *new_ext* (must include the dot)."""
    return os.path.splitext(path)[0] + new_ext


def score_to_rating(score: float) -> int:
    """Map aggregate score (0-1) to XMP star rating (1-5).

    Ranges: 0-0.2=1, 0.2-0.4=2, 0.4-0.6=3, 0.6-0.8=4, 0.8-1.0=5.
    """
    if score < 0.2:
        return 1
    if score < 0.4:
        return 2
    if score < 0.6:
        return 3
    if score < 0.8:
        return 4
    return 5


def score_to_label(score: float) -> str:
    """Map aggregate score to XMP color label.

    Red (<0.3), Yellow (0.3-0.6), Green (0.6-0.8), Blue (>=0.8).
    """
    if score < 0.3:
        return "Red"
    if score < 0.6:
        return "Yellow"
    if score < 0.8:
        return "Green"
    return "Blue"


_XMP_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
    <rdf:Description
      xmlns:xmp="http://ns.adobe.com/xap/1.0/"
      xmlns:xmpRights="http://ns.adobe.com/xap/1.0/rights/"
      xmp:Rating="{rating}"
      xmp:Label="{label}">
      <xmp:Description>{description}</xmp:Description>
    </rdf:Description>
  </rdf:RDF>
</x:xmpmeta>
"""


def _iter_weighted_score_pairs() -> list[tuple[str, str]]:
    """yield (short_key, full_field) pairs for every
    weighted scorer in the registry. Short key is the human-friendly
    name used in the manifest (`"blur"`); full field matches the
    column in the photo dict (`"blur_score"`).

    A plugin contributor who registers a new weighted scorer (e.g.
    a "saturation" scorer with `weight_key="saturation_weight"` and
    `api_fields={"saturation_score": 0}`) automatically gets its
    column included in every report / manifest / XMP description —
    no callsite edits needed.
    """
    from bpp.scoring.registry import get_weighted_scorers

    pairs: list[tuple[str, str]] = []
    for s in get_weighted_scorers():
        # The api_fields dict's first key is the canonical export
        # field. By convention every weighted scorer's first
        # api_fields entry is `<key>_score`, but read it from the
        # registry rather than hardcoding the f-string.
        full = next(iter(s.api_fields.keys())) if s.api_fields else f"{s.key}_score"
        pairs.append((s.key, full))
    return pairs


def write_xmp_sidecar(
    photo_path: str,
    scores: dict[str, float],
    *,
    score_pairs: list[tuple[str, str]] | None = None,
) -> str:
    """Write an XMP sidecar file next to the photo. Returns the XMP path.

    the per-scorer breakdown line iterates the scorer
    registry rather than hardcoding blur/exposure/face/composition.
    Custom scorers show up automatically.

    callers in a tight loop (``export_selected``) can
    pass a pre-snapshotted ``score_pairs`` to avoid re-walking the
    registry per photo. Standalone callers (tests, ad-hoc scripts)
    omit the kwarg and pay the per-call walk.
    """
    if score_pairs is None:
        score_pairs = _iter_weighted_score_pairs()
    aggregate = scores.get("aggregate_score", 0)
    rating = score_to_rating(aggregate)
    label = score_to_label(aggregate)

    breakdown = " ".join(f"{short}={scores.get(full, 0):.2f}" for short, full in score_pairs)
    description = f"Score: {aggregate:.2f} ({breakdown}) — {APP_NAME}"

    xmp_path = _swap_ext(photo_path, ".xmp")
    content = _XMP_TEMPLATE.format(rating=rating, label=label, description=description)
    with open(xmp_path, "w", encoding="utf-8") as f:
        f.write(content)
    return xmp_path


def _safe_source_name(src: str, library_path: str) -> str:
    """produce a recipient-safe filename/path for export reports.

    Returns the source's path relative to ``library_path`` when the
    source lives inside the library; otherwise just the basename.
    Never returns an absolute path. Used for manifest.json and
    report.json/csv values, which the user may share alongside the
    exported photos — absolute paths there leak the owner's
    username, drive layout, and any private folder names embedded
    in the path."""
    if library_path:
        try:
            rel = os.path.relpath(src, library_path)
        except ValueError:
            # Different drives on Windows; fall back to basename.
            return os.path.basename(src)
        # `os.path.relpath` happily walks above the library
        # ("../../foo.jpg") for sources outside it — that still leaks
        # parent directory names, so refuse and fall back to basename.
        if rel.startswith("..") or os.path.isabs(rel):
            return os.path.basename(src)
        return rel
    return os.path.basename(src)


def write_json_manifest(
    outdir: str,
    photos_data: list[dict[str, Any]],
    library_path: str,
    include_source_paths: bool = False,
    *,
    score_pairs: list[tuple[str, str]] | None = None,
) -> str:
    """Write a manifest.json alongside exported photos. Returns the manifest path.

    By default emits sanitized recipient-facing names only — see
    ``_safe_source_name``. Pass ``include_source_paths=True`` to
    keep the absolute ``library`` and per-photo ``original_path``
    fields.

    ``score_pairs`` is an optional pre-snapshotted
    registry walk; ``export_selected`` passes its cached snapshot to
    avoid re-walking. Omit to fall back to the per-call walk.
    """
    if score_pairs is None:
        score_pairs = _iter_weighted_score_pairs()

    def _score_dict(entry: dict[str, Any]) -> dict[str, Any]:
        # aggregate plus every weighted scorer from the
        # registry. Plugin scorers slot in automatically.
        scores = {"aggregate": entry["scores"].get("aggregate_score", 0)}
        for short, full in score_pairs:
            scores[short] = entry["scores"].get(full, 0)
        return scores

    manifest: dict[str, Any] = {
        "exported_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(photos_data),
        "photos": [
            {
                "filename": entry["dest_name"],
                "original_filename": _safe_source_name(entry["original_path"], library_path),
                "scores": _score_dict(entry),
                "rank": entry["rank"],
            }
            for entry in photos_data
        ],
    }
    if include_source_paths:
        manifest["library"] = library_path
        for src_entry, dest_entry in zip(photos_data, manifest["photos"], strict=True):
            dest_entry["original_path"] = src_entry["original_path"]
    manifest_path = os.path.join(outdir, "manifest.json")
    tmp_path = manifest_path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(manifest, f, indent=2)
    os.replace(tmp_path, manifest_path)
    return manifest_path

"""The files written next to an export's photos: the manifest and the
report pair (json + csv). Split out of :mod:`bpp.output.export` for
the 500-LOC cap.
"""

from __future__ import annotations

import csv
import json
import os
from typing import Any

from bpp import __version__
from bpp.output.export_metadata import _safe_source_name, write_json_manifest
from bpp.utils.fresh_file import replace_fresh
from bpp.utils.logging import get_logger
from bpp.utils.retry import retry_io

log = get_logger(__name__)


def _write_manifest(
    outdir: str,
    exported: list[dict[str, Any]],
    score_pairs: list[tuple[str, str]],
    library_path: str,
    include_source_paths: bool,
) -> None:
    """manifest.json (optional). Builds the per-photo `scores` dict from
    the registry — every weighted scorer + the aggregate, no hardcoded
    keys — reusing the `score_pairs` snapshot instead of walking the
    registry a second time."""

    def _full_scores(item: dict[str, Any]) -> dict[str, Any]:
        scores = {"aggregate_score": item.get("aggregate_score", 0)}
        for _short, full in score_pairs:
            scores[full] = item.get(full, 0)
        return scores

    manifest_data = [
        {
            "dest_name": e["dest"],
            "original_path": e["source"],
            "scores": _full_scores(e["item"]),
            "rank": e["index"],
        }
        for e in exported
    ]
    write_json_manifest(
        outdir,
        manifest_data,
        library_path,
        include_source_paths=include_source_paths,
        score_pairs=score_pairs,
    )


def _csv_safe(value: Any) -> Any:
    """A text cell that a spreadsheet would run as a formula (file names
    are untrusted) is prefixed with an apostrophe; numbers pass through."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def _write_reports(
    outdir: str,
    selected: list[dict[str, Any]],
    analysis: list[dict[str, Any]],
    config: dict[str, Any],
    score_pairs: list[tuple[str, str]],
    library_path: str,
    include_source_paths: bool,
) -> str:
    """report.json + report.csv. Returns the JSON report's path."""
    selected_paths = {s["filepath"] for s in selected}

    def _entry_filename(item: dict[str, Any]) -> str:
        """Recipient-safe name. ``include_source_paths`` opts back in
        to the absolute path — the report.json contract still uses
        the ``filepath`` key for the value."""
        if include_source_paths:
            return item["filepath"]
        return _safe_source_name(item["filepath"], library_path)

    # every weighted scorer's full `<key>_score` field name
    # comes from the registry, so a custom scorer's column lands in
    # both report.json and the CSV without callsite edits.
    # reuse the `score_pairs` snapshot from the top of
    # `export_selected` — third walk of the registry per export, now
    # the same first walk's result.
    report_full_keys = [full for _short, full in score_pairs]

    def _selected_entry(i: int, item: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {
            "index": i + 1,
            "filepath": _entry_filename(item),
            "date": item.get("date", ""),
            "aggregate_score": item.get("aggregate_score", 0),
        }
        for full in report_full_keys:
            out[full] = item.get(full, 0)
        out["selection_reason"] = item.get("selection_reason", "")
        out["cluster_size"] = item.get("cluster_size", 1)
        return out

    report = {
        "version": __version__,
        "config": config,
        "total_analyzed": len(analysis),
        "total_selected": len(selected),
        "selected": [_selected_entry(i, item) for i, item in enumerate(selected)],
        "skipped": [
            {
                "filepath": _entry_filename(item),
                "aggregate_score": item.get("aggregate_score", 0),
            }
            for item in analysis
            if item["filepath"] not in selected_paths
        ],
    }

    report_path = os.path.join(outdir, "report.json")

    def _write_json_report():
        with replace_fresh(report_path, "w") as f:
            json.dump(report, f, indent=2)

    retry_io(_write_json_report, label="write_report_json")

    # Write report.csv. fieldnames mirror the JSON shape.
    csv_fieldnames = [
        "index",
        "filepath",
        "date",
        "aggregate_score",
        *report_full_keys,
        "selection_reason",
    ]
    csv_path = os.path.join(outdir, "report.csv")

    def _write_csv_report():
        with replace_fresh(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=csv_fieldnames)
            writer.writeheader()
            for entry in report["selected"]:
                writer.writerow({k: _csv_safe(entry.get(k, "")) for k in writer.fieldnames})

    retry_io(_write_csv_report, label="write_report_csv")
    return report_path

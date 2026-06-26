"""Export selected photos and generate reports."""

from __future__ import annotations

import csv
import errno
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from bpp import __version__
from bpp.output.export_metadata import (
    _iter_weighted_score_pairs,
    _safe_source_name,
    score_to_label,
    score_to_rating,
    write_json_manifest,
    write_xmp_sidecar,
)
from bpp.output.export_modes import (  # noqa: F401 — re-exported for plugins/back-compat
    _METADATA_STRIP_EXTS,
    ZIP_BUNDLE_NAME,
    ExportMode,
    ExportModeRegistry,
    _copy_image_without_metadata,
    _export_copy,
    _needs_processing,
    _process_image,
    bundle_selected_zip,
    register_export_mode,
)
from bpp.utils.logging import get_logger
from bpp.utils.paths import safe_join
from bpp.utils.retry import retry_io
from bpp.utils.video import is_video_file

# Re-exported from bpp.output.export_metadata since the v0.1 cleanup.
__all__ = [
    "ExportResult",
    "_iter_weighted_score_pairs",
    "_safe_source_name",
    "score_to_label",
    "score_to_rating",
    "write_json_manifest",
    "write_xmp_sidecar",
]

log = get_logger(__name__)


# OSError errnos that mean "every subsequent write to this output
# directory will fail the same way" — once we see one, there's no
# point burning I/O on the rest of the selection. The UI categorises
# off the `category` so it can show the user "Disk full" instead of a
# generic per-photo failure count.
_FATAL_OSERROR_CATEGORIES: dict[int, str] = {
    errno.ENOSPC: "no_space",
    errno.EDQUOT: "no_space",
    errno.EACCES: "permission",
    errno.EPERM: "permission",
    errno.EROFS: "read_only_fs",
}


@dataclass
class ExportResult:
    """Outcome of an `export_selected` run.

    ``exported`` and ``failed`` are counts. ``disk_error`` is None on
    success and on per-photo failures that don't repeat for every
    subsequent photo (corrupt source file, missing input). When set,
    it carries the category the UI should display and the 1-based index
    of the first failure so the toast can say "stopped at photo N."
    """

    exported: int
    failed: int
    disk_error: dict[str, Any] | None = None

    def __iter__(self):
        # Back-compat for any caller that still tuple-unpacks the
        # legacy `(exported, failed)` return shape. New code should
        # read the fields by name.
        yield self.exported
        yield self.failed


def export_selected(
    selected: list[dict[str, Any]],
    analysis: list[dict[str, Any]],
    outdir: str,
    mode: str = "copy",
    gallery: bool = False,
    config: dict[str, Any] | None = None,
    fmt: str = "original",
    max_size: int | None = None,
    quality: int = 85,
    write_manifest: bool = False,
    write_xmp: bool = False,
    library_path: str = "",
    include_source_paths: bool = False,
    strip_metadata: bool = True,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> ExportResult:
    """Export selected photos and write reports.

    Returns an :class:`ExportResult` carrying export/failure counts and
    a ``disk_error`` category when the loop aborted because every
    further write would have failed the same way (disk full, permission
    denied, read-only filesystem). Per-photo failures with non-fatal
    OSErrors (corrupt source, missing input) are counted under
    ``failed`` and the loop continues.

    For back-compat, ``ExportResult`` is iterable so existing callers
    that tuple-unpack ``(exported, failed)`` keep working — new code
    should read fields by name.

    By default the report and manifest files emit only filenames
    (or paths relative to ``library_path``), never absolute source
    paths — recipients of the export folder shouldn't see the
    owner's username, drive layout, or private folder names in
    these sidecar files. Pass ``include_source_paths=True``
    to opt in to absolute paths for diagnostics. Image exports in copy/original
    mode strip embedded metadata by default; pass ``strip_metadata=False`` to
    preserve original image bytes intentionally."""
    if config is None:
        config = {}

    # Merge into the existing folder rather than wiping it. The previous
    # behavior (rmtree on force, refuse otherwise) was a foot-gun: if the
    # user pointed at ~/Downloads or any folder with unrelated files,
    # 'Overwrite' silently nuked everything. Now we create the folder if
    # missing and let individual photo writes overwrite same-named files
    # in place. Non-export files in the destination are preserved.
    selected_dir = os.path.join(outdir, "selected")
    os.makedirs(selected_dir, exist_ok=True)

    # "zip" is a bundle mode: write each photo exactly as copy mode would
    # (so format/resize/strip-metadata all apply — Option A), then pack
    # the staging folder into a single archive after the loop. The
    # per-photo write therefore uses the "copy" code path.
    bundle_zip = mode == "zip"
    effective_mode = "copy" if bundle_zip else mode

    process = _needs_processing(effective_mode, fmt, max_size)

    # snapshot the weighted-scorer registry ONCE per
    # export run instead of re-walking it for every photo's XMP
    # sidecar plus three more times for manifest / report. With N
    # photos and write_xmp=True the previous shape was N+3 walks;
    # now it's 1.
    score_pairs = _iter_weighted_score_pairs()

    # Export files
    exported = []
    failed = []
    disk_error: dict[str, Any] | None = None
    total = len(selected)
    for i, item in enumerate(selected, 1):
        src = item["filepath"]
        orig_name = os.path.basename(src)
        dest_name = f"{i:03d}_{orig_name}"
        dest = safe_join(selected_dir, dest_name)
        # L-S3: progress callback fires per-photo so the UI can show
        # determinate progress on long exports (100+ photos with
        # format conversion ran for 30-60s with no feedback before).
        # Fail-soft: a misbehaving callback can't break the export.
        if on_progress is not None:
            try:
                on_progress(i, total, orig_name)
            except Exception:
                log.debug("export progress callback raised", exc_info=True)

        is_vid = item.get("is_video") or is_video_file(src)
        try:
            if process and not is_vid:
                dest = _process_image(src, dest, fmt, max_size, quality)
                dest_name = os.path.basename(dest)
            else:
                # Mode dispatch goes through ExportModeRegistry so
                # plugin-registered modes work the same as built-ins.
                # Unknown modes (typos / legacy clients) fall back to
                # copy.
                ext = os.path.splitext(src)[1].lower()
                if (
                    effective_mode == "copy"
                    and strip_metadata
                    and not is_vid
                    and ext in _METADATA_STRIP_EXTS
                ):
                    _copy_image_without_metadata(src, dest, quality)
                else:
                    mode_def = ExportModeRegistry.get(effective_mode)
                    handler = mode_def.handler if mode_def else _export_copy
                    handler(src, dest)
            exported.append({"index": i, "source": src, "dest": dest_name, "item": item})
            if write_xmp:
                # pass the full item dict; write_xmp_sidecar
                # iterates the scorer registry to pick out the fields
                # it needs. Adding a new weighted scorer doesn't
                # require a callsite edit here.
                # pass the cached snapshot so the inner
                # call doesn't re-walk the registry per photo.
                write_xmp_sidecar(
                    os.path.join(selected_dir, dest_name),
                    item,
                    score_pairs=score_pairs,
                )
        except OSError as e:
            log.warning("Failed to export %s: %s", src, e)
            failed.append({"index": i, "source": src, "error": str(e)})
            category = _FATAL_OSERROR_CATEGORIES.get(e.errno or -1)
            if category is not None:
                disk_error = {
                    "category": category,
                    "errno": e.errno,
                    "first_failed_index": i,
                    "message": str(e),
                }
                log.warning(
                    "Aborting export at photo %d: %s (errno=%s)",
                    i,
                    category,
                    e.errno,
                )
                break

    if failed:
        log.warning("Export completed with %d failures out of %d", len(failed), len(selected))

    # Write manifest.json (optional). build the per-photo
    # `scores` dict from the registry — every weighted scorer + the
    # aggregate, no hardcoded keys. reuse the
    # `score_pairs` snapshot from the top of `export_selected`
    # instead of walking the registry a second time.
    if write_manifest and exported:

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

    # Write report.json
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

    def _write_json_report():
        tmp = report_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(report, f, indent=2)
        os.replace(tmp, report_path)

    report_path = os.path.join(outdir, "report.json")
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

    def _write_csv_report():
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=csv_fieldnames)
            writer.writeheader()
            for entry in report["selected"]:
                writer.writerow({k: entry.get(k, "") for k in writer.fieldnames})

    csv_path = os.path.join(outdir, "report.csv")
    retry_io(_write_csv_report, label="write_report_csv")

    log.info("Exported %d photos to %s", len(exported), selected_dir)
    log.info("Report: %s", report_path)

    # ZIP bundle: pack the staging folder into one archive and drop the
    # loose folder so the user gets a single hand-off file. A
    # folder-relative gallery can't ride inside the zip, so it's skipped
    # in bundle mode (below). On a zip write failure we keep the loose
    # folder — the export isn't lost, just not bundled.
    if bundle_zip and exported and disk_error is None:
        archive_path = os.path.join(outdir, ZIP_BUNDLE_NAME)
        try:
            bundle_selected_zip(selected_dir, archive_path, [e["dest"] for e in exported])
            import shutil

            shutil.rmtree(selected_dir, ignore_errors=True)
            log.info("Bundled %d photos into %s", len(exported), archive_path)
        except OSError as e:
            log.warning("Failed to bundle export into %s: %s", archive_path, e)
            category = _FATAL_OSERROR_CATEGORIES.get(e.errno or -1)
            if category is not None:
                disk_error = {
                    "category": category,
                    "errno": e.errno,
                    "first_failed_index": 0,
                    "message": str(e),
                }

    if gallery and not bundle_zip:
        from bpp.output.gallery import generate_gallery

        generate_gallery(selected, outdir)

    return ExportResult(
        exported=len(exported),
        failed=len(failed),
        disk_error=disk_error,
    )

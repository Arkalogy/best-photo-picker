"""Export selected photos and generate reports."""

from __future__ import annotations

import errno
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

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
    metadata_not_strippable,
    register_export_mode,
)
from bpp.output.export_reports import _write_manifest, _write_reports
from bpp.utils.logging import get_logger
from bpp.utils.paths import safe_join
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
    #: One entry per failed photo: ``{"index", "source", "error"}``.
    failures: list[dict[str, Any]] = field(default_factory=list)
    #: Files exported with their metadata although removal was asked for
    #: (videos: the export doesn't re-encode them). Reported, never silent.
    metadata_kept: int = 0

    def __iter__(self):
        # Back-compat for any caller that still tuple-unpacks the
        # legacy `(exported, failed)` return shape. New code should
        # read the fields by name.
        yield self.exported
        yield self.failed


@dataclass(frozen=True)
class _WritePlan:
    """How each photo is written: the options of one export run."""

    mode: str  # the per-photo mode ("copy" when bundling to zip)
    fmt: str
    max_size: int | None
    quality: int
    strip_metadata: bool
    process: bool  # format / resize conversion needed


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
    # "zip" is a bundle mode: write each photo exactly as copy mode would
    # (so format/resize/strip-metadata all apply — Option A), then pack
    # the staging folder into a single archive after the loop. The
    # per-photo write therefore uses the "copy" code path. The staging
    # folder is this export's own temporary one, so bundling removes
    # only what this export wrote — never an earlier export's files in
    # ``selected/``.
    bundle_zip = mode == "zip"
    if bundle_zip:
        import tempfile

        os.makedirs(outdir, exist_ok=True)
        selected_dir = tempfile.mkdtemp(prefix=".bundle-", dir=outdir)
    else:
        selected_dir = os.path.join(outdir, "selected")
        os.makedirs(selected_dir, exist_ok=True)
    effective_mode = "copy" if bundle_zip else mode
    plan = _WritePlan(
        mode=effective_mode,
        fmt=fmt,
        max_size=max_size,
        quality=quality,
        strip_metadata=strip_metadata,
        process=_needs_processing(effective_mode, fmt, max_size),
    )

    # snapshot the weighted-scorer registry ONCE per
    # export run instead of re-walking it for every photo's XMP
    # sidecar plus three more times for manifest / report. With N
    # photos and write_xmp=True the previous shape was N+3 walks;
    # now it's 1.
    score_pairs = _iter_weighted_score_pairs()

    try:
        exported, failed, disk_error, metadata_kept = _write_photos(
            selected, selected_dir, plan, score_pairs, write_xmp, on_progress
        )
        if failed:
            log.warning("Export completed with %d failures out of %d", len(failed), len(selected))

        if write_manifest and exported:
            _write_manifest(outdir, exported, score_pairs, library_path, include_source_paths)

        report_path = _write_reports(
            outdir, selected, analysis, config, score_pairs, library_path, include_source_paths
        )
        log.info("Exported %d photos to %s", len(exported), selected_dir)
        log.info("Report: %s", report_path)

        if bundle_zip and exported and disk_error is None:
            disk_error = _bundle_zip(outdir, selected_dir, exported)
            if disk_error is not None:
                # The zip failed: keep the photos, under a name the user
                # can see, instead of in the hidden staging folder.
                selected_dir = _keep_staging_visible(outdir, selected_dir)
    finally:
        if (
            bundle_zip
            and os.path.isdir(selected_dir)
            and os.path.basename(selected_dir).startswith(".bundle-")
        ):
            import shutil

            shutil.rmtree(selected_dir, ignore_errors=True)

    if gallery and not bundle_zip:
        from bpp.output.gallery import generate_gallery

        generate_gallery(selected, outdir)

    return ExportResult(
        exported=len(exported),
        failed=len(failed),
        disk_error=disk_error,
        failures=failed,
        metadata_kept=metadata_kept,
    )


def _fatal_disk_error(e: OSError, first_failed_index: int) -> dict[str, Any] | None:
    """The ``disk_error`` payload for an OSError that will repeat for
    every further write, else None."""
    category = _FATAL_OSERROR_CATEGORIES.get(e.errno or -1)
    if category is None:
        return None
    return {
        "category": category,
        "errno": e.errno,
        "first_failed_index": first_failed_index,
        "message": str(e),
    }


def _write_one(src: str, dest: str, is_vid: bool, plan: _WritePlan) -> str:
    """Write one photo; returns the destination path actually written
    (a format conversion can change the extension)."""
    if plan.process and not is_vid:
        return _process_image(
            src, dest, plan.fmt, plan.max_size, plan.quality, keep_exif=not plan.strip_metadata
        )
    # Mode dispatch goes through ExportModeRegistry so
    # plugin-registered modes work the same as built-ins.
    # Unknown modes (typos / legacy clients) fall back to
    # copy.
    ext = os.path.splitext(src)[1].lower()
    if plan.mode == "copy" and plan.strip_metadata and not is_vid:
        # Removal was asked for: an image type the export can't strip
        # fails rather than shipping its metadata (videos, and the link
        # modes, are counted by the caller instead).
        if ext not in _METADATA_STRIP_EXTS:
            raise metadata_not_strippable(src, "this type can't be re-encoded")
        return _copy_image_without_metadata(src, dest, plan.quality)
    else:
        mode_def = ExportModeRegistry.get(plan.mode)
        handler = mode_def.handler if mode_def else _export_copy
        handler(src, dest)
    return dest


def _keeps_metadata(plan: _WritePlan, is_vid: bool) -> bool:
    """A file written with its metadata although removal was asked for:
    a video (never re-encoded) or any file in a mode that links or hands
    the original bytes over (hardlink, symlink, a plugin mode)."""
    if not plan.strip_metadata:
        return False
    if is_vid:
        return True  # never re-encoded, in any mode
    return not plan.process and plan.mode != "copy"


def _write_photos(
    selected: list[dict[str, Any]],
    selected_dir: str,
    plan: _WritePlan,
    score_pairs: list[tuple[str, str]],
    write_xmp: bool,
    on_progress: Callable[[int, int, str], None] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None, int]:
    """The per-photo loop. Returns ``(exported, failed, disk_error,
    metadata_kept)``; the loop stops at the first OSError every further
    write would repeat."""
    exported: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    disk_error: dict[str, Any] | None = None
    metadata_kept = 0
    total = len(selected)
    for i, item in enumerate(selected, 1):
        src = item["filepath"]
        orig_name = os.path.basename(src)
        dest_name = f"{i:03d}_{orig_name}"
        dest = safe_join(selected_dir, dest_name)
        # Progress callback fires per-photo so the UI can show
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
            dest = _write_one(src, dest, bool(is_vid), plan)
            dest_name = os.path.basename(dest)
            exported.append({"index": i, "source": src, "dest": dest_name, "item": item})
            if _keeps_metadata(plan, bool(is_vid)):
                metadata_kept += 1
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
            disk_error = _fatal_disk_error(e, i)
            if disk_error is not None:
                log.warning(
                    "Aborting export at photo %d: %s (errno=%s)",
                    i,
                    disk_error["category"],
                    e.errno,
                )
                break
        except Exception as e:
            # A plugin export handler that raised something other than an
            # OSError: the documented contract is per-photo isolation, so
            # record it and carry on (it says nothing about the disk).
            log.warning("Export handler failed on %s: %s", src, e, exc_info=True)
            failed.append({"index": i, "source": src, "error": str(e)})
    return exported, failed, disk_error, metadata_kept


def _keep_staging_visible(outdir: str, staging_dir: str) -> str:
    """Rename a zip staging folder to a visible, dated ``selected-…``
    folder (the bundle failed; the photos are still the export)."""
    import datetime as _dt

    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    visible = os.path.join(outdir, f"selected-{stamp}")
    try:
        os.replace(staging_dir, visible)
    except OSError as e:
        log.warning("Couldn't rename %s to %s: %s", staging_dir, visible, e)
        return staging_dir
    log.info("Bundle failed; the exported photos are in %s", visible)
    return visible


def _bundle_zip(
    outdir: str, selected_dir: str, exported: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """ZIP bundle: pack the staging folder into one archive and drop the
    loose folder so the user gets a single hand-off file. A
    folder-relative gallery can't ride inside the zip, so it's skipped
    in bundle mode. On a zip write failure we keep the loose folder —
    the export isn't lost, just not bundled. Returns a ``disk_error``
    when the failure would repeat, else None."""
    archive_path = os.path.join(outdir, ZIP_BUNDLE_NAME)
    try:
        bundle_selected_zip(selected_dir, archive_path, [e["dest"] for e in exported])
        import shutil

        shutil.rmtree(selected_dir, ignore_errors=True)
        log.info("Bundled %d photos into %s", len(exported), archive_path)
        return None
    except OSError as e:
        log.warning("Failed to bundle export into %s: %s", archive_path, e)
        return _fatal_disk_error(e, 0)

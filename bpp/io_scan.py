"""Scan input directory for image files."""

from __future__ import annotations

import os
from collections.abc import Callable

from bpp.utils.logging import get_logger

log = get_logger(__name__)

# How often to call on_progress during a recursive walk. Counted by
# *files inspected* (not just matched), so deeply nested empty trees
# still get periodic ticks.
_SCAN_PROGRESS_EVERY = 500


def scan_images(
    input_dir: str,
    extensions: list[str] | None = None,
    follow_symlinks: bool = False,
    max_images: int = 0,
    recursive: bool = False,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[str]:
    """Return sorted list of image file paths in input_dir.

    Skips symlinks unless follow_symlinks is True.
    When recursive is True, walks subdirectories as well.

    M15: ``on_progress(scanned_so_far, matched_so_far)`` is invoked
    every ~500 files inspected during recursive walks so callers can
    surface scan progress on libraries with tens of thousands of files
    instead of presenting a black box. Non-recursive mode is fast
    enough that progress reporting would just be visual noise.
    """
    if extensions is None:
        # pull from the config registry instead of a hardcoded
        # tuple, so plugin overrides (e.g. AVIF support, RAW formats)
        # take effect without patching this file.
        from bpp.config import DEFAULTS, parse_scan_extensions

        extensions = parse_scan_extensions(DEFAULTS["scan_extensions"])

    ext_set = {"." + e.lower().lstrip(".") for e in extensions}
    found: list[str] = []

    # Skip internal directories created by bpp
    _SKIP_DIRS = {"web_thumbs", "face_crops", ".thumbnails", "__pycache__", "data", "cache", "logs"}

    if recursive:
        try:
            walker = os.walk(input_dir, followlinks=follow_symlinks)
        except OSError as e:
            log.warning("Failed to scan %s: %s", input_dir, e)
            return []
        scanned = 0
        for dirpath, _dirnames, filenames in walker:
            if os.path.basename(dirpath) in _SKIP_DIRS:
                continue
            for name in sorted(filenames):
                scanned += 1
                filepath = os.path.join(dirpath, name)
                if not follow_symlinks and os.path.islink(filepath):
                    log.debug("Skipping symlink: %s", filepath)
                else:
                    _, ext = os.path.splitext(name)
                    if ext.lower() in ext_set:
                        found.append(filepath)
                if on_progress is not None and scanned % _SCAN_PROGRESS_EVERY == 0:
                    try:
                        on_progress(scanned, len(found))
                    except Exception:
                        # Caller's progress hook must never break the scan
                        log.debug("scan_images on_progress raised", exc_info=True)
        if on_progress is not None:
            try:
                on_progress(scanned, len(found))
            except Exception:
                log.debug("scan_images on_progress raised", exc_info=True)
        found.sort()
    else:
        try:
            entries = sorted(os.scandir(input_dir), key=lambda e: e.name)
        except OSError as e:
            log.warning("Failed to scan %s: %s", input_dir, e)
            return []
        for entry in entries:
            if not entry.is_file(follow_symlinks=follow_symlinks):
                continue
            if not follow_symlinks and entry.is_symlink():
                log.debug("Skipping symlink: %s", entry.path)
                continue
            _, ext = os.path.splitext(entry.name)
            if ext.lower() not in ext_set:
                continue
            found.append(entry.path)

    if max_images > 0:
        found = found[:max_images]

    return found

"""Library management: import folders, detect missing files."""

from __future__ import annotations

import errno
import hashlib
import os
import shutil
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from bpp.db.photos import bulk_upsert_photos, check_missing
from bpp.io_scan import scan_images
from bpp.utils.logging import get_logger
from bpp.utils.raw import is_raw_file
from bpp.utils.retry import retry_io
from bpp.utils.video import is_video_file

log = get_logger(__name__)

DEFAULT_LIBRARY_PATH = os.path.expanduser("~/Pictures/BestPhotoPicker")
DEFAULT_EXTENSIONS = [
    "jpg",
    "jpeg",
    "png",
    "heic",
    "mp4",
    "mov",
    "avi",
    "mkv",
    "webm",
    "cr2",
    "cr3",
    "nef",
    "arw",
    "orf",
    "raf",
    "rw2",
    "dng",
    "pef",
    "srw",
    "x3f",
]


@dataclass
class ImportResult:
    """Outcome of a folder import."""

    imported: int = 0
    skipped: int = 0
    errors: int = 0
    batch_name: str = ""
    imported_paths: list[str] = field(default_factory=list)


def get_library_path(config: dict[str, Any] | None = None) -> str:
    """Return the library root path from config or default."""
    if config and config.get("library_path"):
        return os.path.abspath(config["library_path"])
    return DEFAULT_LIBRARY_PATH


def get_library_dirs(library_path: str) -> dict[str, str]:
    """Return standard subdirectory paths for a library root.

    All data is organized under the single library_path:
      photos/   — imported photo batches
      data/     — photopicker.db, analysis_cache.db, analysis.json
      cache/    — web_thumbs/, face_crops/
      logs/     — server.log
    """
    library_path = os.path.abspath(library_path)
    return {
        "root": library_path,
        "photos": os.path.join(library_path, "photos"),
        "data": os.path.join(library_path, "data"),
        "cache": os.path.join(library_path, "cache"),
        "logs": os.path.join(library_path, "logs"),
        "thumbs": os.path.join(library_path, "cache", "web_thumbs"),
        "face_crops": os.path.join(library_path, "cache", "face_crops"),
        "pet_crops": os.path.join(library_path, "cache", "pet_crops"),
    }


def ensure_library_dirs(library_path: str) -> dict[str, str]:
    """Create all standard library subdirectories and return their paths."""
    dirs = get_library_dirs(library_path)
    for key in ("photos", "data", "cache", "logs", "thumbs", "face_crops", "pet_crops"):
        os.makedirs(dirs[key], exist_ok=True)
    return dirs


def _sha256_file(filepath: str) -> str:
    """Compute SHA-256 hex digest of a file.

    Retries on transient I/O errors (NAS flakes).
    """
    from bpp.utils.retry import retry_io

    def _read() -> str:
        h = hashlib.sha256()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    return retry_io(_read, label=f"sha256({os.path.basename(filepath)})")


def import_folder(
    conn: sqlite3.Connection,
    source_dir: str,
    library_path: str,
    extensions: list[str] | None = None,
    batch_name: str | None = None,
    on_progress: Callable[[int, int, str, str], None] | None = None,
    import_live_photo_sidecars: bool = False,
) -> ImportResult:
    """Import photos from source_dir into the library.

    - Scans source_dir for images
    - SHA-256 dedup against existing library
    - Copies to library/photos/{batch_name}/ preserving filenames
    - Inserts into photos table

    on_progress(current, total, filename, status) is called after each file.
    status is one of "imported", "skipped", "error".

    import_live_photo_sidecars controls whether iPhone Live Photo motion
    sidecars (IMG_xxxx_1.HEIC / IMG_xxxx_1.JPG) are imported at all.
    Default False — sidecars are silently skipped at scan time when a
    corresponding parent file is present in the same source directory.
    When True, sidecars are imported and linked (is_live_photo_sidecar=1)
    but remain hidden from the grid and smart albums.
    See bpp/db/live_photo.py for the full detection assumptions.
    """
    from bpp.db.live_photo import (
        detect_and_link_live_photo_sidecars,
        filter_sidecar_paths,
    )

    if extensions is None:
        extensions = DEFAULT_EXTENSIONS

    if batch_name is None:
        batch_name = os.path.basename(os.path.abspath(source_dir))
    else:
        # Sanitize: strip path separators and traversal components.
        batch_name = os.path.basename(batch_name)

    photos_dir = get_library_dirs(library_path)["photos"]
    batch_dir = os.path.join(photos_dir, batch_name)
    os.makedirs(batch_dir, exist_ok=True)

    images = scan_images(source_dir, extensions=extensions, recursive=True)

    if not import_live_photo_sidecars:
        images, skipped_sidecars = filter_sidecar_paths(images)
        if skipped_sidecars:
            log.info(
                "Import scan: skipped %d Live Photo sidecar(s) "
                "(enable 'Import Live Photo sidecars' to include them)",
                len(skipped_sidecars),
            )

    total = len(images)
    log.info(
        "Import scan: %d candidate file(s) found in %s (batch=%s)",
        total,
        source_dir,
        batch_name,
    )
    result = ImportResult(batch_name=batch_name)

    # Within-batch dedup set: hashes of photos imported in *this* call.
    # Cross-batch dedup is handled by an indexed SELECT per source file
    # below — pre-loading the full sha256 column was N rows of pure
    # waste when importing 1 photo into a 50K-photo library, and the
    # idx_photos_sha256 index makes the per-row lookup essentially free.
    seen_hashes: set[str] = set()

    imported_photos: list[dict[str, Any]] = []

    for i, src_path in enumerate(images):
        filename = os.path.basename(src_path)
        tmp_path = None
        try:
            sha = _sha256_file(src_path)

            if (
                sha in seen_hashes
                or conn.execute("SELECT 1 FROM photos WHERE sha256 = ? LIMIT 1", (sha,)).fetchone()
            ):
                result.skipped += 1
                if on_progress:
                    on_progress(i + 1, total, filename, "skipped")
                continue

            dest_path = os.path.join(batch_dir, filename)

            # Handle filename collisions within batch
            if retry_io(os.path.exists, dest_path, label="exists_check"):
                name, ext = os.path.splitext(filename)
                counter = 1
                while retry_io(os.path.exists, dest_path, label="exists_check"):
                    dest_path = os.path.join(batch_dir, f"{name}_{counter}{ext}")
                    counter += 1

            # Atomic copy: write to .tmp, verify size, then rename
            tmp_path = dest_path + ".tmp"
            retry_io(shutil.copy2, src_path, tmp_path, label="copy")
            src_size = retry_io(os.path.getsize, src_path, label="src_size")
            tmp_size = retry_io(os.path.getsize, tmp_path, label="tmp_size")
            if src_size != tmp_size:
                os.remove(tmp_path)
                raise OSError(f"Size mismatch after copy: {src_size} vs {tmp_size}")
            os.rename(tmp_path, dest_path)

            stat = retry_io(os.stat, dest_path, label="stat")
            photo_rec: dict[str, Any] = {
                "filepath": dest_path,
                "original_filename": filename,
                "import_batch": batch_name,
                "sha256": sha,
                "file_size": stat.st_size,
                "file_mtime": stat.st_mtime,
            }
            if is_video_file(dest_path):
                photo_rec["is_video"] = 1
                from bpp.utils.video import extract_video_metadata

                vmeta = extract_video_metadata(dest_path)
                if vmeta:
                    photo_rec["video_duration"] = vmeta["duration"]
                    photo_rec["video_width"] = vmeta["width"]
                    photo_rec["video_height"] = vmeta["height"]
                    photo_rec["video_fps"] = vmeta["fps"]
                    photo_rec["video_codec"] = vmeta["codec"]
            if is_raw_file(dest_path):
                photo_rec["is_raw"] = 1
            imported_photos.append(photo_rec)
            seen_hashes.add(sha)
            result.imported += 1
            result.imported_paths.append(dest_path)

            if on_progress:
                on_progress(i + 1, total, filename, "imported")

        except OSError as e:
            if e.errno == errno.ENOSPC:
                log.error("Disk full while importing %s", src_path)
                if tmp_path and os.path.exists(tmp_path):
                    os.remove(tmp_path)
                raise OSError("Disk is full. Free up space and try again.") from e
            log.warning("Failed to import %s: %s", src_path, e)
            result.errors += 1
            if on_progress:
                on_progress(i + 1, total, filename, "error")
        except Exception as e:
            log.warning("Failed to import %s: %s", src_path, e)
            result.errors += 1
            if on_progress:
                on_progress(i + 1, total, filename, "error")

    if imported_photos:
        bulk_upsert_photos(conn, imported_photos)
        # Link any Live Photo sidecars that came in with this batch.
        # Runs even when import_live_photo_sidecars=False because the
        # library may already contain unlinked sidecars from a prior
        # import that pre-dates this feature.
        detect_and_link_live_photo_sidecars(conn)

        # Plugin event bus: post-import fires after the DB commit so
        # plugins can react to the new filepaths (side-cache priming,
        # EXIF enrichment, external mirroring). Best-effort.
        from bpp.db.event_hooks import dispatch_post_import
        from bpp.db.photos import get_photo_id_map_by_paths

        id_map = get_photo_id_map_by_paths(conn, result.imported_paths)
        imported_ids = [id_map[fp] for fp in result.imported_paths if fp in id_map]
        dispatch_post_import(conn, imported_ids, list(result.imported_paths))

    # Always log the import outcome (not just when something was
    # imported) — when a user reports "import didn't do anything",
    # the breadcrumb showing 100% skipped is what tells support
    # whether the run actually executed.
    log.info(
        "Import complete: %d imported, %d skipped, %d errors (batch=%s, dest=%s)",
        result.imported,
        result.skipped,
        result.errors,
        batch_name,
        batch_dir,
    )

    return result


def clear_library(conn: sqlite3.Connection, library_path: str) -> dict[str, int]:
    """Delete all photos from DB and remove imported files from disk.

    Clears: photos, album_photos, face_embeddings, clip_embeddings, dedup_feedback.
    Keeps: albums (structure), presets.
    Removes batch folders under library_path/photos/ (but not the DB).
    Also clears the thumbnail and face_crops caches.
    """
    dirs = get_library_dirs(library_path)

    # Count before delete
    row = conn.execute("SELECT COUNT(*) FROM photos").fetchone()
    count = row[0] if row else 0

    # Get batch folders to remove from disk
    rows = conn.execute(
        "SELECT DISTINCT import_batch FROM photos WHERE import_batch IS NOT NULL"
    ).fetchall()
    batch_names = [r[0] for r in rows]

    log.warning(
        "Clearing library: deleting %d photo(s) across %d batch folder(s) — this is irreversible",
        count,
        len(batch_names),
    )
    # Clear DB tables (CASCADE handles album_photos, face/clip embeddings, feedback)
    conn.execute("DELETE FROM photos")
    # album_photos cleaned by CASCADE, but be explicit for safety
    conn.execute("DELETE FROM album_photos")
    conn.execute("DELETE FROM face_embeddings")
    conn.execute("DELETE FROM clip_embeddings")
    conn.execute("DELETE FROM dedup_feedback")
    conn.commit()

    # Remove batch folders from photos/ dir
    removed_dirs = 0
    for batch in batch_names:
        # Check new layout first, then legacy flat layout
        for base in (dirs["photos"], library_path):
            batch_dir = os.path.join(base, batch)
            if os.path.isdir(batch_dir):
                try:
                    retry_io(shutil.rmtree, batch_dir, label="rmtree_batch")
                    removed_dirs += 1
                    log.info("Removed batch folder: %s", batch_dir)
                except OSError as e:
                    log.warning("Failed to remove batch folder %s: %s", batch_dir, e)
                break

    # Clear cache dirs (thumbnails and face crops)
    for cache_dir in (dirs["thumbs"], dirs["face_crops"]):
        if os.path.isdir(cache_dir):
            try:
                retry_io(shutil.rmtree, cache_dir, label="rmtree_cache")
            except OSError as e:
                log.warning("Failed to remove cache %s: %s", cache_dir, e)
    # Also check legacy flat thumbs location
    legacy_thumbs = os.path.join(library_path, "web_thumbs")
    if os.path.isdir(legacy_thumbs):
        try:
            retry_io(shutil.rmtree, legacy_thumbs, label="rmtree_thumbs")
        except OSError as e:
            log.warning("Failed to remove legacy thumbnail cache %s: %s", legacy_thumbs, e)

    log.info("Cleared library: %d photos, %d batch folders", count, removed_dirs)
    return {"photos_deleted": count, "folders_removed": removed_dirs}


def backfill_sha256(
    conn: sqlite3.Connection,
    on_progress: Callable[[int, int], None] | None = None,
) -> int:
    """Compute SHA-256 for all photos where sha256 IS NULL.

    Returns the number of photos updated.

    Hashing (the slow part — a full file read per photo) runs with NO open
    write transaction; only the tight batched UPDATE+commit holds the write
    lock, for milliseconds. The previous version opened a transaction on the
    first UPDATE and didn't commit for 200 rows, so the write lock was held
    across ~200 slow file reads (>30s on large HEICs) — long enough to blow
    past the 30s busy_timeout and fail any concurrent foreground write
    (e.g. a person rename's smart-album refresh) with "database is locked".
    on_progress(current, total) is called after each file if provided.
    """
    rows = conn.execute(
        "SELECT id, filepath FROM photos WHERE sha256 IS NULL AND missing = 0"
    ).fetchall()
    if not rows:
        return 0

    total = len(rows)
    log.info("SHA-256 backfill: %d photos need hashing", total)
    updated = 0
    pending: list[tuple[str, int]] = []  # (sha, photo_id) awaiting a flush

    def _flush() -> None:
        if not pending:
            return
        # Tight write: open the transaction, write, commit immediately so the
        # write lock is never held while the NEXT batch is being hashed.
        conn.executemany("UPDATE photos SET sha256 = ? WHERE id = ?", pending)
        conn.commit()
        pending.clear()

    try:
        for i, row in enumerate(rows):
            photo_id = row["id"]
            filepath = row["filepath"]
            try:
                if os.path.isfile(filepath):
                    sha = _sha256_file(filepath)  # slow file read — NO open txn
                    pending.append((sha, photo_id))
                    updated += 1
                    if len(pending) >= 200:
                        _flush()
                        log.info("SHA-256 backfill: %d/%d done", updated, total)
            except Exception as e:
                log.warning("SHA-256 backfill failed for %s: %s", filepath, e, exc_info=True)

            if on_progress:
                on_progress(i + 1, total)
    finally:
        _flush()

    log.info("SHA-256 backfill complete: %d/%d updated", updated, total)
    return updated


def check_missing_files(conn: sqlite3.Connection) -> list[str]:
    """Check all non-missing photos for files that no longer exist on disk."""
    return check_missing(conn)

"""Thumbnail caching with path-hash URLs."""

from __future__ import annotations

import hashlib
import os
from typing import Any

from bpp.constants import HASH_PREFIX_LEN, THUMBNAIL_MAX_SIZE
from bpp.output.gallery import _make_thumbnail
from bpp.utils.logging import get_logger

log = get_logger(__name__)

_CACHE_VERSION = "5"  # bumped to clear stale face/pet crops with old 16-char hashes


class ThumbnailCache:
    """Manages thumbnail generation and lookup via path hashes.

    URLs use sha256(filepath)[:16] to prevent path traversal.
    Thumbnails are lazily generated on first request.
    """

    def __init__(self, cache_dir: str, size: int = THUMBNAIL_MAX_SIZE) -> None:
        self.cache_dir = cache_dir
        self.size = size
        self._hash_to_path: dict[str, str] = {}
        self._path_to_hash: dict[str, str] = {}
        self._verified: set[str] = set()  # hashes with confirmed cached thumbs
        os.makedirs(cache_dir, exist_ok=True)
        self._check_cache_version()

    def _check_cache_version(self) -> None:
        """Clear cache if version has changed (e.g. hash length bump).

        Also clears sibling face_crops/ and pet_crops/ dirs since they
        embed the same path hash in their filenames.
        """
        version_file = os.path.join(self.cache_dir, ".version")
        current = None
        if os.path.exists(version_file):
            try:
                with open(version_file) as f:
                    current = f.read().strip()
            except OSError:
                log.warning(
                    "Couldn't read thumbnail cache version file %s; treating the "
                    "cache as stale and rebuilding it",
                    version_file,
                    exc_info=True,
                )
        if current != _CACHE_VERSION:
            self.clear()
            # Clear sibling crop caches that embed the same path hash.
            import shutil

            parent = os.path.dirname(self.cache_dir)
            for sibling in ("face_crops", "pet_crops"):
                sib_dir = os.path.join(parent, sibling)
                if os.path.isdir(sib_dir):
                    shutil.rmtree(sib_dir, ignore_errors=True)
                    os.makedirs(sib_dir, exist_ok=True)
                    log.info("Cleared stale %s cache (hash length changed)", sibling)
            try:
                with open(version_file, "w") as f:
                    f.write(_CACHE_VERSION)
            except OSError:
                log.warning(
                    "Couldn't write thumbnail cache version sentinel %s; the cache "
                    "will be cleared again on the next startup",
                    version_file,
                    exc_info=True,
                )

    def build_map(self, analysis: list[dict[str, Any]]) -> None:
        """Build hash maps from analysis dicts (convenience wrapper)."""
        self.build_map_from_paths([item["filepath"] for item in analysis])

    def build_map_from_paths(self, filepaths: list[str]) -> None:
        """Build hash-to-filepath and filepath-to-hash mappings from paths."""
        self._hash_to_path = {}
        self._path_to_hash: dict[str, str] = {}
        self._add_paths(filepaths)

    def add_to_map(self, analysis: list[dict[str, Any]]) -> None:
        """Add these items' paths to the mappings, keeping what is there
        (a page of a listing, not the whole library)."""
        self._add_paths([item["filepath"] for item in analysis])

    def _add_paths(self, filepaths: list[str]) -> None:
        for fp in filepaths:
            h = self._path_hash(fp)
            self._hash_to_path[h] = fp
            self._path_to_hash[fp] = h

    @staticmethod
    def _path_hash(filepath: str) -> str:
        return hashlib.sha256(filepath.encode()).hexdigest()[:HASH_PREFIX_LEN]

    def get_hash(self, filepath: str) -> str:
        """Get the URL hash for a filepath (O(1) cached lookup)."""
        cached = self._path_to_hash.get(filepath)
        if cached is not None:
            return cached
        return self._path_hash(filepath)

    def get_filepath(self, path_hash: str) -> str | None:
        """Look up original filepath by its URL hash."""
        return self._hash_to_path.get(path_hash)

    def invalidate(self, path_hash: str) -> None:
        """Invalidate a single cached thumbnail so it regenerates on next request."""
        self._verified.discard(path_hash)

    def remove_for_hash(
        self,
        path_hash: str,
        face_crop_dir: str | None = None,
        pet_crop_dir: str | None = None,
        content_hash: str | None = None,
    ) -> int:
        """Remove thumbnail + variants + face/pet crops for a path hash.

        The main cache is swept by the names in `PHOTO_CACHE_SUFFIXES`
        (no directory listing). The crop folders embed a face index and
        bbox tag the caller can't know, so they stay a prefix glob.

        Called during permanent delete to clean up orphaned cache files.

        Args:
            content_hash: truncated SHA-256 of file content. The inpaint
                cache uses content hash; when provided, both content_hash
                and path_hash are swept for backward compatibility.
        """
        import glob as _glob

        from bpp.web.photo_cache import cached_variant_paths

        removed = 0
        hashes = {path_hash}
        if content_hash:
            hashes.add(content_hash)
        for variant in cached_variant_paths(self.cache_dir, hashes):
            try:
                os.remove(variant)
            except FileNotFoundError:
                continue
            except OSError as e:
                log.warning("Couldn't remove cached variant %s: %s", variant, e)
                continue
            removed += 1
        self._verified.discard(path_hash)
        self._hash_to_path.pop(path_hash, None)
        # Face crops: {path_hash}_{face_index}_{bbox_tag}.jpg
        if face_crop_dir and os.path.isdir(face_crop_dir):
            for f in _glob.glob(os.path.join(face_crop_dir, f"{path_hash}_*")):
                os.remove(f)
                removed += 1
        # Pet crops: pet_{path_hash}_{idx}_{bbox_tag}.jpg
        if pet_crop_dir and os.path.isdir(pet_crop_dir):
            for f in _glob.glob(os.path.join(pet_crop_dir, f"pet_{path_hash}_*")):
                os.remove(f)
                removed += 1
        return removed

    @staticmethod
    def remove_crops_for_hashes(
        face_crop_dir: str | None, pet_crop_dir: str | None, path_hashes: set[str]
    ) -> int:
        """Delete the face / pet crops of many photos with one listing per
        folder (``remove_for_hash`` globs per photo: fine for one delete,
        two scans per photo for a purge of thousands)."""
        removed = 0
        width = len(next(iter(path_hashes))) if path_hashes else 0
        for folder, prefix in ((face_crop_dir, ""), (pet_crop_dir, "pet_")):
            if not folder or not width or not os.path.isdir(folder):
                continue
            try:
                names = os.listdir(folder)
            except OSError as e:
                log.warning("Couldn't list crop folder %s: %s", folder, e)
                continue
            for name in names:
                if not name.startswith(prefix):
                    continue
                if name[len(prefix) : len(prefix) + width] not in path_hashes:
                    continue
                if name[len(prefix) + width : len(prefix) + width + 1] != "_":
                    continue
                try:
                    os.remove(os.path.join(folder, name))
                    removed += 1
                except FileNotFoundError:
                    continue
                except OSError as e:
                    log.warning("Couldn't remove crop %s: %s", name, e)
        return removed

    def clear(self) -> int:
        """Delete all cached thumbnails. Returns the count of files actually
        removed; any that couldn't be deleted are logged at WARNING and left
        in place, so the return value can be lower than the number on disk."""
        self._verified.clear()
        count = 0
        try:
            entries = os.listdir(self.cache_dir)
        except OSError as e:
            log.warning("Failed to list thumbnail cache %s: %s", self.cache_dir, e)
            return 0
        for fname in entries:
            if fname == ".version":
                continue
            fpath = os.path.join(self.cache_dir, fname)
            try:
                if os.path.isfile(fpath):
                    os.remove(fpath)
                    count += 1
            except OSError:
                log.warning(
                    "Couldn't delete cached thumbnail %s during cache clear; it was left in place",
                    fpath,
                    exc_info=True,
                )
        return count

    def get_thumbnail(self, path_hash: str) -> str | None:
        """Return path to thumbnail JPEG, generating if needed.

        Returns None if hash is unknown or generation fails.
        Uses an in-memory set to skip stat calls for already-verified thumbnails.
        """
        # Fast path: already verified this thumbnail exists
        if path_hash in self._verified:
            return os.path.join(self.cache_dir, f"{path_hash}.jpg")

        filepath = self._hash_to_path.get(path_hash)
        if filepath is None:
            return None

        thumb_path = os.path.join(self.cache_dir, f"{path_hash}.jpg")
        if os.path.exists(thumb_path):
            # Regenerate if source is newer than cached thumbnail
            try:
                if os.path.getmtime(filepath) <= os.path.getmtime(thumb_path):
                    self._verified.add(path_hash)
                    return thumb_path
            except OSError:
                self._verified.add(path_hash)
                return thumb_path

        if _make_thumbnail(filepath, thumb_path, size=self.size):
            self._verified.add(path_hash)
            return thumb_path

        return None

    def warm_cache(self, limit: int = 0, cancel_event: object = None) -> int:
        """Pre-generate thumbnails for all registered files.

        Runs synchronously — call from a background thread.
        Returns the number of thumbnails generated.

        `cancel_event`: optional `threading.Event`; when set, the loop
        exits at the next iteration. Used by `WebAppState.switch_library`
        to drain warming work before swapping the underlying DB.
        """
        generated = 0
        # A snapshot: a permanent delete on a request thread pops from
        # this dict while the warmer runs.
        for path_hash, filepath in list(self._hash_to_path.items()):
            if cancel_event is not None and cancel_event.is_set():
                break
            if path_hash in self._verified or path_hash not in self._hash_to_path:
                continue
            thumb_path = os.path.join(self.cache_dir, f"{path_hash}.jpg")
            if os.path.exists(thumb_path):
                self._verified.add(path_hash)
                continue
            if _make_thumbnail(filepath, thumb_path, size=self.size):
                self._verified.add(path_hash)
                generated += 1
            if limit and generated >= limit:
                break
        return generated

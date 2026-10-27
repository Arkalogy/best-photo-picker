"""The file side of an import: hashing, the destination slot, the atomic
copy, the photos row. Split out of :mod:`bpp.db.library` for the 500-LOC
cap; ``import_folder`` there drives these.

An import may be killed at any moment (the desktop quit is a SIGKILL
after a short grace period), so nothing here assumes a run finishes:
``_slot_for`` adopts a file an earlier run copied but never recorded,
and ``_remove_stale_tmp`` clears the half-copies such a kill leaves.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import sqlite3
import threading
from collections.abc import Iterator
from typing import Any

from bpp.utils.logging import get_logger
from bpp.utils.raw import is_raw_file
from bpp.utils.retry import retry_io
from bpp.utils.video import is_video_file

log = get_logger(__name__)


def _sha256_file(filepath: str) -> str:
    """Compute SHA-256 hex digest of a file.

    Retries on transient I/O errors (NAS flakes).
    """

    def _read() -> str:
        h = hashlib.sha256()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    return retry_io(_read, label=f"sha256({os.path.basename(filepath)})")


def _slot_taken(path: str) -> bool:
    """Whether a file is at ``path``. A storage error is an error here
    (``os.path.exists`` would read it as "free" and let the copy overwrite
    a photo imported moments ago)."""
    from bpp.db.photos_missing import file_present

    present = file_present(path)
    if present is None:
        raise OSError(f"Storage didn't answer for {path}")
    return present


def _slot_for(
    conn: sqlite3.Connection | None,
    batch_dir: str,
    filename: str,
    sha: str | None,
    src_size: int | None = None,
) -> tuple[str, bool]:
    """``(dest_path, adopt)`` for ``filename`` in the batch folder.

    A taken slot holding this very content is a copy an interrupted
    import made but never recorded: adopt it instead of copying the file
    again as ``name_1``. "Never recorded" means no photos row at that
    path (a recorded photo is someone's scored data, never overwritten);
    the content is compared by size first and hashed only when the sizes
    agree, so a routine same-name collision costs a stat, not a read of
    the whole file. A slot that can't be read (a folder, no permission)
    is simply not ours: numbering steps past it.
    """
    dest_path = os.path.join(batch_dir, filename)
    name, ext = os.path.splitext(filename)
    counter = 1
    while _slot_taken(dest_path):
        if sha is not None and _is_our_unrecorded_copy(conn, dest_path, sha, src_size):
            return dest_path, True
        dest_path = os.path.join(batch_dir, f"{name}_{counter}{ext}")
        counter += 1
    return dest_path, False


def _is_our_unrecorded_copy(
    conn: sqlite3.Connection | None, dest_path: str, sha: str, src_size: int | None
) -> bool:
    if conn is not None:
        try:
            if conn.execute("SELECT 1 FROM photos WHERE filepath=?", (dest_path,)).fetchone():
                return False
        except sqlite3.OperationalError:
            return False
    try:
        if src_size is not None and os.path.getsize(dest_path) != src_size:
            return False
        return _sha256_file(dest_path) == sha
    except OSError:
        return False


def _unique_dest_path(batch_dir: str, filename: str) -> str:
    """The destination for ``filename`` in the batch folder, numbered on
    a collision within the batch (no adoption)."""
    return _slot_for(None, batch_dir, filename, None)[0]


def _copy_atomically(src_path: str, tmp_path: str, dest_path: str) -> None:
    """Atomic copy: write to .tmp, verify size, then rename. On any
    failure the .tmp is removed, so a half-copied file never lingers in
    the batch folder."""
    try:
        retry_io(shutil.copy2, src_path, tmp_path, label="copy")
        src_size = retry_io(os.path.getsize, src_path, label="src_size")
        tmp_size = retry_io(os.path.getsize, tmp_path, label="tmp_size")
        if src_size != tmp_size:
            raise OSError(f"Size mismatch after copy: {src_size} vs {tmp_size}")
        os.rename(tmp_path, dest_path)
    except OSError:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


_active_batches: dict[str, int] = {}  # folder → running imports writing into it
_active_lock = threading.Lock()


@contextlib.contextmanager
def active_import(batch_dir: str) -> Iterator[None]:
    """Mark ``batch_dir`` as written by a running import for the duration;
    the stale-copy sweep leaves such a folder alone. Counted, so two
    imports into one folder keep it marked until both are done."""
    key = os.path.abspath(batch_dir)
    with _active_lock:
        _active_batches[key] = _active_batches.get(key, 0) + 1
    try:
        yield
    finally:
        with _active_lock:
            left = _active_batches.get(key, 1) - 1
            if left > 0:
                _active_batches[key] = left
            else:
                _active_batches.pop(key, None)


def _remove_stale_tmp(batch_dir: str, *, older_than_s: float = 0) -> int:
    """Remove the ``*.tmp`` half-copies a killed import left in the batch
    folder (``_copy_atomically`` cleans up after an error, not after a
    SIGKILL). With ``older_than_s``, a file written more recently is left
    alone: it may be an import in progress. Returns how many were removed."""
    import time

    with _active_lock:
        if os.path.abspath(batch_dir) in _active_batches:
            return 0  # an import is writing here right now
    removed = 0
    cutoff = time.time() - older_than_s
    try:
        names = os.listdir(batch_dir)
    except OSError as e:
        log.warning("Could not list %s for stale copies: %s", batch_dir, e)
        return 0
    for name in names:
        if not name.endswith(".tmp"):
            continue
        path = os.path.join(batch_dir, name)
        try:
            # ctime, not mtime: copy2 gives the half-copy its source's old
            # mtime, while its ctime says when it was actually written.
            if older_than_s and os.stat(path).st_ctime > cutoff:
                continue
            os.remove(path)
            removed += 1
        except OSError as e:
            log.warning("Could not remove stale copy %s: %s", name, e)
    if removed:
        log.info(
            "Removed %d half-copied file(s) from an interrupted import in %s", removed, batch_dir
        )
    return removed


#: A ``.tmp`` younger than this is an import in progress, not a leftover.
STALE_TMP_AGE_S = 600.0


def remove_stale_import_tmp(photos_dir: str, *, older_than_s: float = STALE_TMP_AGE_S) -> int:
    """Sweep every batch folder under ``photos_dir`` for the half-copies a
    killed import left (run when the library opens: the next import may
    go to another batch and never see them). A file younger than
    ``older_than_s`` belongs to a running import and stays. Returns how
    many went."""
    try:
        batches = [os.path.join(photos_dir, d) for d in os.listdir(photos_dir)]
    except OSError as e:
        log.warning("Could not list %s for stale copies: %s", photos_dir, e)
        return 0
    return sum(_remove_stale_tmp(b, older_than_s=older_than_s) for b in batches if os.path.isdir(b))


def _photo_record(dest_path: str, filename: str, batch_name: str, sha: str) -> dict[str, Any]:
    """The photos row for an imported file (video / RAW flags included)."""
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

        # Optional: a probe failure must not orphan the file already in
        # the batch folder (it would be re-copied as name_1 next time).
        try:
            vmeta = extract_video_metadata(dest_path)
        except Exception:
            log.warning("Could not read video metadata for %s", dest_path, exc_info=True)
            vmeta = None
        if vmeta:
            photo_rec["video_duration"] = vmeta["duration"]
            photo_rec["video_width"] = vmeta["width"]
            photo_rec["video_height"] = vmeta["height"]
            photo_rec["video_fps"] = vmeta["fps"]
            photo_rec["video_codec"] = vmeta["codec"]
    if is_raw_file(dest_path):
        photo_rec["is_raw"] = 1
    return photo_rec

"""Archive-extraction helper for the analyze worker.

When a user points the analyzer at a .zip or .tar(.gz) archive rather
than a directory, we extract it into a sibling directory under the
workdir and then analyze the extracted tree. The extraction guards
against archive-bomb behavior (entry-count / total-size caps) and
path-traversal entries (zip ``..`` / tar absolute paths / symlinks /
device nodes).

Imported from ``analyze_worker.py``; not part of the public API.
"""

from __future__ import annotations

import errno
import os
import shutil
import tarfile
import tempfile
import zipfile
import zlib
from collections.abc import Callable
from typing import Any

from bpp.db.analysis_leftovers import EXTRACTED_DIR
from bpp.utils.logging import get_logger
from bpp.utils.paths import is_safe_archive_member, is_safe_tar_member
from bpp.web.archive_placement import (
    UNPACKING as _UNPACKING,
)
from bpp.web.archive_placement import (
    first_clash as _first_clash,
)
from bpp.web.archive_placement import (
    move_into as _move_into,
)
from bpp.web.archive_placement import (
    sweep_unfinished_unpacking,
)

log = get_logger(__name__)

_MAX_ARCHIVE_BYTES = 50 * 1024 * 1024 * 1024  # 50 GB
_MAX_ARCHIVE_MEMBERS = 100_000
_MAX_ARCHIVE_MEMBER_PATH_CHARS = 4096


def _archive_folder(input_path: str) -> str:
    """The folder name one archive is unpacked into: its own, so that
    analysing it does not pick up photos of archives unpacked before."""
    import hashlib
    import re

    stem = (
        re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.basename(input_path))[:60].strip("._") or "archive"
    )
    # By where the archive is, not by its size or date: analysing it
    # again after adding photos lands on the same paths (rows are updated
    # in place, not duplicated).
    identity = os.path.abspath(input_path)
    return f"{stem}-{hashlib.sha256(identity.encode()).hexdigest()[:10]}"


def _real_folder(workdir: str, name: str) -> str:
    """``workdir/extracted/<name>`` as a real folder with no links in it.

    Unpacking writes by name. A link left at one of those names (the
    library folder may have been prepared by someone else) would send
    the archive's files to wherever it points, over the owner's own.
    Links are removed, never followed."""
    base = os.path.join(workdir, EXTRACTED_DIR)
    target = os.path.join(base, name)
    for folder in (base, target):
        if os.path.islink(folder):
            log.warning("Removed a link at %s: archives are unpacked into real folders", folder)
            os.unlink(folder)
        os.makedirs(folder, exist_ok=True)
    for parent, folders, files in os.walk(target):  # does not descend into links
        for entry in [*folders, *files]:
            path = os.path.join(parent, entry)
            if os.path.islink(path):
                log.warning("Removed a link at %s before unpacking", path)
                os.unlink(path)
    return target


def _files_and_folders(
    tf: tarfile.TarFile, input_path: str, emit: Callable[[dict[str, Any]], None]
) -> list[tarfile.TarInfo]:
    """The members of a tar that are unpacked: files and folders only.
    A photo archive needs no links, and a hard link is unpacked as a
    full copy of its target that the size limit never counted. Links
    left out are reported: an archive made from a folder with
    hard-linked duplicates or linked photos imports fewer."""
    everything = tf.getmembers()
    members = [m for m in everything if m.isfile() or m.isdir()]
    left_out = len(everything) - len(members)
    if left_out:
        log.warning(
            "Archive %s: %d link entries were not unpacked (links are not imported)",
            os.path.basename(input_path),
            left_out,
        )
        emit(
            {
                "type": "progress",
                "current": 0,
                "total": 0,
                "filepath": f"Skipping {left_out} links in the archive (files only)…",
            }
        )
    return members


def _unpack_tar(
    tf: tarfile.TarFile,
    members: list[tarfile.TarInfo],
    dest: str,
    emit: Callable[[dict[str, Any]], None],
) -> bool:
    """Write the checked members of a tar into *dest* (a new, empty
    folder). False (after an error event) when the archive is damaged
    or holds an unsafe entry."""
    try:
        if hasattr(tarfile, "data_filter"):
            # filter="data" (3.11.4+ ships it) rejects absolute
            # paths, links escaping the tree, device files, and
            # drops the archive's mode bits.
            tf.extractall(dest, members=members, filter="data")
        else:
            # Python 3.11: no filter kwarg yet — validate
            # each member via is_safe_tar_member, which
            # checks BOTH path containment AND tar entry
            # type. Rejecting symlinks / hardlinks /
            # device nodes here mirrors what filter="data"
            # does on 3.12+, so a malicious archive can't
            # plant a non-file entry on the older Python.
            for member in members:
                if not is_safe_tar_member(member, dest):
                    raise ValueError(
                        f"Unsafe tar entry rejected: {member.name} (type={member.type!r})"
                    )
                tf.extract(member, dest)
    except _UNPACK_ERRORS as e:
        # Py3.12+ tarfile filters raise FilterError /
        # AbsolutePathError / OutsideDestinationError on
        # malicious entries. Surface as a user-readable
        # message instead of a 500 crashing the worker.
        _report_unpack_failure(e, emit)
        return False
    return True


#: What can go wrong while an archive is read: a damaged or truncated
#: file (zlib.error, EOFError), a password (RuntimeError), a compression
#: this Python can't read (NotImplementedError), an unsafe entry.
_UNPACK_ERRORS = (
    zipfile.BadZipFile,
    tarfile.TarError,
    zlib.error,
    EOFError,
    RuntimeError,
    NotImplementedError,
    ValueError,
    OSError,
)


def _report_unpack_failure(e: BaseException, emit: Callable[[dict[str, Any]], None]) -> None:
    log.warning("Could not extract archive: %r", e)
    if isinstance(e, OSError) and e.errno == errno.ENOSPC:
        reason = "the disk is full: free some space and analyse it again"
    elif isinstance(e, RuntimeError) and "encrypted" in str(e).lower():
        reason = "it is password-protected: unpack it yourself, then analyse the folder"
    elif isinstance(e, NotImplementedError):
        reason = (
            "it uses a compression this app can't read: unpack it yourself, then analyse the folder"
        )
    else:
        reason = "it is damaged or holds an unsafe entry (see server.log)"
    emit({"type": "error", "message": f"Could not extract archive: {reason}"})


def _room_for(dest: str, size: int, emit: Callable[[dict[str, Any]], None]) -> bool:
    """Is there room to unpack *size* bytes next to *dest*? (It is
    unpacked whole before it is moved into place.)"""
    try:
        free = shutil.disk_usage(dest).free
    except OSError:
        return True  # can't tell: try, and report ENOSPC if it comes
    if size <= free:
        return True
    emit(
        {
            "type": "error",
            "message": f"Not enough free disk space to unpack this archive: it needs "
            f"{size / 1e9:.1f} GB and {free / 1e9:.1f} GB is free",
        }
    )
    return False


def extract_archive_into_workdir(
    input_path: str,
    workdir: str,
    emit: Callable[[dict[str, Any]], None],
) -> str | None:
    """Extract *input_path* into its own folder under ``workdir/extracted``
    and return that path.

    Emits ``{"type": "progress", ...}`` once at the start and
    ``{"type": "error", "message": ...}`` on any guard failure;
    returns ``None`` when extraction failed so the caller can bail.

    Guards:

    * Entry-count cap (``_MAX_ARCHIVE_MEMBERS``).
    * Per-entry path length cap (``_MAX_ARCHIVE_MEMBER_PATH_CHARS``).
    * Total uncompressed-size cap (``_MAX_ARCHIVE_BYTES``).
    * Zip entries validated via ``is_safe_archive_member``.
    * Tar entries validated via ``is_safe_tar_member`` (Py<3.12) or
      the stdlib ``filter="data"`` policy (Py 3.12+).
    """
    extract_dir = _real_folder(workdir, _archive_folder(input_path))
    emit(
        {
            "type": "progress",
            "current": 0,
            "total": 0,
            "filepath": f"Extracting {os.path.basename(input_path)}…",
        }
    )
    # Unpacked into a new, empty folder of our own first: nothing the
    # archive names can then be written into a file that is already
    # there (a hard link of an exported copy, a link), whatever the
    # archive's names turn into on this system. Only a complete, checked
    # unpacking is moved into place.
    base = os.path.dirname(extract_dir)
    sweep_unfinished_unpacking(workdir)  # one analysis at a time: none is in use
    fresh = tempfile.mkdtemp(prefix=_UNPACKING, dir=base)
    try:
        if not _unpack(input_path, fresh, emit):
            return None
        clash = _first_clash(fresh, extract_dir)
        if clash is not None:
            log.warning("Archive %s clashes with %s at %s", input_path, extract_dir, clash)
            emit(
                {
                    "type": "error",
                    "message": f"Nothing was changed: the archive has '{clash}' as a file where "
                    "its earlier unpacking has a folder of that name, or the other way round. "
                    f"Remove '{os.path.join(extract_dir, clash)}' and analyse it again",
                }
            )
            return None
        try:
            _move_into(fresh, extract_dir)
        except OSError as e:
            log.warning("Could not move the unpacked archive into %s: %s", extract_dir, e)
            emit(
                {
                    "type": "error",
                    "message": "Could not put the unpacked photos in place: "
                    f"{e.strerror or e} (see server.log)",
                }
            )
            return None
    finally:
        shutil.rmtree(fresh, ignore_errors=True)

    _drop_twins_of_the_old_layout(os.path.join(workdir, EXTRACTED_DIR), extract_dir)
    log.info("Extracted archive to %s", extract_dir)
    return extract_dir


def _unpack(input_path: str, dest: str, emit: Callable[[dict[str, Any]], None]) -> bool:
    """Unpack the whole archive into *dest*, a new empty folder. False
    (after an error event) when it is refused or fails."""
    if zipfile.is_zipfile(input_path):
        with zipfile.ZipFile(input_path, "r") as zf:
            infos = zf.infolist()
            if len(infos) > _MAX_ARCHIVE_MEMBERS:
                emit(
                    {
                        "type": "error",
                        "message": (
                            f"Archive has too many entries ({len(infos)}). "
                            f"Max allowed: {_MAX_ARCHIVE_MEMBERS}."
                        ),
                    }
                )
                return False
            if any(len(i.filename) > _MAX_ARCHIVE_MEMBER_PATH_CHARS for i in infos):
                emit(
                    {
                        "type": "error",
                        "message": "Archive contains an entry path that is too long.",
                    }
                )
                return False
            total_size = sum(i.file_size for i in infos)
            if total_size > _MAX_ARCHIVE_BYTES:
                emit(
                    {
                        "type": "error",
                        "message": (
                            f"Archive too large ({total_size / 1e9:.1f} GB). "
                            f"Max allowed: {_MAX_ARCHIVE_BYTES / 1e9:.0f} GB."
                        ),
                    }
                )
                return False
            if not _room_for(dest, total_size, emit):
                return False
            try:
                for info in infos:
                    if not is_safe_archive_member(info.filename, dest):
                        raise ValueError(f"Zip entry escapes target: {info.filename}")
                    zf.extract(info, dest)
            except _UNPACK_ERRORS as e:
                _report_unpack_failure(e, emit)
                return False
    elif tarfile.is_tarfile(input_path):
        with tarfile.open(input_path, "r:*") as tf:
            # Files and folders only. A photo archive needs no links, and
            # a hard link is unpacked as a full copy of its target that
            # the size limit below never counted.
            members = _files_and_folders(tf, input_path, emit)
            if len(members) > _MAX_ARCHIVE_MEMBERS:
                emit(
                    {
                        "type": "error",
                        "message": (
                            f"Archive has too many entries ({len(members)}). "
                            f"Max allowed: {_MAX_ARCHIVE_MEMBERS}."
                        ),
                    }
                )
                return False
            if any(len(m.name) > _MAX_ARCHIVE_MEMBER_PATH_CHARS for m in members):
                emit(
                    {
                        "type": "error",
                        "message": "Archive contains an entry path that is too long.",
                    }
                )
                return False
            # Size cap: parallels the zip path so a malicious or
            # bloated tar can't fill the disk before extractall
            # has a chance to bail.
            total_size = sum(m.size for m in members if m.isfile())
            if total_size > _MAX_ARCHIVE_BYTES:
                emit(
                    {
                        "type": "error",
                        "message": (
                            f"Archive too large ({total_size / 1e9:.1f} GB). "
                            f"Max allowed: {_MAX_ARCHIVE_BYTES / 1e9:.0f} GB."
                        ),
                    }
                )
                return False
            if not _room_for(dest, total_size, emit):
                return False
            if not _unpack_tar(tf, members, dest, emit):
                return False
    else:
        emit({"type": "error", "message": "Unsupported archive format"})
        return False
    return True


def _drop_twins_of_the_old_layout(base: str, extract_dir: str) -> None:
    """Earlier versions unpacked every archive straight into
    ``extracted/``. Analysing one of those archives again would import
    each photo a second time from its new folder. A file just unpacked
    is removed when the library already has it: a photo row for the old
    place, whose file there is byte for byte the same."""
    import filecmp

    pairs: dict[str, str] = {}
    for parent, _folders, files in os.walk(extract_dir):
        rel = os.path.relpath(parent, extract_dir)
        for name in files:
            old = os.path.normpath(os.path.join(base, rel, name))
            if os.path.isfile(old) and not os.path.islink(old):
                pairs[old] = os.path.join(parent, name)
    dropped = 0
    workdir = os.path.dirname(base)
    # A photo the library already has at the NEW place too stays: its
    # row, albums and choices point at this file.
    has_row_here = set(_in_the_library(workdir, list(pairs.values())))
    for old in _in_the_library(workdir, list(pairs)):
        if pairs[old] in has_row_here:
            continue
        try:
            if filecmp.cmp(old, pairs[old], shallow=False):
                os.unlink(pairs[old])
                dropped += 1
        except OSError:
            continue
    if dropped:
        log.info(
            "%d photos of this archive are already in the library from an earlier "
            "unpacking; they were not unpacked again",
            dropped,
        )


def _in_the_library(workdir: str, paths: list[str]) -> list[str]:
    """Those of *paths* that have a photo row in the library whose
    database is in *workdir* (none when there is no database there)."""
    import sqlite3

    from bpp.db.connection import get_db
    from bpp.db.sql_batches import in_batches

    db_path = os.path.join(workdir, "photopicker.db")
    if not paths or not os.path.isfile(db_path):
        return []
    found: list[str] = []
    try:
        conn = get_db(db_path)
        for marks, batch in in_batches(paths):
            found.extend(
                r[0]
                for r in conn.execute(
                    f"SELECT filepath FROM photos WHERE filepath IN ({marks})", batch
                )
            )
    except sqlite3.Error:
        log.warning("Could not look up earlier unpackings in %s", db_path, exc_info=True)
        return []
    return found

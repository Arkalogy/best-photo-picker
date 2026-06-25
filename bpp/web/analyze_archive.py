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

import os
import sys
import tarfile
import zipfile
from collections.abc import Callable
from typing import Any

from bpp.utils.logging import get_logger
from bpp.utils.paths import is_safe_archive_member, is_safe_tar_member

log = get_logger(__name__)

_MAX_ARCHIVE_BYTES = 50 * 1024 * 1024 * 1024  # 50 GB
_MAX_ARCHIVE_MEMBERS = 100_000
_MAX_ARCHIVE_MEMBER_PATH_CHARS = 4096


def extract_archive_into_workdir(
    input_path: str,
    workdir: str,
    emit: Callable[[dict[str, Any]], None],
) -> str | None:
    """Extract *input_path* into ``workdir/extracted`` and return that path.

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
    extract_dir = os.path.join(workdir, "extracted")
    os.makedirs(extract_dir, exist_ok=True)
    emit(
        {
            "type": "progress",
            "current": 0,
            "total": 0,
            "filepath": f"Extracting {os.path.basename(input_path)}…",
        }
    )

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
                return None
            if any(len(i.filename) > _MAX_ARCHIVE_MEMBER_PATH_CHARS for i in infos):
                emit(
                    {
                        "type": "error",
                        "message": "Archive contains an entry path that is too long.",
                    }
                )
                return None
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
                return None
            for info in infos:
                if not is_safe_archive_member(info.filename, extract_dir):
                    raise ValueError(f"Zip entry escapes target: {info.filename}")
                zf.extract(info, extract_dir)
    elif tarfile.is_tarfile(input_path):
        with tarfile.open(input_path, "r:*") as tf:
            members = tf.getmembers()
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
                return None
            if any(len(m.name) > _MAX_ARCHIVE_MEMBER_PATH_CHARS for m in members):
                emit(
                    {
                        "type": "error",
                        "message": "Archive contains an entry path that is too long.",
                    }
                )
                return None
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
                return None
            try:
                if sys.version_info >= (3, 12):
                    # filter="data" rejects absolute paths, links
                    # escaping the tree, device files, etc.
                    tf.extractall(extract_dir, filter="data")
                else:
                    # Python 3.11: no filter kwarg yet — validate
                    # each member via is_safe_tar_member, which
                    # checks BOTH path containment AND tar entry
                    # type. Rejecting symlinks / hardlinks /
                    # device nodes here mirrors what filter="data"
                    # does on 3.12+, so a malicious archive can't
                    # plant a non-file entry on the older Python.
                    for member in members:
                        if not is_safe_tar_member(member, extract_dir):
                            raise ValueError(
                                f"Unsafe tar entry rejected: {member.name} (type={member.type!r})"
                            )
                        tf.extract(member, extract_dir)
            except (tarfile.TarError, ValueError, OSError) as e:
                # Py3.12+ tarfile filters raise FilterError /
                # AbsolutePathError / OutsideDestinationError on
                # malicious entries. Surface as a user-readable
                # message instead of a 500 crashing the worker.
                emit({"type": "error", "message": f"Could not extract archive: {e}"})
                return None
    else:
        emit({"type": "error", "message": "Unsupported archive format"})
        return None

    log.info("Extracted archive to %s", extract_dir)
    return extract_dir

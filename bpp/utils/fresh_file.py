"""Files created where someone else may have been first.

A name in a user-visible folder (an export, a cache) may already be
taken: by a stale leftover of an earlier run, or by a link someone
planted there. Opening the name for writing would follow the link and
truncate whatever it points at. The writers here unlink the existing
entry (which never touches what it pointed at) and create the file
exclusively, without following links, so the bytes land in a new inode
of this process's own.
"""

from __future__ import annotations

import contextlib
import errno
import os
import stat
from collections.abc import Iterator
from typing import IO, Any

_FLAGS = (
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
)


def open_fresh(path: str, mode: str = "wb", **kw: Any) -> IO[Any]:
    """``path`` opened for writing as a file created here and now.

    ``mode`` is ``"wb"`` (the default) or ``"w"`` with the text keywords
    (``encoding``, ``newline``). The file takes the process umask like
    any other new file. A folder at the name is not removed: that is
    one failed write (``EEXIST``), the same on every platform."""
    if "w" not in mode or any(c in mode for c in "ra+x"):
        raise ValueError(f"open_fresh writes a new file; mode {mode!r} is not a plain write")
    with contextlib.suppress(FileNotFoundError):
        if stat.S_ISDIR(os.lstat(path).st_mode):
            raise OSError(errno.EEXIST, "a folder is in the way of the file", path)
        os.unlink(path)
    fd = os.open(path, _FLAGS, 0o666)
    try:
        return os.fdopen(fd, mode, **kw)
    except Exception:
        os.close(fd)
        raise


@contextlib.contextmanager
def replace_fresh(
    path: str, mode: str = "wb", *, part: str | None = None, **kw: Any
) -> Iterator[IO[Any]]:
    """Write ``path`` through a fresh sibling file that is moved onto
    ``path`` when the block completes. An existing entry at ``path`` (a
    file, a link) is replaced, never written into; if the block raises,
    the part file is removed and ``path`` is left as it was.

    ``part`` names the sibling; the default is ``path + ".part"``. A
    writer that may run concurrently for the same ``path`` passes a
    name of its own (process and thread ids) so two never share one."""
    part = path + ".part" if part is None else part
    try:
        with open_fresh(part, mode, **kw) as fh:
            yield fh
        os.replace(part, path)
    finally:
        with contextlib.suppress(OSError):
            os.remove(part)

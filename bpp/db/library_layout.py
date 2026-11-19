"""Refuse a library folder whose own layout leads out of it.

Opening a library backs up its database, rotates the previous backup,
sweeps stale caches and writes logs, all by fixed names under the
library root. A folder prepared by someone else (a shared drive, an
archive, a synced folder) can make any of those names a link to a file
or folder of the owner's, and the app would then overwrite or delete
through it. So a library is checked before anything in it is touched.

The library root itself may be reached through a link (an external
drive, a shortcut): only links *inside* it are refused. Entries of
``photos/`` and of the cache folders are not listed here (there can be
many thousands); the code that deletes, writes or serves one checks it
where it acts.
"""

from __future__ import annotations

import os

from bpp.errors import ValidationError

#: App-managed folders, relative to the library root. Each must be a
#: real folder, and (for the first two) hold no linked entries: the app
#: reads, rewrites and deletes files there by name.
_SCANNED = ("data", "logs")
_MANAGED = (
    *_SCANNED,
    "photos",
    "cache",
    os.path.join("cache", "web_thumbs"),
    os.path.join("cache", "face_crops"),
    os.path.join("cache", "pet_crops"),
)


def find_layout_link(library_path: str) -> str | None:
    """The first app-managed entry of the library that is a link, as a
    path relative to the library root, or None when there is none."""
    root = os.path.abspath(library_path)
    for rel in _MANAGED:
        if os.path.islink(os.path.join(root, rel)):
            return rel
    for rel in _SCANNED:
        try:
            with os.scandir(os.path.join(root, rel)) as entries:
                for entry in entries:
                    if entry.is_symlink():
                        return os.path.join(rel, entry.name)
        except FileNotFoundError:
            continue  # a new library: the folder is created next
        except NotADirectoryError:
            return rel
    return None


def check_library_layout(library_path: str) -> None:
    """Raise ValidationError when the library can't be opened safely."""
    try:
        rel = find_layout_link(library_path)
    except OSError as e:
        raise ValidationError(
            f"This library can't be opened: one of its folders can't be read ({e.strerror or e}). "
            "Check the folder's permissions and that its drive is connected.",
        ) from e
    if rel is not None:
        raise ValidationError(
            f"This library can't be opened: '{rel}' inside it is a link to another location. "
            "Remove the link (or replace it with a real folder or file) and try again.",
            entry=rel,
        )

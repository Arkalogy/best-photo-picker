"""What a paired device may see of the owner's filesystem: nothing.

Every JSON response to a LAN principal passes through
:func:`redact_paths` (installed as an ``after_request`` hook in
``bpp.web.app``). Strings that start with the library root become
library-relative; strings that start with the owner's home folder
become ``~/...``. Dict keys get the same treatment, so maps keyed by
file path (overrides, favorites) stay consistent with the photo rows
the device renders. The device's own read-only lookups by path go
through :func:`resolve_device_path`, the inverse.

One choke point, rather than a rule per endpoint: the first version
rewrote ``filepath`` inside the photo-dict builders only, and the SPA
then joined absolute override keys against relative photo paths and
lost every pick and favorite on the phone.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from typing import Any

from bpp.constants import HASH_PREFIX_LEN

#: Prefix of the opaque form of a path outside the library and home.
EXT_PREFIX = "@ext"

#: Strings that start like one of the app's own URLs are routes, not
#: paths (an error hint such as ``/api/v1/photos``); leave them alone.
_URL_PREFIXES = ("/api/", "/static/", "/share", "/pair")


def _path_hash(filepath: str) -> str:
    """The same hash the thumbnail map keys a path by."""
    return hashlib.sha256(filepath.encode()).hexdigest()[:HASH_PREFIX_LEN]


def path_rewriter(library_path: str | None, home: str | None = None) -> Callable[[str], str]:
    """A function mapping one absolute path to its device-safe form.
    Resolves the roots once; call it per response, not per path."""
    root = os.path.normpath(library_path) if library_path else None
    home_dir = os.path.normpath(home or os.path.expanduser("~"))

    def rewrite(value: str) -> str:
        # The prefix match already proves the layout: slice, don't relpath.
        if root and (value == root or value.startswith(root + os.sep)):
            return "." if value == root else value[len(root) + 1 :]
        if value == home_dir or value.startswith(home_dir + os.sep):
            return "~" + value[len(home_dir) :]
        if os.path.isabs(value) and len(value) > 1 and not value.startswith(_URL_PREFIXES):
            # Photos analysed in place elsewhere (an external drive, a
            # temp folder): fail closed — the device gets a handle it can
            # look the photo up by, not the volume and folder names.
            return f"{EXT_PREFIX}/{_path_hash(value)}/{os.path.basename(value)}"
        return value

    return rewrite


def redact_paths(data: Any, rewrite: Callable[[str], str]) -> Any:
    """Apply ``rewrite`` to every string in a JSON-like structure, dict
    keys included. Returns a new structure; scalars pass through."""
    if isinstance(data, str):
        return rewrite(data)
    if isinstance(data, list):
        return [redact_paths(item, rewrite) for item in data]
    if isinstance(data, dict):
        return {
            (rewrite(key) if isinstance(key, str) else key): redact_paths(value, rewrite)
            for key, value in data.items()
        }
    return data


def resolve_device_path(
    path: str, library_path: str | None, lookup: Callable[[str], str | None] | None = None
) -> str:
    """The absolute path behind a device-safe one (the inverse of
    :func:`path_rewriter`); absolute input passes through. ``lookup``
    maps a path hash to its path (the thumbnail map) for the opaque
    ``@ext/<hash>/<name>`` form; without it that form stays as is."""
    if not path:
        return path
    if path == "~" or path.startswith("~" + os.sep):
        return os.path.expanduser(path)
    if path.startswith(EXT_PREFIX + "/"):
        parts = path.split("/", 2)
        resolved = lookup(parts[1]) if lookup and len(parts) >= 2 else None
        return resolved or path
    if not os.path.isabs(path) and library_path:
        return os.path.normpath(os.path.join(library_path, path))
    return path

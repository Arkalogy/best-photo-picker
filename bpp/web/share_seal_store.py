"""What this installation keeps, outside every library, to seal sharing state.

Two private files beside ``libraries.json``:

* ``share-seal.key``: the key. Written once and never rewritten, so an
  ordinary save can't damage it.
* ``share-seal.json``: for each sealed library, its counter and where it
  was last opened; and the databases that were in use before the key
  existed (adopted once, see :mod:`bpp.web.share_seal`).

A file that can't be read or saved never stops the app: the process
carries on with what it has in memory, and says so. The cost is that
sharing has to be turned on again after a restart.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import threading
from collections.abc import Callable
from typing import Any

from bpp.utils.logging import get_logger

log = get_logger(__name__)

_lock = threading.RLock()
#: Per file path, what this process holds when the file can't be used.
_memory_keys: dict[str, str] = {}
_memory_states: dict[str, dict] = {}
#: Per key file path, why it could not be read the last time it was tried.
_unreadable: dict[str, str] = {}


def _folder() -> str:
    from bpp.db.registry import get_registry_path

    return os.path.dirname(get_registry_path())


def key_file() -> str:
    return os.path.join(_folder(), "share-seal.key")


def state_file() -> str:
    return os.path.join(_folder(), "share-seal.json")


def file_identity(path: str) -> list[int] | None:
    """``[device, inode]`` of a file, or None when it isn't there."""
    try:
        st = os.stat(path)
    except (OSError, ValueError):
        return None
    return [st.st_dev, st.st_ino]


def _write_private(path: str, text: str, *, once: bool) -> None:
    """Write an owner-only file and flush it to disk. ``once`` creates it
    and fails if it exists; otherwise it replaces the file in one step."""
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    target = path if once else f"{path}.{secrets.token_hex(4)}.tmp"
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if not once:
            os.replace(target, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(target)
        raise


def _valid_key(text: str) -> bool:
    try:
        return len(text) == 64 and len(bytes.fromhex(text)) == 32
    except ValueError:
        return False


def _databases_in_use() -> list[list[int]]:
    """The identity of every registered library's database file as it is
    now: what may be adopted, once, because it predates the key."""
    from bpp.db.registry import list_libraries

    found = []
    for lib in list_libraries():
        ident = file_identity(os.path.join(lib["path"], "data", "photopicker.db"))
        if ident is not None:
            found.append(ident)
    return found


def _create_key(path: str) -> str:
    key = secrets.token_hex(32)
    # Records made under an earlier key mean nothing now: start over.
    state = {"adopt": _databases_in_use(), "libs": {}}
    try:
        _write_private(state_file(), json.dumps(state), once=False)
        _write_private(path, key, once=True)
    except OSError as exc:
        log.error(
            "Can't save this installation's sharing key in %s (%s): sharing settings "
            "will not survive a restart",
            os.path.dirname(path),
            exc,
        )
        _memory_keys[path] = key
        _memory_states[state_file()] = state
        return key
    log.info("Created this installation's sharing key (%d libraries in use)", len(state["adopt"]))
    _warn_of_settings_left_elsewhere()
    return key


def _warn_of_settings_left_elsewhere() -> None:
    """A first start with an empty settings folder while an earlier
    version's settings sit in the home folder: the usual cause is a
    container started without its settings folder mounted, and then
    every new container starts from nothing (sharing turned off, model
    choices asked again)."""
    old = os.path.join(os.path.expanduser("~"), ".config", "bpp")
    now = _folder()
    with contextlib.suppress(OSError):
        if os.path.isdir(old) and not os.path.samefile(old, now):
            log.warning(
                "Settings of an earlier version are in %s and are no longer used: this "
                "version keeps its settings in %s. Sharing has to be turned on again. "
                "In a container, mount a folder at /state so the settings survive a "
                "new container (see the README).",
                old,
                now,
            )


def key() -> str:
    """This installation's key, created on first use. Call it before a
    newly opened library is registered: the first creation lists the
    databases already in use."""
    with _lock:
        path = key_file()
        if path in _memory_keys:
            return _memory_keys[path]
        try:
            with open(path) as f:
                text = f.read().strip()
        except FileNotFoundError:
            return _create_key(path)
        except OSError as exc:
            # Never replace a key we merely couldn't read, and never judge
            # a library's sharing state by a stand-in: see unreadable().
            if _unreadable.get(path) != str(exc):
                log.error(
                    "Can't read this installation's sharing key %s (%s). Sharing stays off "
                    "and the sharing settings of the libraries opened are left as they are "
                    "until the file can be read: check its permissions, then restart.",
                    path,
                    exc,
                )
            _unreadable[path] = str(exc)
            return secrets.token_hex(32)  # not kept: the file is read again next time
        _unreadable.pop(path, None)
        if _valid_key(text):
            return text
        from bpp.utils.json_utils import move_aside

        with contextlib.suppress(OSError):
            move_aside(path, context="sharing key")
        return _create_key(path)


def unreadable() -> str | None:
    """Why this installation's key can't be read right now, or None when
    it can (or does not exist yet). While it can't, nothing can be said
    about whether a library's sharing state is ours."""
    with _lock:
        key()
        return _unreadable.get(key_file())


def _load() -> dict:
    path = state_file()
    if path in _memory_states:
        return _memory_states[path]
    from bpp.utils.json_utils import load_json_file

    data = load_json_file(path, {}, expected_type=dict, context="sharing seal records")
    adopt, libs = data.get("adopt"), data.get("libs")
    return {
        "adopt": [
            a
            for a in (adopt if isinstance(adopt, list) else [])
            if isinstance(a, list) and len(a) == 2 and all(isinstance(x, int) for x in a)
        ],
        "libs": {
            lib_id: {"n": rec["n"], "path": rec["path"]}
            for lib_id, rec in (libs.items() if isinstance(libs, dict) else ())
            if isinstance(rec, dict)
            and isinstance(rec.get("n"), int)
            and isinstance(rec.get("path"), str)
        },
    }


def update(change: Callable[[dict], Any]) -> None:
    """Read the records, apply *change* to them and save, as one step in
    this process. A save that fails keeps the result in memory."""
    with _lock:
        key()  # the records belong to a key: make sure there is one
        path = state_file()
        state = _load()
        before = json.dumps(state, sort_keys=True)
        change(state)
        if json.dumps(state, sort_keys=True) == before:
            return
        try:
            _write_private(path, json.dumps(state), once=False)
            _memory_states.pop(path, None)
        except OSError as exc:
            if path not in _memory_states:
                log.error("Can't save the sharing seal records to %s (%s)", path, exc)
            _memory_states[path] = state


def record(lib_id: str) -> dict | None:
    """The counter and place recorded for a sealed library, if any."""
    with _lock:
        key()
        return _load()["libs"].get(lib_id)


def adoptable(ident: list[int] | None) -> bool:
    """Was this database file in use before the key existed, and not
    opened since?"""
    with _lock:
        key()
        return ident is not None and ident in _load()["adopt"]


def forget_adoption(ident: list[int] | None) -> None:
    if ident is not None:
        update(lambda state: state.update(adopt=[a for a in state["adopt"] if a != ident]))

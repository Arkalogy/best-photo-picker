"""Packages the in-app installer touched stay unloaded until a restart.

Once pip runs for a feature, its packages (and the libraries that install
writes which the app imports itself) aren't imported by this process
again: pip may be rewriting them, or has replaced them under code already
in memory. The app asks for a restart instead; the next start loads them
cleanly. Imports of these packages go through :func:`import_gated` (or
:func:`requires` / :func:`importable`).

A package already loaded keeps working; only parts of it not loaded yet
are held (they'd be the new files).

Known limit: only the app's own imports of these packages are held (the
model checks, the dlib loader, scipy). Code a held-off feature would run is
never reached; other packages pip upgrades on the way (numpy, protobuf…)
aren't held — the app imported them at start, and work that would start
processes loading them afresh waits for the restart (bp_install's worker
start guard). A system-wide import blocker was considered and left out on
purpose (its edge cases).
"""

from __future__ import annotations

import atexit
import contextlib
import importlib
import os
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

#: Install key -> the packages its feature is loaded through.
FEATURE_MODULES: dict[str, frozenset[str]] = {
    "faces": frozenset({"face_recognition", "mediapipe", "ai_edge_litert"}),
    "onnxruntime": frozenset({"onnxruntime"}),
    "inpaint": frozenset({"simple_lama_inpainting"}),
}

#: Other packages an install writes that the app imports itself.
_DEPENDENCIES: dict[str, frozenset[str]] = {
    "faces": frozenset({"scipy", "dlib", "face_recognition_models"}),
    "inpaint": frozenset({"torch", "torchvision"}),
}

RESTART_REASON = "restart Best Photo Picker to finish installing"

#: Carries the hold to the analysis processes the app starts (they import
#: from disk afresh, and must wait for the restart too): the file listing
#: what the app holds. Set once, as the app starts (:func:`become_app`) —
#: changing the environment while threads run isn't safe — and inherited by
#: every process the app starts; the file is what changes.
_ENV = "BPP_INSTALL_HELD_FILE"


def _own_file_path() -> Path:
    from bpp.utils.paths import cache_dir

    return cache_dir() / "install-held" / f"held-{os.getpid()}.txt"


def _parent_file() -> Path | None:
    """The hold file of the app that started this process (an analysis
    process), or None (nothing named, or this process's own name: stale)."""
    named = os.environ.get(_ENV)
    if not named or Path(named).name == _own_file_path().name:
        return None
    return Path(named)


def _read(path: Path) -> frozenset[str]:
    try:
        return frozenset(filter(None, path.read_text(encoding="utf-8").split(",")))
    except OSError:
        return frozenset()  # no file: nothing held


_lock = threading.Lock()
#: Install keys whose packages are held: pip runs for them, or ran.
_installed: frozenset[str] = frozenset()
#: Of those, the ones pip wrote packages for: they wait for the restart.
_written: frozenset[str] = frozenset()
_parent = _parent_file()
#: Held by the app that started this process (an analysis process).
_inherited: frozenset[str] = _read(_parent) if _parent is not None else frozenset()
#: Top-level packages held until the restart.
_held: frozenset[str] = _inherited
#: The app: where it lists what it holds for the processes it starts
#: (None until :func:`become_app`; a process the app started only reads).
_own_file: Path | None = None


def _sweep_stale_files(folder: Path) -> None:
    """Remove hold files of apps no longer running (killed or crashed: no
    exit to remove them) — ours too, left by one that had this process id."""
    for path in folder.glob("held-*.txt"):
        pid = path.stem.removeprefix("held-")
        if pid.isdigit() and (int(pid) == os.getpid() or not _process_alive(int(pid))):
            with contextlib.suppress(OSError):
                path.unlink()


def _process_alive(pid: int) -> bool:
    if os.name != "posix":
        return True  # (on Windows os.kill would end it): left in place
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True  # exists, not ours to signal
    return True


def become_app() -> None:
    """This process is the app (the server, as it starts — before its worker
    threads): it lists what it holds for the processes it starts. (Even if
    the environment named another app's file: one that started this server
    from its own process, say — this server isn't its analysis process.)"""
    global _own_file, _parent, _inherited, _held
    with _lock:
        _own_file = _own_file_path()
        _parent, _inherited = None, frozenset()
        _held = frozenset().union(*(install_modules(key) for key in _installed))
        _sweep_stale_files(_own_file.parent)
        os.environ[_ENV] = str(_own_file)
        if _held:
            _write_held_file(_own_file, _held)


def install_modules(key: str) -> frozenset[str]:
    """Every package the app imports that install *key* writes."""
    return FEATURE_MODULES.get(key, frozenset()) | _DEPENDENCIES.get(key, frozenset())


def hold_during_install(key: str) -> None:
    """pip is about to run for *key*: what it may write isn't loaded from
    here on (it may be half written). Not yet "waiting for a restart": pip
    may still write nothing (see :func:`hold_until_restart`, :func:`release`)."""
    global _installed
    with _lock:
        _installed = _installed | {key}
        _set_held_locked()


def hold_until_restart(key: str) -> None:
    """pip wrote packages for *key*: held until a restart, which the app
    asks for. (Even a failed run: pip keeps what it installed before the
    failing package.)"""
    global _installed, _written
    with _lock:
        _installed = _installed | {key}
        _written = _written | {key}
        _set_held_locked()


def release(key: str) -> None:
    """pip for *key* wrote nothing (couldn't be launched, failed before
    installing anything, or found it all installed): nothing is held."""
    global _installed, _written
    with _lock:
        _installed = _installed - {key}
        _written = _written - {key}
        _set_held_locked()


def _set_held_locked() -> None:
    global _held
    _held = _inherited.union(*(install_modules(key) for key in _installed))
    if _own_file is not None:
        _write_held_file(_own_file, _held)


def _write_held_file(path: Path, held: frozenset[str]) -> None:
    """List *held* for the processes this app starts (none: no file)."""
    try:
        if not held:
            with contextlib.suppress(FileNotFoundError):
                path.unlink()
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(",".join(sorted(held)), encoding="utf-8")
        tmp.replace(path)  # a starting process never reads half a list
    except OSError:
        # Analysis processes then don't know the hold and would load what
        # pip wrote. Rare (a full or read-only disk): worth saying.
        from bpp.utils.logging import get_logger

        get_logger(__name__).warning("Couldn't record the install hold in %s", path, exc_info=True)


@atexit.register
def _forget_held_file() -> None:
    if _own_file is not None:
        with contextlib.suppress(OSError):
            _own_file.unlink()


def held_keys() -> frozenset[str]:
    """Install keys pip wrote packages for: they work after a restart."""
    return _written


def held_for_restart(module: str) -> bool:
    """True if *module*'s package is held and *module* itself isn't loaded."""
    return module not in sys.modules and module.partition(".")[0] in _held


class HeldForRestart(ImportError):
    """An install touched this package: it loads only after a restart (the
    app answers "restart to finish installing")."""


#: Why a package isn't loaded while pip still runs for it.
INSTALLING_REASON = "it's being installed — wait for the install to finish"


def held_reason(module: str) -> str:
    """Why *module* (held) isn't loaded: installed, so restart — or still
    being installed (pip may yet write nothing)."""
    top = module.partition(".")[0]
    written = any(top in install_modules(key) for key in _written) or top in _inherited
    return RESTART_REASON if written else INSTALLING_REASON


def still_installing(exc: HeldForRestart) -> bool:
    """*exc* means "wait for the running install" (pip may yet write
    nothing), not "installed: restart"."""
    return held_reason(exc.name or "") == INSTALLING_REASON


def refuse_if_held(module: str) -> None:
    """Raise :class:`HeldForRestart` if an install touched *module*'s package
    (without importing it)."""
    if held_for_restart(module):
        raise HeldForRestart(f"{module}: {held_reason(module)}", name=module)


def import_gated(module: str) -> Any:
    """Import *module*, unless an install touched its package (then
    :class:`HeldForRestart`)."""
    refuse_if_held(module)
    return importlib.import_module(module)


def requires(module: str) -> Callable[[], Any]:
    """An ``import_check`` for a ModelSingleton: import *module* through
    :func:`import_gated`."""
    return lambda: import_gated(module)


def importable(module: str) -> bool:
    """True if *module* is loaded or can be imported now."""
    if module in sys.modules:
        return True
    try:
        import_gated(module)
    except Exception:  # missing, held, or installed but broken (its native library)
        return False
    return True

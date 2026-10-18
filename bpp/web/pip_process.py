"""Stopping the in-app installer's pip, and everything it started.

pip runs in its own process group (POSIX), so a stop reaches the
compilers it runs (a dlib build) too — and so a Ctrl-C on the server
doesn't: :func:`stop_running_pip` (at exit and on shutdown) does it
instead, or pip would keep writing packages the next start imports.
"""

from __future__ import annotations

import atexit
import contextlib
import hashlib
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

from bpp.utils.json_utils import safe_json_loads
from bpp.utils.logging import get_logger

log = get_logger(__name__)

#: Seconds pip gets to exit after a stop before it's killed.
GRACE_S = 5

#: The pip process running right now, if any.
_live: subprocess.Popen[str] | None = None


def set_live(proc: subprocess.Popen[str] | None, key: str | None = None) -> None:
    """*proc* is the running pip, installing *key*."""
    global _live
    if proc is not None:
        proc.bpp_key = key  # type: ignore[attr-defined]  # see stop_running_pip
    _live = proc


def note_prior(proc: subprocess.Popen[str], prior: bool) -> None:
    """An earlier run of *proc*'s install was cut off writing: its repair
    note stays however this run ends early."""
    proc.bpp_prior = prior  # type: ignore[attr-defined]


def mark_writing(proc: subprocess.Popen[str]) -> None:
    """*proc* started writing packages (pip's "Installing collected
    packages"): stopping it from here on may leave them half replaced."""
    proc.bpp_writing = True  # type: ignore[attr-defined]


def clear_live(proc: subprocess.Popen[str]) -> None:
    """*proc* is over: forget it, unless a newer install's pip replaced it."""
    global _live
    if _live is proc:
        _live = None


def signal_group(proc: subprocess.Popen[str], *, kill: bool) -> None:
    """Signal pip and everything it started (its process group on POSIX)."""
    pid = getattr(proc, "pid", None)
    if os.name == "posix" and isinstance(pid, int):
        try:
            os.killpg(pid, signal.SIGKILL if kill else signal.SIGTERM)
            return
        except (ProcessLookupError, PermissionError):
            pass  # already gone, or not its own group: fall back below
    elif os.name == "nt" and isinstance(pid, int):
        # No process groups: end pip's whole process tree (its compilers
        # would otherwise keep its output open, and the stream waiting).
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, timeout=10
            )
    if kill:
        proc.kill()
    else:
        proc.terminate()


def stop(proc: subprocess.Popen[str], what: str = "pip install") -> None:
    """Stop *proc* and its group: terminate, then kill after the grace.
    Nothing is sent once pip has exited: its process id (and group id) may
    already belong to an unrelated program."""
    if proc.poll() is not None:
        return
    proc.bpp_stopped = True  # type: ignore[attr-defined]  # see was_stopped
    signal_group(proc, kill=False)
    try:
        proc.wait(timeout=GRACE_S)
    except subprocess.TimeoutExpired:
        signal_group(proc, kill=True)
        try:
            proc.wait(timeout=GRACE_S)
        except subprocess.TimeoutExpired:
            log.error("%s refused SIGKILL — leaked process", what)


def was_stopped(proc: subprocess.Popen[str]) -> bool:
    """True if :func:`stop` cut *proc* off while it ran (whatever exit code
    that gave: a SIGTERM on POSIX, 1 on Windows)."""
    return bool(getattr(proc, "bpp_stopped", False))


#: Where the per-install notes go: next to the environment pip changes.
#: A note is written while pip runs and kept when pip was stopped before
#: it finished (the app quit, the deadline, a closed page): packages may
#: be half replaced, so the page shows how to repair it.
def _notes_dir() -> Path:
    """Per user and per Python environment (the environment itself may be
    read-only, or shared by every user of a system Python)."""
    from bpp.utils.paths import cache_dir

    env = hashlib.sha256(sys.prefix.encode()).hexdigest()[:16]
    return cache_dir() / "install-notes" / env


#: Found on first use (not at import: the cache folder may be set later).
MARKER_DIR: Path | None = None


def _dir() -> Path:
    global MARKER_DIR
    if MARKER_DIR is None:
        MARKER_DIR = _notes_dir()
    return MARKER_DIR


def _marker(key: str) -> Path:
    return _dir() / f"bpp-install-in-progress-{key}.json"


def command_line(args: list[str]) -> str:
    """*args* as one command for this system's shell to run as is (the pip
    specs hold >=, < and ;)."""
    if os.name == "nt":
        return subprocess.list2cmdline(args)  # cmd.exe: double quotes
    return shlex.join(args)


def mark_started(key: str, command: str, pid: int | None = None, argv: str = "") -> bool:
    """Note that pip (process *pid*, started with arguments *argv* after the
    interpreter's path) runs for *key*. Returns True if an earlier run of it
    had been stopped part way (its note was still there)."""
    marker = _marker(key)
    prior = marker.exists()
    # The repair shown to the user removes the note when it succeeds.
    if os.name == "nt":  # cmd.exe: its own quoting, no rm
        repair = f'{command} && del /f "{marker}"'
    else:
        repair = f"{command} && rm -f {shlex.quote(str(marker))}"
    global _notes_cache
    _notes_cache = None  # rewritten in place: the folder may look unchanged
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        note = {"command": repair, "pid": pid, "argv": argv}
        marker.write_text(json.dumps(note), encoding="utf-8")
    except OSError:
        log.warning("Couldn't note the install in %s", marker, exc_info=True)
    return prior


def mark_ended(key: str, *, stopped: bool, succeeded: bool, prior: bool) -> None:
    """pip is done for *key*. Keep the note if we stopped it part way, or if
    it failed after an earlier stopped run (its own rollback can't undo
    that run's damage); otherwise the environment is whole again."""
    if not succeeded and (stopped or prior):
        return  # (a pip that finished fine just as it was stopped is whole)
    global _notes_cache
    _notes_cache = None
    with contextlib.suppress(FileNotFoundError):
        _marker(key).unlink()


_PREFIX, _SUFFIX = "bpp-install-in-progress-", ".json"


#: ((notes folder, its change time), when read, its notes): every page's
#: status calls and 30-second polls read them; the folder changes when a note
#: comes or goes (and is reread every few seconds anyway: some filesystems
#: keep that time only to the second, or two).
_notes_cache: tuple[tuple[Path, int], float, dict[str, dict]] | None = None
_NOTES_CACHE_S = 5.0


def _notes() -> dict[str, dict]:
    """key -> its note ({} when unreadable)."""
    global _notes_cache
    try:
        folder = _dir()
        changed = (folder, folder.stat().st_mtime_ns)
    except OSError:
        return {}  # no notes folder: no notes
    cached = _notes_cache
    if (
        cached is not None
        and cached[0] == changed
        and time.monotonic() - cached[1] < _NOTES_CACHE_S
    ):
        return cached[2]
    notes: dict[str, dict] = {}
    for marker in sorted(folder.glob(f"{_PREFIX}*{_SUFFIX}")):
        key = marker.name[len(_PREFIX) : -len(_SUFFIX)]
        try:
            text = marker.read_text(encoding="utf-8")
        except OSError:
            text = ""  # unreadable note: still worth saying
        notes[key] = safe_json_loads(text, {}, expected_type=dict, context="install note")
    _notes_cache = (changed, time.monotonic(), notes)
    return notes


#: (pid, argv) -> (verdict, when): the 30-second polls from every page
#: shouldn't each list processes.
_running_cache: dict[tuple[object, object], tuple[bool, float]] = {}
_CACHE_S = 30.0


def _pip_still_running(pid: object, argv: object = "") -> bool:
    """:func:`_pip_still_running_now`, its answer kept for a while (asked on
    every 30 s poll) — but never past the process's exit (cheap to tell)."""
    key = (pid, argv)
    if not _process_exists(pid):
        _running_cache.pop(key, None)
        return False
    cached = _running_cache.get(key)
    if cached is not None and time.monotonic() - cached[1] < _CACHE_S:
        return cached[0]
    verdict = _pip_still_running_now(pid, argv)
    _running_cache[key] = (verdict, time.monotonic())
    return verdict


def _process_exists(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        # (os.kill would end it there.) Listed by tasklist, or not.
        try:
            listed = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return True  # can't tell: assume it runs (installs wait)
        return f'"{pid}"' in listed
    if os.name != "posix":
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True  # exists, but not ours to signal
    return True


def _pip_still_running_now(pid: object, argv: object = "") -> bool:
    """True if *pid* is still the pip an earlier server started (*argv* its
    arguments after the interpreter's path), outliving it (killed, crashed:
    pip is in its own session) — not another program that got the same
    process id."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if not isinstance(argv, str) or not argv:
        return False  # an older note: can't tell it's still ours
    if os.name == "nt":
        # Its command line isn't readable without extra tools: a process
        # with that id counts as the pip (installs wait; the repair banner
        # waits) — two pips writing one environment would be worse.
        return _process_exists(pid)
    if os.name != "posix":
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:  # alive: make sure it's still that pip, not a reused process id
        command = subprocess.run(
            ["ps", "-ww", "-o", "command=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    # Matched without the interpreter's path (which may hold spaces): a
    # macOS framework Python re-runs itself as …/Python.app/Contents/MacOS/
    # Python, so ps shows another path than the one pip was started with.
    return f" {argv}" in command


def notes_state() -> tuple[dict[str, str], bool]:
    """One pass over the notes: (installs pip didn't get to finish — key ->
    how to repair it, "" when its note can't be read —, whether a pip an
    earlier server started still runs). Neither while this server's own
    pip runs (it refuses a second install by itself)."""
    if _live is not None:
        return {}, False
    found: dict[str, str] = {}
    left_running = False
    for key, note in _notes().items():
        if _pip_still_running(note.get("pid"), note.get("argv")):
            left_running = True  # still writing: not (yet) a repair
            continue
        command = note.get("command")
        found[key] = command if isinstance(command, str) else ""
    return found, left_running


def interrupted_installs() -> dict[str, str]:
    """Installs pip didn't get to finish (see notes_state)."""
    return notes_state()[0]


def pip_left_running() -> bool:
    """True if a pip an earlier server started is still running."""
    return notes_state()[1]


def stop_running_pip() -> None:
    """Server shutting down: stop a running pip (see the module doc)."""
    proc = _live
    if proc is None or proc.poll() is not None:
        return
    log.warning("Stopping the package install: the app is shutting down")
    stop(proc)
    key = getattr(proc, "bpp_key", None)
    if key and not getattr(proc, "bpp_writing", False) and not getattr(proc, "bpp_prior", False):
        # Still downloading or building: this run wrote nothing, nothing to
        # repair (the stream that would settle its note never finishes) —
        # unless an earlier run of it was cut off writing: that repair stays.
        mark_ended(key, stopped=False, succeeded=True, prior=False)


atexit.register(stop_running_pip)

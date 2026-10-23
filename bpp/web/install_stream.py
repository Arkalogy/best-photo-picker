"""The in-app installer's progress stream: GET /install/<key>/progress runs
pip for the install POST /install/<key> claimed and streams its output
(split from bp_install for the 500-LOC cap; the claim lives there).

``_PipRun`` is one pip process and what it leaves behind (the note, the
hold, the claim); ``install_progress`` streams it as server-sent events.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import threading
from collections.abc import Iterator

from flask import Response

from bpp.errors import ValidationError
from bpp.scoring.install_hold import hold_during_install, hold_until_restart, release
from bpp.utils.logging import get_logger
from bpp.web import pip_process
from bpp.web.sse import sse_event

log = get_logger(__name__)

# Outside-cap on the whole pip invocation. Slow PyPI mirrors,
# network stalls, or a hung pip resolver can otherwise block the
# SSE stream — and, since analysis waits for installs, the app's
# background work — indefinitely. Building dlib from source can be
# silent for 10-20 minutes, so the cap leaves room for that.
_PIP_HARD_TIMEOUT_S = 1800  # 30 minutes
# Grace period for pip to exit after we close its stdout (or after
# the client disconnects mid-stream and we terminate the process).
_PIP_TERM_GRACE_S = pip_process.GRACE_S


class _PipRun:
    """One ``pip install`` for a claimed install: starts it, reads its
    output, and settles everything it leaves behind exactly once."""

    def __init__(self, key: str, specs: list[str], token: object) -> None:
        self.key = key
        self.specs = specs
        self.token = token
        self.args = [sys.executable, "-m", "pip", "install", *specs]
        self.proc: subprocess.Popen[str] | None = None
        self.prior = False  # an earlier run of this install was stopped part way
        self.finished = False
        # Did pip write anything (it then waits for a restart)? pip downloads
        # and builds first; it writes only after "Installing collected
        # packages" — and keeps what it installed before a failing package.
        # (Not written: an install where everything was already there.)
        self.wrote = False
        self.restart = False
        self.timed_out = threading.Event()
        self.watchdog: threading.Timer | None = None
        # Keep the last few non-empty log lines so we can surface them
        # in the error message. "pip exited with code 1" alone tells
        # the user nothing.
        self.recent_lines: list[str] = []

    def start(self) -> None:
        """Set the hold, start pip, note it, arm the deadline."""
        # From here on, what pip writes isn't imported by this app (or
        # its analysis processes). Set before pip starts writing; kept
        # until a restart if it writes, lifted if it doesn't.
        hold_during_install(self.key)
        try:
            self.proc = subprocess.Popen(
                self.args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                # Its own process group, so stopping pip also stops the
                # compilers it runs (a dlib build) — see pip_process.
                start_new_session=os.name == "posix",
            )
        except Exception:
            release(self.key)  # pip never ran: nothing changed
            raise
        proc = self.proc
        # Noted until pip ends by itself: if it's stopped part way, the
        # page says so and how to repair it.
        self.prior = pip_process.mark_started(
            self.key,
            pip_process.command_line(self.args),
            getattr(proc, "pid", None),
            " ".join(self.args[1:]),  # its arguments: the interpreter path may differ in ps
        )
        pip_process.note_prior(proc, self.prior)
        # Stopped if the server quits (after the note: what it does then
        # depends on it).
        pip_process.set_live(proc, self.key)
        assert proc.stdout is not None  # guaranteed by stdout=PIPE
        self.watchdog = threading.Timer(_PIP_HARD_TIMEOUT_S, self._time_out)
        self.watchdog.daemon = True
        self.watchdog.start()

    def _time_out(self) -> None:
        # pip may print nothing for a long time, so the cap can't
        # wait for its next line: end pip from here (its output then
        # closes and the read loop finishes; teardown does the rest).
        if self.proc is not None and self.proc.poll() is None:
            self.timed_out.set()  # only when it really stopped a running pip
            pip_process.stop(self.proc)

    def lines(self) -> Iterator[str]:
        """pip's non-empty output lines as they come."""
        assert self.proc is not None and self.proc.stdout is not None
        for line in self.proc.stdout:
            line = line.rstrip()
            if line.startswith("Installing collected packages"):
                self.wrote = True
                pip_process.mark_writing(self.proc)
            if line:
                self.recent_lines.append(line)
                if len(self.recent_lines) > 6:
                    self.recent_lines.pop(0)
                yield line

    def wait(self) -> None:
        """stdout closed → pip should be exiting. Bound the wait so a
        post-close hang can't lock the worker thread."""
        assert self.proc is not None
        self.proc.wait(timeout=_PIP_TERM_GRACE_S)
        if self.timed_out.is_set() and self.proc.returncode != 0:
            # (pip may still have exited by itself as the deadline hit)
            raise TimeoutError(f"pip install exceeded {_PIP_HARD_TIMEOUT_S}s; stopped it")

    def failure_message(self) -> str:
        """Find the most informative line from the tail. Prefer ones
        that start with ERROR: which pip uses for its actual
        diagnostic; fall back to the very last line."""
        assert self.proc is not None
        hint = next(
            (line_ for line_ in reversed(self.recent_lines) if line_.startswith("ERROR:")),
            self.recent_lines[-1] if self.recent_lines else "",
        )
        msg = f"pip exited with code {self.proc.returncode}"
        if hint:
            msg += f" — {hint}"
        return msg

    def timeout_message(self) -> str:
        minutes = round(_PIP_HARD_TIMEOUT_S / 60)
        # The exact command for this app's Python, quoted for a shell
        # (the specs hold >=, < and ;).
        command = pip_process.command_line(self.args)
        return (
            f"The install didn't finish within {minutes} minutes and was stopped "
            f"— try again, or install it from a terminal with: {command}"
        )

    def _stop_pip(self) -> None:
        """Close pip's stdout, then stop it and what it started."""
        proc = self.proc
        assert proc is not None
        if proc.stdout is not None and not proc.stdout.closed:
            with contextlib.suppress(OSError):
                proc.stdout.close()
        pip_process.stop(proc, f"pip install ({' '.join(self.specs)})")

    def finish(self) -> None:
        """Once pip is over, however it ended (done, failed, the client
        left, the deadline): stop it if still running, settle its note,
        release the claim (and the hold, if pip wrote nothing) — once,
        in this order (a retry may write its own note as soon as the
        claim is free), and before the last event (the page acts on it
        at once)."""
        from bpp.web import bp_install as _b

        if self.finished:
            return
        self.finished = True
        proc = self.proc
        try:
            if proc is not None:
                self._stop_pip()
        finally:
            if proc is not None:
                # Cut off by us, or killed by any signal (a crash).
                # Cut off part way through writing: needs a repair.
                stopped = self.wrote and (
                    pip_process.was_stopped(proc) or (proc.returncode or 0) < 0
                )
                succeeded = proc.returncode == 0
                # (All "already satisfied": nothing written, nothing waits.)
                self.restart = self.wrote
                try:
                    pip_process.mark_ended(
                        self.key, stopped=stopped, succeeded=succeeded, prior=self.prior
                    )
                except Exception:
                    # The note left behind reads as an interrupted install
                    # in /api/v1/health; say why it couldn't be cleared.
                    log.warning("Couldn't clear the install note for %s", self.key, exc_info=True)
                pip_process.clear_live(proc)  # after the note; only if ours

            # Whatever failed above, never leave the claim held: every
            # analysis/import start would be refused until a restart. The
            # hold is settled with it: kept until the restart if pip
            # wrote, lifted if it didn't.
            def _settle_hold() -> None:
                if proc is not None:
                    (hold_until_restart if self.restart else release)(self.key)

            _b.finish_claim(self.token, _settle_hold)


def install_progress(key: str) -> Response:
    """Run pip for the install POST /install/<key> claimed, streaming its
    output (see bp_install)."""
    from bpp.web import bp_install as _b

    specs = _b.installable_specs(key)
    if not specs:
        raise ValidationError(f"Unknown package: {key}", key=key)

    # Refuse unless POST /install/<key> was just called (it claims the
    # install). Without this gate, the GET handler itself would launch
    # pip — the @requires_local_app decorator plus this start-gate make
    # the contract: install is a two-step POST-then-GET dance, never a
    # one-shot GET.
    token = _b.claim_for_stream(key)  # taken when the stream starts (generate)

    def generate():
        # Taken here, when the stream really starts — not when the GET was
        # answered: a client gone before reading it would leave the claim
        # taken (its reconnect refused) with no stream to release it.
        taken = _b.take_stream(token)
        if taken == "taken":  # another connection started first: it runs pip
            yield sse_event({"type": "error", "message": "This install is already running"})
            return
        if taken == "gone":  # released as stale between the GET and this start
            msg = "The install took too long to start and was cancelled — try again"
            yield sse_event({"type": "error", "message": msg})
            return
        run = _PipRun(key, specs, token)
        try:
            log.info("pip install %s starting", " ".join(specs))
            yield sse_event({"type": "start"})
            run.start()
            for line in run.lines():
                yield sse_event({"type": "log", "message": line})
            run.wait()
            run.finish()
            assert run.proc is not None
            if run.proc.returncode == 0:
                log.info("pip install %s completed", " ".join(specs))
                # Loaded after a restart: never into this running process.
                # (Nothing written: it was all installed already.)
                yield sse_event({"type": "done", "restart_required": run.restart})
            else:
                msg = run.failure_message()
                log.error("pip install %s failed: %s", " ".join(specs), msg)
                yield sse_event({"type": "error", "message": msg, "restart_required": run.restart})
        except GeneratorExit:
            # Client disconnected mid-stream. Don't yield (the connection
            # is gone), but DO clean the subprocess in `finally` so it
            # can't orphan with an open pipe.
            raise
        except Exception as e:
            log.error("Dependency install failed: %s", e, exc_info=True)
            if run.timed_out.is_set():
                msg = run.timeout_message()
            else:
                msg = "Installation failed. Check server logs for details."
            run.finish()  # stops pip first
            yield sse_event({"type": "error", "message": msg, "restart_required": run.restart})
        finally:
            # Subprocess teardown runs on normal completion AND on
            # GeneratorExit (client disconnect) AND on exception, so
            # there's no path that leaks an orphan pip process.
            if run.watchdog is not None:
                run.watchdog.cancel()
            # The client left, or pip crashed: still stop pip and release.
            run.finish()

    return Response(generate(), mimetype="text/event-stream")

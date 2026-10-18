"""The in-app installer's progress stream: GET /install/<key>/progress runs
pip for the install POST /install/<key> claimed and streams its output
(split from bp_install for the 500-LOC cap; the claim lives there)."""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import threading

from flask import Response

from bpp.errors import ValidationError
from bpp.scoring.install_hold import hold_during_install, hold_until_restart, release
from bpp.utils.logging import get_logger
from bpp.web import pip_process

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

    def _stop_pip(proc: subprocess.Popen[str]) -> None:
        """Close pip's stdout, then stop it and what it started."""
        if proc.stdout is not None and not proc.stdout.closed:
            with contextlib.suppress(OSError):
                proc.stdout.close()
        pip_process.stop(proc, f"pip install ({' '.join(specs)})")

    def generate():
        # Taken here, when the stream really starts — not when the GET was
        # answered: a client gone before reading it would leave the claim
        # taken (its reconnect refused) with no stream to release it.
        taken = _b.take_stream(token)
        if taken == "taken":  # another connection started first: it runs pip
            msg = "This install is already running"
            yield f"data: {json.dumps({'type': 'error', 'message': msg})}\n\n"
            return
        if taken == "gone":  # released as stale between the GET and this start
            msg = "The install took too long to start and was cancelled — try again"
            yield f"data: {json.dumps({'type': 'error', 'message': msg})}\n\n"
            return
        args = [sys.executable, "-m", "pip", "install", *specs]
        proc: subprocess.Popen[str] | None = None
        prior = False  # an earlier run of this install was stopped part way
        finished = False
        # Did pip write anything (it then waits for a restart)? pip downloads
        # and builds first; it writes only after "Installing collected
        # packages" — and keeps what it installed before a failing package.
        # (Not written: an install where everything was already there.)
        wrote = False
        restart = False
        timed_out = threading.Event()
        watchdog: threading.Timer | None = None

        def _time_out() -> None:
            # pip may print nothing for a long time, so the cap can't
            # wait for its next line: end pip from here (its output then
            # closes and the read loop finishes; teardown does the rest).
            if proc is not None and proc.poll() is None:
                timed_out.set()  # only when it really stopped a running pip
                pip_process.stop(proc)

        def _finish() -> None:
            """Once pip is over, however it ended (done, failed, the client
            left, the deadline): stop it if still running, settle its note,
            release the claim (and the hold, if pip wrote nothing) — once,
            in this order (a retry may write its own note as soon as the
            claim is free), and before the last event (the page acts on it
            at once)."""
            nonlocal finished, restart
            if finished:
                return
            finished = True
            try:
                if proc is not None:
                    _stop_pip(proc)
            finally:
                if proc is not None:
                    # Cut off by us, or killed by any signal (a crash).
                    # Cut off part way through writing: needs a repair.
                    stopped = wrote and (
                        pip_process.was_stopped(proc) or (proc.returncode or 0) < 0
                    )
                    succeeded = proc.returncode == 0
                    # (All "already satisfied": nothing written, nothing waits.)
                    restart = wrote
                    with contextlib.suppress(Exception):
                        pip_process.mark_ended(
                            key, stopped=stopped, succeeded=succeeded, prior=prior
                        )
                    pip_process.clear_live(proc)  # after the note; only if ours

                # Whatever failed above, never leave the claim held: every
                # analysis/import start would be refused until a restart. The
                # hold is settled with it: kept until the restart if pip
                # wrote, lifted if it didn't.
                def _settle_hold() -> None:
                    if proc is not None:
                        (hold_until_restart if restart else release)(key)

                _b.finish_claim(token, _settle_hold)

        try:
            log.info("pip install %s starting", " ".join(specs))
            yield f"data: {json.dumps({'type': 'start'})}\n\n"
            # From here on, what pip writes isn't imported by this app (or
            # its analysis processes). Set before pip starts writing; kept
            # until a restart if it writes, lifted if it doesn't.
            hold_during_install(key)
            try:
                proc = subprocess.Popen(
                    args,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    # Its own process group, so stopping pip also stops the
                    # compilers it runs (a dlib build) — see pip_process.
                    start_new_session=os.name == "posix",
                )
            except Exception:
                release(key)  # pip never ran: nothing changed
                raise
            # Noted until pip ends by itself: if it's stopped part way, the
            # page says so and how to repair it.
            prior = pip_process.mark_started(
                key,
                pip_process.command_line(args),
                getattr(proc, "pid", None),
                " ".join(args[1:]),  # its arguments: the interpreter path may differ in ps
            )
            pip_process.note_prior(proc, prior)
            # Stopped if the server quits (after the note: what it does then
            # depends on it).
            pip_process.set_live(proc, key)
            assert proc.stdout is not None  # guaranteed by stdout=PIPE
            watchdog = threading.Timer(_PIP_HARD_TIMEOUT_S, _time_out)
            watchdog.daemon = True
            watchdog.start()
            # Keep the last few non-empty log lines so we can surface them
            # in the error message. "pip exited with code 1" alone tells
            # the user nothing.
            recent_lines: list[str] = []
            for line in proc.stdout:
                line = line.rstrip()
                if line.startswith("Installing collected packages"):
                    wrote = True
                    pip_process.mark_writing(proc)
                if line:
                    recent_lines.append(line)
                    if len(recent_lines) > 6:
                        recent_lines.pop(0)
                    yield f"data: {json.dumps({'type': 'log', 'message': line})}\n\n"
            # stdout closed → pip should be exiting. Bound the wait so a
            # post-close hang can't lock the worker thread.
            proc.wait(timeout=_PIP_TERM_GRACE_S)
            if timed_out.is_set() and proc.returncode != 0:
                # (pip may still have exited by itself as the deadline hit)
                raise TimeoutError(f"pip install exceeded {_PIP_HARD_TIMEOUT_S}s; stopped it")
            _finish()
            if proc.returncode == 0:
                log.info("pip install %s completed", " ".join(specs))
                # Loaded after a restart: never into this running process.
                # (Nothing written: it was all installed already.)
                done = {"type": "done", "restart_required": restart}
                yield f"data: {json.dumps(done)}\n\n"
            else:
                # Find the most informative line from the tail. Prefer
                # ones that start with ERROR: which pip uses for its
                # actual diagnostic; fall back to the very last line.
                hint = next(
                    (line_ for line_ in reversed(recent_lines) if line_.startswith("ERROR:")),
                    recent_lines[-1] if recent_lines else "",
                )
                msg = f"pip exited with code {proc.returncode}"
                if hint:
                    msg += f" — {hint}"
                log.error("pip install %s failed: %s", " ".join(specs), msg)
                error = {"type": "error", "message": msg, "restart_required": restart}
                yield f"data: {json.dumps(error)}\n\n"
        except GeneratorExit:
            # Client disconnected mid-stream. Don't yield (the connection
            # is gone), but DO clean the subprocess in `finally` so it
            # can't orphan with an open pipe.
            raise
        except Exception as e:
            log.error("Dependency install failed: %s", e, exc_info=True)
            if timed_out.is_set():
                minutes = round(_PIP_HARD_TIMEOUT_S / 60)
                # The exact command for this app's Python, quoted for a shell
                # (the specs hold >=, < and ;).
                command = pip_process.command_line(args)
                msg = (
                    f"The install didn't finish within {minutes} minutes and was stopped "
                    f"— try again, or install it from a terminal with: {command}"
                )
            else:
                msg = "Installation failed. Check server logs for details."
            _finish()  # stops pip first
            error = {"type": "error", "message": msg, "restart_required": restart}
            yield f"data: {json.dumps(error)}\n\n"
        finally:
            # Subprocess teardown runs on normal completion AND on
            # GeneratorExit (client disconnect) AND on exception, so
            # there's no path that leaks an orphan pip process.
            if watchdog is not None:
                watchdog.cancel()
            # The client left, or pip crashed: still stop pip and release.
            _finish()

    return Response(generate(), mimetype="text/event-stream")

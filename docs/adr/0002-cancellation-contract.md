# ADR 0002 — Cancellation contract

**Status.** Accepted. Implemented in `bpp/utils/cancel.py`; the
mirror-thread bridge lives in `BoundedSubprocessRunner` (ADR 0001), and
recovery handlers in `bpp/web/face_recovery.py` take the captured
`library_path`. Inside the face-extraction child, phases still poll a
`cancellation_check` callable derived from the token.

**Context.** Before this ADR the codebase had three different
cancellation styles: `threading.Event` in `BackgroundWorker._cancelled`, a
`multiprocessing.Event` passed into the scoring subprocess child, and a
raw `Callable[[], bool]` named `cancellation_check` inside
`extract_and_cluster_faces`. Worse:

- `run_face_extraction_subprocess` did NOT propagate the cancel signal
  into the child. The cancel button in the UI stopped emitting *visible*
  progress but the child ran to completion.
- The chunk loop in `run_face_extraction_subprocess` had no cancel
  check between chunks.
- Recovery handlers closed over `get_ctx_or_none`, not over a captured
  library identity. After `switch_library`, an in-flight recovery for
  the OLD library could write into the NEW library's
  DB. (This was a real data-corruption hole, not just a cancel issue.)

**Decision.** Ship `bpp/utils/cancel.py` with a single
`CancellationToken` protocol:

```python
class CancellationToken(Protocol):
    def is_set(self) -> bool: ...
    def set(self) -> None: ...
    def wait(self, timeout: float | None = None) -> bool: ...
```

Two concrete implementations:

1. **`ThreadCancellation`** — wraps `threading.Event`. Used by
   `BackgroundWorker._cancelled`. Subscript: `.event` for the raw
   `threading.Event` so legacy call sites can keep using it during the
   transition.
2. **`ProcessCancellation`** — wraps `multiprocessing.Event`. Picklable.
   Used by every subprocess spawn target. The parent constructs one
   per run, passes it to the child via the subprocess runner args, and
   sets it from the `BackgroundWorker._cancelled` shadow via a small
   "mirror" thread that polls the thread Event every 100 ms.

The mirror thread is the bridge between the in-process Flask request
thread that calls `.cancel()` and the cross-process Event the child
checks. It's added by `BoundedSubprocessRunner` automatically — no
caller plumbing.

**Recovery handler library-binding.** Recovery handlers registered by
`register_face_extraction_retry_recovery` etc. MUST capture the
library path at registration time, not call `get_ctx_or_none()` at
recovery time. The handler signature becomes:

```python
def register_face_extraction_retry_recovery(library_path: str) -> None: ...
```

Inside the handler body, the live `ctx` is fetched, but the handler
short-circuits with a log warning if `ctx.library_path != library_path`.

**Consequences.**
- A user clicking Cancel during a 5,000-photo analyze reliably stops
  the child: it exits at its next per-photo cancel check, and the parent
  (which doesn't abort its drain, but waits for the child's next message
  for at most `message_timeout_s`) then joins within `graceful_join_s` +
  `force_join_s` — no more "stop showing progress but keep eating CPU."
- `switch_library` no longer creates a window where the OLD library's
  recovery rewrites the NEW library's data.
- New tests must actually fire a subprocess and join under timeout to
  prove cancel works. Mock-based tests are insufficient.

**Out of scope.**
- Killing in-flight HTTP requests on cancel.
- Cancellation of `ProcessPoolExecutor` workers spawned *inside* the
  face-extract subprocess (a different problem, handled by the
  face-extraction phases themselves).

# ADR 0001 — Subprocess runner contract

**Status.** Accepted. Implemented in `bpp/utils/subprocess_runner.py`;
scoring (`bpp/web/analyze_scoring.py`) and face extraction
(`bpp/web/analyze_face_extract.py`) both run through it. The shipped API
generalizes the sketch below: each worker supplies a `Phase` (target,
argv builder, per-message reducer) and `run(payload, *, cancel_event,
progress_callback)` returns `(state, child_pid)`. The constructor's
timeouts are `message_timeout_s`, `graceful_join_s` and `force_join_s`
(the sketch's `queue_get_timeout_s` / `graceful_timeout_s` /
`force_timeout_s`). Point 7 (config
marshalling) is not enforced by type — callers still pass the plain-dict
snapshot from `_snapshot_config`, a thin wrapper over
`ConfigSnapshot.from_live`. The "spawn only" constraint under
*Consequences* is not enforced either: the runner uses
`multiprocessing.Process` with the platform default start method (spawn
on macOS and Windows, fork on Linux under Python 3.11). Thread pinning
holds in both cases — a spawned child re-imports `analyze_worker`, and a
forked child inherits the parent's already-pinned environment.

**Context.** Before this ADR, scoring (analyze phase 1) and face
extraction (phase 2) duplicated ~400 LOC of subprocess plumbing (queue,
sentinel, drain loop, fatal_error vs. progress dispatch,
graceful-then-force join, exit-code post-mortem). A bug found in one
almost never got back-ported to the other. CLIP runs in-thread under
`BackgroundWorker`, so it does NOT share this surface.

The native-thread-pinning block near the top of `bpp/web/analyze_worker.py`
(`OMP_NUM_THREADS=1`, etc.) MUST execute before any C-extension import
in both parent and child. `multiprocessing.spawn` re-imports the parent
module in the child — that's how the pinning currently propagates.
Any abstraction that changes import order risks a silent SIGSEGV on
workers≥2 large libraries.

The runtime `Config` holds a bound method (`_get_conn`) that pulls in
`mappingproxy`, which the spawn-method `ForkingPickler` refuses with
`TypeError: cannot pickle 'mappingproxy' object`. The current fix
(`_snapshot_config`) flattens to a plain dict at the parent/child
boundary; a typed `ConfigSnapshot` (`bpp/utils/config_snapshot.py`) wraps
it with a picklability gate test.

**Decision.** Ship `BoundedSubprocessRunner[I, O]` in
`bpp/utils/subprocess_runner.py` with this contract:

1. **Constructor:** target callable (the spawn entry point),
   `graceful_timeout_s` (default `SUBPROCESS_GRACEFUL_JOIN_S`),
   `force_timeout_s`, `queue_get_timeout_s`.
2. **Run:** `.run(input: I, token: CancellationToken,
   progress_cb: Callable[[dict], None] | None) -> O`.
3. **Queue protocol:** the child puts `{"type": "progress", ...}`,
   `{"type": "fatal_error", "error": str, "traceback": str}`, the
   final result payload, and finally `_SENTINEL`. The runner handles
   each message type uniformly.
4. **Timeout:** queue-get timeout fires → parent logs at WARNING with
   `pid`, `alive`, `exitcode`, `results_so_far/total` and joins the
   child force-kill within `force_timeout_s`. The runner is responsible
   for the post-mortem `proc.exitcode` check (SIGKILL/SIGSEGV detection).
5. **Cancellation:** the token (see ADR 0002) is passed
   to the child as `multiprocessing.Event`; the child polls it between
   photos and exits. The parent's drain doesn't check it: it waits for
   the child's next message (at most `message_timeout_s`), then joins.
6. **Env-var-pinning preservation:** the runner spawn target MUST
   import via the `analyze_worker` module path so the env-var setdefault
   block at module top fires in the child before any cv2/numpy/onnxruntime
   import. A test (`TestEnvVarPreservation.test_OMP_NUM_THREADS_propagates_to_child`
   in `tests/test_subprocess_runner.py`) spawns a real child and asserts `os.environ["OMP_NUM_THREADS"] == "1"`
   inside.
7. **Config marshalling:** parent calls `ConfigSnapshot.from_live(config)`
   before constructing the child args. The runner refuses to start with
   a raw `Config` (typed parameter — must be `ConfigSnapshot`).

**Consequences.**
- Adding a new subprocess worker = implement one class plus its spawn target.
- Subprocess test matrix (SIGKILL, SIGSEGV, timeout, fatal_error,
  picklability) lives in one place.
- The `_snapshot_config` shim stays for one release as a wrapper around
  `ConfigSnapshot.from_live().values` so existing call sites keep working.
- The runner is constrained to `multiprocessing.spawn` start method. Fork
  is forbidden — it doesn't re-import the parent module, so env-var
  pinning wouldn't propagate.

**Out of scope.** CLIP worker. Plugin-provided workers. async/await
substrates (not used here).

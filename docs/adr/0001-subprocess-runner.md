# ADR 0001 — Subprocess runner contract

**Status.** Proposed (P0). Locks the shape that P2 implements.

**Context.** Scoring (Phase 1) and face-extraction (Phase 2) currently
duplicate ~400 LOC of subprocess plumbing (queue, sentinel, drain loop,
fatal_error vs. progress dispatch, graceful-then-force join, exit-code
post-mortem). A bug found in one almost never gets back-ported to the
other. CLIP runs in-thread under `BackgroundWorker`, so it does NOT
share this surface.

The native-thread-pinning block at the top of `analyze_worker.py:34–37`
(`OMP_NUM_THREADS=1`, etc.) MUST execute before any C-extension import
in both parent and child. `multiprocessing.spawn` re-imports the parent
module in the child — that's how the pinning currently propagates.
Any abstraction that changes import order risks a silent SIGSEGV on
workers≥2 large libraries.

The runtime `Config` holds a bound method (`_get_conn`) that pulls in
`mappingproxy`, which the spawn-method `ForkingPickler` refuses with
`TypeError: cannot pickle 'mappingproxy' object`. The current fix
(`_snapshot_config`) flattens to a plain dict at the parent/child
boundary; P0 wraps it in a typed `ConfigSnapshot` with a picklability
gate test.

**Decision.** P2 ships `BoundedSubprocessRunner[I, O]` in
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
5. **Cancellation:** the token (P1 contract — see ADR 0002) is passed
   to the child as `multiprocessing.Event`; the runner checks it in
   the drain loop between messages.
6. **Env-var-pinning preservation:** the runner spawn target MUST
   import via the `analyze_worker` module path so the env-var setdefault
   block at module top fires in the child before any cv2/numpy/onnxruntime
   import. A test (`test_subprocess_runner_preserves_env_var_pinning`)
   spawns a real child and asserts `os.environ["OMP_NUM_THREADS"] == "1"`
   inside.
7. **Config marshalling:** parent calls `ConfigSnapshot.from_live(config)`
   before constructing the child args. The runner refuses to start with
   a raw `Config` (typed parameter — must be `ConfigSnapshot`).

**Consequences.**
- Adding a Phase-4 worker = implement one class plus its spawn target.
- Subprocess test matrix (SIGKILL, SIGSEGV, timeout, fatal_error,
  picklability) lives in one place.
- The `_snapshot_config` shim stays for one release as a wrapper around
  `ConfigSnapshot.from_live().values` so existing call sites keep working.
- The runner is constrained to `multiprocessing.spawn` start method. Fork
  is forbidden — it doesn't re-import the parent module, so env-var
  pinning wouldn't propagate.

**Out of scope.** CLIP worker. Plugin-provided workers. async/await
substrates (not used here).

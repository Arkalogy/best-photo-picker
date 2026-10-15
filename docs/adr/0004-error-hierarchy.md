# ADR 0004 — Error & result types

**Status.** Accepted; implemented with a revised class set.
`bpp/errors.py` defines `BppError` (user-safe `user_message`, separate
`diagnostic_message`, `http_status`, `code`, `context`, `to_dict()`) with
`ValidationError`, `NotFoundError`, `ForbiddenError`, `ConflictError`,
`ResourceExhaustedError`, `FeatureUnavailableError` and `IntegrityError`
subclasses. The pre-existing exceptions (`ModelIntegrityError`,
`ServingLockError`, …) inherit `BppError` while keeping their original
parents. A single `errorhandler(BppError)` in `bpp/web/app.py` renders
the envelope and logs the diagnostic. The `TransientError` /
`ConfigError` / `UserError` / `SystemError` split and `retry_after_s`
sketched below were not adopted; a handful of endpoints still return
ad-hoc error JSON.

**Context.** Before this ADR, bpp had exactly two custom exceptions:
- `ModelIntegrityError` (`bpp/scoring/model_base.py:36`) — SHA mismatch
  on a cached ML model; must NOT silently degrade.
- `ServingLockError` (`bpp/utils/serving_lock.py:36`) — couldn't acquire
  the per-library serving lock at startup.

Everywhere else, failures bubbled through plain `Exception`,
`RuntimeError`, `sqlite3.Error`, etc. Endpoints caught broadly with
~47 hand-rolled `return jsonify({"error": ...}), 500` sites. The UI
got a string that mixed user-actionable messages
(`"Photos folder doesn't exist"`) with internal noise
(`"Database error during merge"`). On-call got the same string in
`server.log` with no category tag.

`BackgroundWorker._safe_run` (`base_worker.py:164–179`) already
strips filesystem paths from error messages before they reach the
client — this is a security-relevant defense. Any error-hierarchy
refactor must integrate with that defense, not replace it.

**Decision.** Ship `bpp/errors.py`:

```python
class BppError(Exception):
    """Base. Carries both a user-safe message and a diagnostic message."""
    user_message: str
    diagnostic_message: str
    retry_after_s: float | None = None   # only meaningful for TransientError

    def __init__(self, user_message: str, diagnostic_message: str | None = None) -> None:
        self.user_message = user_message
        self.diagnostic_message = diagnostic_message or user_message
        super().__init__(self.diagnostic_message)


class TransientError(BppError):
    """Retryable: NAS flake, network blip, transient lock contention.
    UI should suggest "try again in a moment". `retry_after_s` set."""


class ConfigError(BppError):
    """User's settings are wrong (bad path, unparseable value).
    UI should point at Settings."""


class IntegrityError(BppError):
    """Data corruption / SHA mismatch / picklability gate failure.
    MUST NOT be silently caught and downgraded — surface to the user
    AND fail loudly in logs. Inherits from BppError but has a class-level
    `propagate = True` flag the endpoint transformer respects."""
    propagate: ClassVar[bool] = True


class UserError(BppError):
    """User supplied bad input (filepath traversal, missing field).
    HTTP 400. Surface user_message; suppress diagnostic in production."""


class SystemError(BppError):
    """Bug. Surface a generic 'Something went wrong, see logs' to the
    user. Diagnostic message + full traceback to server.log."""
```

**Endpoint transformer.** Single Flask `errorhandler(BppError)` that:
1. Routes by class → status code (Transient=503+Retry-After,
   Config=409, Integrity=500+propagate, User=400, System=500).
2. Returns `jsonify({"error": err.user_message, "category": class_name})`.
3. Logs `err.diagnostic_message` at WARNING (transient/user) or ERROR
   (config/integrity/system) with `exc_info=True`.
4. Strips filesystem paths from the user-facing message via the same
   helper `BackgroundWorker._safe_run` uses today.

**Worker integration.** `BackgroundWorker._safe_run` keeps its current
path-strip defense. When a `BppError` reaches `_safe_run`, the message
goes through `err.user_message` (already sanitized) instead of the
generic-Exception path-strip — the defense applies to plain Exception
subclasses (legacy) AND to BppError (new code) uniformly.

**Migration.** Migrate the top 10 highest-traffic endpoints first
to prove the contract works. The 37+ remaining ad-hoc error returns
get migrated as a separate cleanup PR (or whenever each endpoint is
touched next — opportunistic).

**Consequences.**
- UI can show category-specific affordances (retry button for transient;
  Settings deep-link for config).
- On-call gets a category tag in every error log line.
- `IntegrityError` from ModelIntegrityError propagates instead of being
  caught — the "tampered cache silently downgrades to dlib" failure mode
  becomes impossible.
- Tests (as shipped): `tests/test_errors.py` (class hierarchy, status
  codes, envelope shape, pickle round-trips, legacy exceptions caught as
  `BppError`) and `tests/test_errors_flask_handler.py` (the
  `errorhandler(BppError)` response + logging). The three tests
  originally named here (`test_transient_error_carries_retry_after`,
  `test_error_handler_strips_filesystem_paths`,
  `test_integrity_error_propagates_not_caught`) were never written,
  since the classes they covered were not adopted.

**Out of scope.**
- Replacing `toast()` in JS.
- Sentry / external error-reporting integration.
- Localizing user-facing messages.

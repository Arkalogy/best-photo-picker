"""Shared memoized license-load gate.

Used by every ``ModelSingleton`` and by the thread-local models (YuNet /
SFace / dlib / the Haar cascade) that opt out of it. Each model needs
the same fail-closed acceptance gate before inference: call
:func:`bpp.registry.enforce_load_policy_for`, then memoize the pass so
the per-photo hot path doesn't re-read the acceptance log on every
call (~50k reads on a large library).

Before this helper the pattern was copy-pasted three times (one
``_*_policy_ok`` global + ``_enforce_*_policy`` + a reset hook per
module), so a fix to the gate logic — like clearing the memo on reset —
had to be made in three places. :class:`MemoizedLoadGate` is the single
home for it.

Semantics:

- A check's result (pass or refusal) is memoized only while the
  acceptance log and use-context files are unchanged, so an accept, a
  withdrawal or a use-context switch takes effect on the next use, in
  any process.
- :meth:`reset` re-arms the gate. The model-registry reset hooks call it
  (alongside clearing their availability negative-cache) so a revoke /
  re-download forces the next load to re-check the acceptance.
"""

from __future__ import annotations

from typing import Any

#: Every gate constructed this process (module-level singletons, so a
#: plain list is fine). Lets a revoke re-arm the gate(s) for one
#: registry id without knowing which scoring module owns them.
_ALL_GATES: list[MemoizedLoadGate] = []


def reset_gates_for(registry_id: str) -> int:
    """Re-arm every gate guarding ``registry_id``; returns how many.

    Called when an acceptance is withdrawn so the revoke takes effect
    without a restart — a memoized pass would otherwise keep letting
    the model load for the rest of the process."""
    n = 0
    for gate in _ALL_GATES:
        if gate.registry_id == registry_id:
            gate.reset()
            n += 1
    _blocked_logged_reset_for(registry_id)
    return n


def _inputs_signature() -> tuple:
    """Identity of the two files a license decision depends on: the
    acceptance log and the use-context declaration. Any accept, withdraw
    or use-context switch rewrites one of them, which changes this."""
    from bpp.registry.acceptance_log import get_acceptance_log_path
    from bpp.registry.use_context_store import get_use_context_path

    sig = []
    for path in (get_acceptance_log_path(), get_use_context_path()):
        try:
            st = path.stat()
            sig.append((str(path), st.st_mtime_ns, st.st_size))
        except OSError:
            sig.append((str(path), None, None))
    return tuple(sig)


class MemoizedLoadGate:
    """Fail-closed license gate for one registry entry.

    A pass is memoized only while the acceptance log and use-context
    files are unchanged, so an accept, a withdrawal or a switch to
    "Commercial" takes effect on the next use — in the server and in a
    running analysis subprocess alike — at the cost of two ``stat``
    calls per use.
    """

    __slots__ = ("_blocked", "_passed_for", "registry_id")

    def __init__(self, registry_id: str) -> None:
        self.registry_id = registry_id
        self._passed_for: tuple | None = None
        #: (signature, PolicyResult) of the last refusal — raised again
        #: without re-checking while the files are unchanged. The result,
        #: not the exception: a stored exception would keep the stack
        #: frames (decoded photos and all) of the first refusal alive.
        self._blocked: tuple[tuple, Any] | None = None
        _ALL_GATES.append(self)

    def enforce(self) -> None:
        """Raise :class:`~bpp.registry.policy.ModelLoadBlockedError` if the
        entry isn't allowed. No-op while a pass is still current."""
        sig = _inputs_signature()
        if self._passed_for == sig:
            return
        if self._blocked is not None and self._blocked[0] == sig:
            from bpp.registry.policy import ModelLoadBlockedError

            # A fresh error each time: re-raising one object would grow
            # its traceback on every photo.
            raise ModelLoadBlockedError(self._blocked[1])
        # Late import: bpp.registry pulls in policy machinery; importing it
        # at module load would create a circular path through scoring.
        from bpp.registry import ModelLoadBlockedError, enforce_load_policy_for

        self._passed_for = None
        try:
            enforce_load_policy_for(self.registry_id)
        except ModelLoadBlockedError as exc:
            self._blocked = (sig, exc.result)
            raise
        self._blocked = None
        self._passed_for = sig

    def reset(self) -> None:
        """Re-arm the gate so the next :meth:`enforce` re-checks acceptance."""
        self._passed_for = None
        self._blocked = None

    @property
    def passed(self) -> bool:
        """Whether the last check passed (introspection/tests)."""
        return self._passed_for is not None


# ── Plugin-model gates ────────────────────────────────────────────────

_shared_gates: dict[str, MemoizedLoadGate] = {}
_unknown_warned: set[str] = set()
_unknown_ids: set[str] = set()


def license_allows(registry_id: str, *, feature: str) -> bool:
    """Whether the model behind ``feature`` (e.g. a plugin detector) may
    run now. One gate per registry id, shared by every caller. A block
    is logged once per feature (INFO); an id with no registry entry is
    a plugin bug, logged once at WARNING. Never raises."""
    from bpp.registry.policy import ModelLoadBlockedError

    if registry_id in _unknown_ids:
        from bpp.registry.model_registry import get_entry

        if get_entry(registry_id) is None:
            _warn_unknown_once(feature, registry_id)
            return False
        _unknown_ids.discard(registry_id)  # registered since: check it
    gate = _shared_gates.get(registry_id)
    if gate is None:
        gate = _shared_gates[registry_id] = MemoizedLoadGate(registry_id)
    try:
        gate.enforce()
    except ModelLoadBlockedError as exc:
        log_blocked_once(feature, exc)
        return False
    except ValueError:
        _unknown_ids.add(registry_id)
        _warn_unknown_once(feature, registry_id)
        return False
    return True


def _warn_unknown_once(feature: str, registry_id: str) -> None:
    if feature in _unknown_warned:
        return
    _unknown_warned.add(feature)
    from bpp.utils.logging import get_logger

    get_logger(__name__).warning(
        "%s names registry_id %r, which has no model-registry entry; "
        "it won't run until the plugin registers that entry.",
        feature,
        registry_id,
    )


# ── Once-per-process "feature skipped" notice ─────────────────────────

_blocked_logged: set[str] = set()


def log_blocked_once(feature: str, exc: BaseException) -> None:
    """Log at INFO — once per process per ``feature`` — that a scorer
    was skipped because its model's license gate refused the load.

    Scorers skip a blocked model per photo (raising mid-analysis would
    abort the run), so the skip itself has to be visible somewhere:
    without this a stale acceptance (e.g. after a disclaimer-version
    bump) made features silently stop. The Settings → Models picker
    shows the matching "re-accept needed" row state; this is the log
    breadcrumb. Per-photo repeats stay at DEBUG.
    """
    from bpp.utils.logging import get_logger

    log = get_logger(__name__)
    if feature in _blocked_logged:
        log.debug("%s skipped (model not accepted): %s", feature, exc)
        return
    _blocked_logged.add(feature)
    log.info(
        "%s skipped for this run: its model needs (re-)acceptance in Settings → Models — %s",
        feature,
        exc,
    )


def _reset_blocked_log_for_tests() -> None:
    _blocked_logged.clear()
    _unknown_warned.clear()
    _unknown_ids.clear()


def _blocked_logged_reset_for(_registry_id: str) -> None:
    """A revoke changes what the user should be told — let the next
    skip log its INFO notice again."""
    _blocked_logged.clear()

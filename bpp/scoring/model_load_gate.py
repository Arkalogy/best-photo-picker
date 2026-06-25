"""Shared memoized license-load gate for the thread-local ML models
(YuNet / SFace / dlib) that opt out of ``ModelSingleton``.

Each of those models needs the same fail-closed acceptance gate before
inference: call :func:`bpp.registry.enforce_load_policy_for` once, then
memoize the pass so the per-photo hot path doesn't re-read and re-parse
the acceptance-log file on every call (~50k reads on a large library).

Before this helper the pattern was copy-pasted three times (one
``_*_policy_ok`` global + ``_enforce_*_policy`` + a reset hook per
module), so a fix to the gate logic — like clearing the memo on reset —
had to be made in three places. :class:`MemoizedLoadGate` is the single
home for it.

Semantics (unchanged from the original per-module gates):

- Only a PASSING check is memoized. A blocked run re-checks (cheap — a
  blocked run does no model work), and a freshly-spawned worker
  subprocess re-evaluates from scratch, so a mid-session acceptance
  takes effect on the next run.
- :meth:`reset` re-arms the gate. The model-registry reset hooks call it
  (alongside clearing their availability negative-cache) so a revoke /
  re-download forces the next load to re-check the acceptance.
"""

from __future__ import annotations


class MemoizedLoadGate:
    """Fail-closed, process-memoized license gate for one registry entry."""

    __slots__ = ("_passed", "registry_id")

    def __init__(self, registry_id: str) -> None:
        self.registry_id = registry_id
        self._passed = False

    def enforce(self) -> None:
        """Raise :class:`~bpp.registry.policy.ModelLoadBlockedError` if the
        entry isn't accepted. No-op once a check has passed this process."""
        if self._passed:
            return
        # Late import: bpp.registry pulls in policy machinery; importing it
        # at module load would create a circular path through scoring.
        from bpp.registry import enforce_load_policy_for

        enforce_load_policy_for(self.registry_id)
        self._passed = True

    def reset(self) -> None:
        """Re-arm the gate so the next :meth:`enforce` re-checks acceptance."""
        self._passed = False

    @property
    def passed(self) -> bool:
        """Whether a check has passed this process (introspection/tests)."""
        return self._passed

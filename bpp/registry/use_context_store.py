"""Local-only file-backed storage for the user's declared use context.

Batch 5 / item 15 of the legal-posture rollout. The user answers the
commercial-use gate exactly once per machine and the answer is
persisted in a tiny JSON file in the same per-user config directory
that already houses :data:`bpp.registry.acceptance_log`. The
acceptance row records what the user *was* declaring at the moment
of an acceptance; this store records what they currently are.

Why this lives outside the library DB

Same reason the acceptance log does (see Q3): the gate is a
declaration between the user and Arkalogy, not a per-library
artifact. A user who maintains multiple libraries should only
answer the gate once.

Why the file is JSON, not JSONL

The acceptance log is append-only (every acceptance event is
preserved). This store is current-state-only — the file is
re-written atomically when the user changes their declaration via
``bpp model use-context set`` or Settings. JSON keeps the file
trivially editable by a curious power user.

Schema

.. code-block:: json

    {
      "schema_version": 1,
      "use_context": "personal" | "research" | "commercial" | "unspecified",
      "set_at": "ISO-8601 UTC timestamp",
      "set_via": "first-launch-gate" | "settings" | "cli" | "test",
      "history": [
        {"use_context": "...", "set_at": "...", "set_via": "..."},
        ...
      ]
    }

Old entries land in ``history`` so a future maintainer can see the
declaration timeline. The file is small (a handful of entries over
a user's lifetime).

The path can be overridden by environment variable
``BPP_USE_CONTEXT_PATH`` for tests / dev workflows that don't want
to touch the user's real config directory.
"""

from __future__ import annotations

import contextlib
import datetime
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from bpp.registry.use_context import UseContext
from bpp.utils.logging import get_logger

_log = get_logger(__name__)


SCHEMA_VERSION = 1


def get_use_context_path() -> Path:
    """Return the path to the use-context JSON store.

    Honours ``BPP_USE_CONTEXT_PATH`` for tests. Otherwise lives
    alongside the libraries.json and model-acceptance.jsonl files
    under ``${XDG_CONFIG_HOME:-~/.config}/bpp/``.
    """
    override = os.environ.get("BPP_USE_CONTEXT_PATH")
    if override:
        return Path(override)
    config_home = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    return Path(config_home) / "bpp" / "use-context.json"


@dataclass(frozen=True)
class UseContextRecord:
    """The current declaration plus a short audit trail.

    ``history`` is a list of older declarations in chronological
    order. The current declaration is NOT duplicated into history
    — ``use_context`` + ``set_at`` + ``set_via`` are the current
    state; history is "what came before."
    """

    use_context: UseContext = UseContext.UNSPECIFIED
    set_at: str = ""
    set_via: str = ""
    history: tuple[dict[str, str], ...] = field(default_factory=tuple)
    schema_version: int = SCHEMA_VERSION


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def read_record(*, path: Path | None = None) -> UseContextRecord:
    """Load the current declaration. Returns the empty record (with
    ``UseContext.UNSPECIFIED``) when the file is missing, empty, or
    unparseable — the policy layer treats UNSPECIFIED as "user has
    not answered yet" and prompts.
    """
    target = path or get_use_context_path()
    if not target.exists():
        return UseContextRecord()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _log.warning(
            "use-context store at %s is unreadable (%s); treating as "
            "UNSPECIFIED so the gate re-prompts.",
            target,
            exc,
        )
        return UseContextRecord()
    try:
        ctx = UseContext(raw.get("use_context", UseContext.UNSPECIFIED.value))
    except ValueError:
        ctx = UseContext.UNSPECIFIED
    return UseContextRecord(
        use_context=ctx,
        set_at=str(raw.get("set_at", "")),
        set_via=str(raw.get("set_via", "")),
        history=tuple(raw.get("history", [])),
        schema_version=int(raw.get("schema_version", 1)),
    )


def get_use_context(*, path: Path | None = None) -> UseContext:
    """Return the user's current declared use context.

    Convenience wrapper over :func:`read_record` for call sites that
    only need the enum value.
    """
    return read_record(path=path).use_context


def has_been_set(*, path: Path | None = None) -> bool:
    """Return ``True`` if the user has ever answered the gate (current
    declaration is not :data:`UseContext.UNSPECIFIED`).

    Pipeline call sites use this to decide whether to prompt before
    proceeding.
    """
    return read_record(path=path).use_context is not UseContext.UNSPECIFIED


def set_use_context(
    use_context: UseContext,
    *,
    set_via: str,
    path: Path | None = None,
) -> UseContextRecord:
    """Persist ``use_context`` as the current declaration.

    Atomic on POSIX: writes to a tempfile alongside the target and
    ``os.replace``-s into place so a power-loss during the write
    cannot leave a partial file.

    ``set_via`` is a short label recorded with the entry — e.g.
    ``"first-launch-gate"``, ``"settings"``, ``"cli"``, ``"test"`` —
    so the audit trail records *how* the declaration was made.

    The previous declaration is appended to ``history``. The history
    is bounded at 50 entries — older ones are dropped silently. A
    user who changes their declaration 50 times is operating well
    outside the intended flow.
    """
    target = path or get_use_context_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    prior = read_record(path=target)
    now = _utc_now_iso()

    new_history: list[dict[str, str]] = list(prior.history)
    if prior.use_context is not UseContext.UNSPECIFIED:
        new_history.append(
            {
                "use_context": prior.use_context.value,
                "set_at": prior.set_at,
                "set_via": prior.set_via,
            }
        )
    new_history = new_history[-50:]

    payload = {
        "schema_version": SCHEMA_VERSION,
        "use_context": use_context.value,
        "set_at": now,
        "set_via": set_via,
        "history": new_history,
    }

    # Atomic write: tempfile + os.replace. fsync the tempfile before
    # the rename so the bytes survive a power-loss before the
    # filesystem flushes the directory entry.
    fd, tmp_path = tempfile.mkstemp(prefix=".use-context-", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.write("\n")
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                _log.debug("fsync on use-context tempfile failed; rename will still proceed")
        os.replace(tmp_path, target)
    except Exception:
        # Best-effort cleanup if the rename or fsync raised.
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise

    return UseContextRecord(
        use_context=use_context,
        set_at=now,
        set_via=set_via,
        history=tuple(new_history),
    )


# ── Test-only helpers ──


def _reset_for_tests(*, path: Path | None = None) -> None:
    """Delete the use-context file. Test-only — production code never
    needs to clear the declaration; setting it to UNSPECIFIED would
    do the wrong thing for the audit trail."""
    target = path or get_use_context_path()
    if target.exists():
        target.unlink()

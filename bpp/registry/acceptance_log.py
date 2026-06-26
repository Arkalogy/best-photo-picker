"""Local-only acceptance log for restricted-model click-through events.

Batch 4 / item 6 of the legal-posture rollout. Q3 decided the log
lives in a separate per-user file outside any specific library
directory: a JSONL file at ``~/.config/bpp/model-acceptance.jsonl``
(or the platform equivalent under ``XDG_CONFIG_HOME``). One row per
acceptance, append-only.

Why it's a separate file rather than the library SQLite DB

* Acceptance is a contract between the user and Arkalogy. It is
  about the *user*, not the *library* — a user who switches
  libraries (BPP supports multi-library workflows) should not lose
  the record of acknowledgments they have already made.
* The whole evidentiary value of the log is "we can prove the user
  saw and accepted exactly this text." That value is much higher
  when the artifact is a small standalone JSONL file readable with
  a text editor than a row inside a 3 GB SQLite photo DB.
* Survives library deletes. If the user trashes their library, the
  acceptance log is still on disk.

What's recorded per row

Each row is a dict containing the fields the second-round legal
review specified (item 19 + Q11):

* ``model_id`` — the entry id from :mod:`bpp.registry.model_registry`.
* ``model_sha256`` — the registry's recorded SHA-256 of the weight
  file the user is being asked to download. Empty string ``""``
  when the entry is metadata-only.
* ``ack_text_version`` + ``ack_text_sha256`` — pointers to the
  canonical disclaimer wording (from :mod:`bpp.registry.disclaimers`).
* ``use_context_text_version`` + ``use_context_text_sha256`` —
  pointers to the trap-T5/T6 text + biometric note shown in the
  dialog (from :mod:`bpp.registry.use_context`).
* ``use_context_at_acceptance`` — the user's declared use context
  at the moment of acceptance (``"personal"`` / ``"research"`` /
  ``"commercial"`` / ``"unspecified"``).
* ``separate_rights_asserted`` — boolean. ``True`` when the user
  triggered the separate-rights override on a commercial-context
  acceptance.
* ``source_of_rights_note`` — Q11 free-text field. Local only,
  never transmitted. Empty string when not provided.
* ``terms_url`` + ``terms_permalink_url`` + ``terms_retrieved_at``
  — copied from the registry entry at acceptance time so a future
  upstream README change doesn't drift away from what the user
  agreed to.
* ``accepted_at`` — ISO-8601 timestamp from the caller (UTC).
  Required at the call site so this module can stay deterministic
  for tests.
* ``schema_version`` — integer; allows future log-schema migration
  to keep older rows readable.

The file format is JSONL (newline-delimited JSON). Append-only.
Readers tolerate truncated last lines (handles power-loss mid-
write).

The path can be overridden by environment variable
``BPP_ACCEPTANCE_LOG_PATH`` for tests / dev workflows that don't
want to touch the user's real config directory.
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import os
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bpp.utils.logging import get_logger

_log = get_logger(__name__)


SCHEMA_VERSION = 2
"""Integer schema version. Incremented when the row shape changes
in a way that would surprise an old reader.

Version history

* ``1`` — Batch-4 baseline. Records aggregate acceptance state; the
  per-checkbox engagement record is validated then discarded.
* ``2`` — Adds ``checkbox_responses`` (item 5 evidentiary chain).
  Each required checkbox the user engaged with is recorded
  individually so a future audit can prove "user clicked checkbox
  X" not just "user submitted a valid form". Older v1 rows on
  disk still parse — :meth:`AcceptanceRow.from_dict` defaults the
  field to ``{}`` when missing.
"""


def get_acceptance_log_path() -> Path:
    """Return the path to the acceptance-log JSONL file.

    Honours ``BPP_ACCEPTANCE_LOG_PATH`` for tests. Otherwise lives
    alongside the existing ``libraries.json`` config under
    ``XDG_CONFIG_HOME/bpp/`` (or ``~/.config/bpp/`` when the env var
    isn't set).
    """
    override = os.environ.get("BPP_ACCEPTANCE_LOG_PATH")
    if override:
        return Path(override)
    config_home = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    return Path(config_home) / "bpp" / "model-acceptance.jsonl"


@dataclass(frozen=True)
class AcceptanceRow:
    """One row of the acceptance log.

    Frozen so a writer cannot accidentally mutate a record after
    serialising it. ``field(default="")`` on the optional strings
    keeps the dataclass JSON-friendly: ``""`` round-trips as
    ``""`` rather than ``None``.
    """

    model_id: str
    model_sha256: str
    ack_text_version: str
    ack_text_sha256: str
    use_context_text_version: str
    use_context_text_sha256: str
    use_context_at_acceptance: str
    separate_rights_asserted: bool
    terms_url: str
    terms_permalink_url: str  # "" if upstream has no permalink form
    terms_retrieved_at: str
    accepted_at: str
    source_of_rights_note: str = ""
    #: Per-checkbox engagement record (schema v2). Maps every
    #: required checkbox id the dialog rendered to the boolean the
    #: user submitted for that box. ``confirm_acceptance`` validates
    #: that every required id is True before writing the row, so a
    #: persisted row's map only ever contains ``True`` values today —
    #: but the *map* is the evidentiary artifact, not the values:
    #: it proves the user engaged with each specific claim, not just
    #: that the form parsed. Empty ``{}`` on legacy v1 rows.
    checkbox_responses: dict[str, bool] = field(default_factory=dict)
    #: Append-only event discriminator. ``"accept"`` is the original
    #: (and default, so every legacy row reads as an acceptance);
    #: ``"revoke"`` is a withdrawal that supersedes the prior acceptance
    #: for the same ``model_id``. The acceptance log is never rewritten —
    #: a withdrawal is a NEW row, so the full agree→withdraw history is
    #: preserved as a legal audit trail. "Currently accepted" is decided
    #: by the LATEST row's event (see :func:`has_accepted`).
    event: str = "accept"
    schema_version: int = SCHEMA_VERSION

    def to_json_line(self) -> str:
        """Serialise to a single JSONL line (no trailing newline)."""
        return json.dumps(
            {
                "model_id": self.model_id,
                "model_sha256": self.model_sha256,
                "ack_text_version": self.ack_text_version,
                "ack_text_sha256": self.ack_text_sha256,
                "use_context_text_version": self.use_context_text_version,
                "use_context_text_sha256": self.use_context_text_sha256,
                "use_context_at_acceptance": self.use_context_at_acceptance,
                "separate_rights_asserted": self.separate_rights_asserted,
                "terms_url": self.terms_url,
                "terms_permalink_url": self.terms_permalink_url,
                "terms_retrieved_at": self.terms_retrieved_at,
                "accepted_at": self.accepted_at,
                "source_of_rights_note": self.source_of_rights_note,
                "checkbox_responses": self.checkbox_responses,
                "event": self.event,
                "schema_version": self.schema_version,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> AcceptanceRow:
        """Build a row from a dict (e.g. parsed JSON). Tolerates
        missing optional fields with sensible defaults so older
        rows stay readable across schema versions.

        Legacy v1 rows (no ``checkbox_responses`` key) load with an
        empty map and keep their original ``schema_version`` so a
        future migration knows which rows still need the per-checkbox
        record back-filled from external evidence (if any)."""
        raw_responses = raw.get("checkbox_responses") or {}
        # Defensive coercion: a corrupted row that recorded the map
        # as a list or string shouldn't crash the reader.
        if isinstance(raw_responses, dict):
            checkbox_responses = {str(k): bool(v) for k, v in raw_responses.items()}
        else:
            checkbox_responses = {}
        return cls(
            model_id=raw["model_id"],
            model_sha256=raw["model_sha256"],
            ack_text_version=raw["ack_text_version"],
            ack_text_sha256=raw["ack_text_sha256"],
            use_context_text_version=raw["use_context_text_version"],
            use_context_text_sha256=raw["use_context_text_sha256"],
            use_context_at_acceptance=raw["use_context_at_acceptance"],
            separate_rights_asserted=bool(raw["separate_rights_asserted"]),
            terms_url=raw["terms_url"],
            terms_permalink_url=raw.get("terms_permalink_url", ""),
            terms_retrieved_at=raw["terms_retrieved_at"],
            accepted_at=raw["accepted_at"],
            source_of_rights_note=raw.get("source_of_rights_note", ""),
            checkbox_responses=checkbox_responses,
            event=str(raw.get("event", "accept")),
            schema_version=int(raw.get("schema_version", 1)),
        )


#: Owner-only file mode for the acceptance log. The log records
#: which restricted models the user accepted, their declared use
#: context, and free-text source-of-rights notes — sensitive
#: metadata that should never leak to other local users on a shared
#: machine. ``0o600`` = read/write for the owner; nothing for group
#: or world.
_LOG_FILE_MODE = 0o600

#: Owner-only directory mode for the parent ``XDG_CONFIG_HOME/bpp/``
#: directory. Prevents directory listing by other local users, which
#: would otherwise reveal that BPP is installed and that an
#: acceptance log exists.
_LOG_DIR_MODE = 0o700


def _harden_permissions(target: Path) -> None:
    """Tighten the acceptance-log file and parent directory to
    owner-only permissions. Called after every write so a pre-
    existing file from an older BPP version (created with the
    default umask) gets re-tightened on the next user action.

    POSIX-only effect: on Windows the mode bits don't map to ACL
    semantics, and ``os.chmod`` is a no-op for the read/write bits
    of interest. The privacy claim downgrades to "trust the user
    profile is appropriately ACL'd" on that platform; the call is
    safe to make regardless.
    """
    try:
        os.chmod(target, _LOG_FILE_MODE)
    except OSError as exc:
        _log.debug(
            "Could not tighten acceptance-log file mode on %s: %s",
            target,
            exc,
        )
    try:
        os.chmod(target.parent, _LOG_DIR_MODE)
    except OSError as exc:
        _log.debug(
            "Could not tighten acceptance-log dir mode on %s: %s",
            target.parent,
            exc,
        )


def append_row(row: AcceptanceRow, *, path: Path | None = None) -> None:
    """Append one row to the acceptance log.

    Creates the parent directory if needed. Atomic on POSIX in the
    common case: POSIX guarantees single-write append atomicity
    below the filesystem block size, well above one JSON row.

    File permissions are pinned to ``0o600`` and the parent directory
    to ``0o700`` — see :data:`_LOG_FILE_MODE`. We use ``os.open`` so
    the mode is set at creation time (the file's ``open`` would
    honour the process umask instead) and then re-chmod after the
    write so a pre-existing file from an older BPP install gets
    re-tightened.
    """
    target = path or get_acceptance_log_path()
    target.parent.mkdir(parents=True, exist_ok=True, mode=_LOG_DIR_MODE)
    fd = os.open(
        target,
        os.O_WRONLY | os.O_CREAT | os.O_APPEND,
        _LOG_FILE_MODE,
    )
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(row.to_json_line())
        f.write("\n")
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            # fsync can fail on some FUSE / network filesystems. The
            # write itself succeeded; the durability guarantee is
            # weaker but acceptable for an evidentiary record. Don't
            # raise — the user already saw the dialog and clicked.
            _log.debug("fsync after acceptance write failed; row still on disk")
    _harden_permissions(target)


def iter_rows(*, path: Path | None = None) -> Iterator[AcceptanceRow]:
    """Yield every row in the log, oldest first. Tolerates a
    truncated last line (power-loss mid-write) by skipping
    un-parseable trailing content with a warning."""
    target = path or get_acceptance_log_path()
    if not target.exists():
        return
    with target.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
                yield AcceptanceRow.from_dict(raw)
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                _log.warning(
                    "Skipping unparseable acceptance-log line %d (%s); file=%s",
                    line_no,
                    exc,
                    target,
                )


def find_latest_for_model(model_id: str, *, path: Path | None = None) -> AcceptanceRow | None:
    """Return the most-recent acceptance row for ``model_id``, or
    ``None`` if no row exists.

    "Most recent" means last row in append order (the log is
    append-only and writers stamp ``accepted_at`` in caller order).
    """
    latest: AcceptanceRow | None = None
    for row in iter_rows(path=path):
        if row.model_id == model_id:
            latest = row
    return latest


def has_accepted(model_id: str, *, path: Path | None = None) -> bool:
    """Return ``True`` if the model is CURRENTLY accepted — i.e. the
    most-recent row for ``model_id`` is an ``"accept"`` event, not a
    ``"revoke"`` withdrawal. A model the user accepted and then withdrew
    reads as not-accepted (the latest row wins)."""
    latest = find_latest_for_model(model_id, path=path)
    return latest is not None and latest.event == "accept"


def append_revocation(
    model_id: str, *, revoked_at: str | None = None, path: Path | None = None
) -> AcceptanceRow | None:
    """Record a withdrawal of a prior acceptance for ``model_id``.

    Append-only: the original acceptance row stays in the log (legal
    audit trail). A revocation copies the latest acceptance's identifying
    fields, flips ``event`` to ``"revoke"``, and stamps a fresh timestamp.
    Returns the written row, or ``None`` if there is nothing to revoke
    (no prior acceptance, or the latest row is already a revocation)."""
    latest = find_latest_for_model(model_id, path=path)
    if latest is None or latest.event != "accept":
        return None
    row = dataclasses.replace(
        latest,
        event="revoke",
        accepted_at=(revoked_at or datetime.datetime.now(datetime.UTC).isoformat()),
        schema_version=SCHEMA_VERSION,
    )
    append_row(row, path=path)
    return row


def list_acceptances(*, path: Path | None = None) -> list[AcceptanceRow]:
    """Return every row, oldest first. Settings will eventually
    render this as a read-only 'view your acceptances' panel."""
    return list(iter_rows(path=path))


# ── Test-only helpers ──


def _make_isolated_log_path() -> Path:
    """Return a unique tempdir-backed path for tests.

    Cooperates with :func:`get_acceptance_log_path` via the
    ``BPP_ACCEPTANCE_LOG_PATH`` env var: a test that wants
    isolation sets the env var to this path before calling any
    acceptance-log function.
    """
    tmp = Path(tempfile.mkdtemp(prefix="bpp-acceptance-log-test-"))
    return tmp / "model-acceptance.jsonl"


@dataclass
class _MutableTestPath:
    """Holder used by the test-fixture pattern: a fixture creates one,
    sets the env var, yields, and on teardown removes the dir. Keeps
    the production code free of test-specific globals."""

    path: Path = field(default_factory=_make_isolated_log_path)

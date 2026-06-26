"""Bring Your Own Model (BYOM) — user-supplied face-embedder files.

Batch 6 / item 11 of the legal-posture rollout. Commercial users
who do not want to run under Arkalogy's restricted-license picker
(or hobbyists who happen to have a licensed face model file)
register their own ONNX file here. BPP records the file path, a
SHA-256 of the contents at registration time, the
:class:`ModelEntry` shape that lets the rest of the registry-aware
code treat it uniformly, and the per-user acknowledgment that the
user is responsible for ensuring they have rights to the file.

Why BYOM is not a quality differentiator surfaced in marketing

The legal-posture spec's editorial rule (item 14) forbids naming
restricted models (AdaFace, buffalo_*) in commercial-targeted
copy. The BYOM docs and CLI help follow the same rule: they
describe the mechanism without naming any specific upstream the
user could "just download." That phrasing would recreate the
inducement risk Batch 4's click-through gate exists to close.

Why BYOM entries are commercial_use_restriction_known=False

Arkalogy is not in the BYOM rights chain. The user is asserting
they have rights to the file they supplied. The policy layer
(Batch 5) reads that flag and lets the load proceed without the
commercial-mode hard-block — the BYOM acknowledgment is the only
gate that fires.

Where the file lives

Same per-user config directory as the use-context store and the
acceptance log:
``${XDG_CONFIG_HOME:-~/.config}/bpp/byom-models.json``. JSON, not
JSONL — BYOM entries are current-state (a small bounded set of
files the user has registered), not an audit trail. The
acceptance row for each BYOM entry lives in the existing
``model-acceptance.jsonl`` so the evidentiary record stays in one
place.

Path can be overridden by ``BPP_BYOM_PATH`` for tests / dev.
"""

from __future__ import annotations

import contextlib
import datetime
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from bpp.registry.disclaimers import (
    BYOM_DISCLAIMER_VERSION,
    byom_disclaimer_sha256,
)
from bpp.registry.model_registry import (
    LicenseClass,
    ModelEntry,
    ModelStatus,
)
from bpp.utils.logging import get_logger

_log = get_logger(__name__)


SCHEMA_VERSION = 1
"""Integer schema version for the on-disk BYOM store."""


def get_byom_store_path() -> Path:
    """Return the path to the BYOM store JSON file.

    Honours ``BPP_BYOM_PATH`` for tests. Otherwise lives alongside
    the use-context.json and model-acceptance.jsonl files in the
    per-user config directory.
    """
    override = os.environ.get("BPP_BYOM_PATH")
    if override:
        return Path(override)
    config_home = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    return Path(config_home) / "bpp" / "byom-models.json"


@dataclass(frozen=True)
class BYOMEntry:
    """One user-registered model file.

    The fields match the bytes BPP writes to the BYOM store JSON.
    :func:`to_model_entry` projects this into a :class:`ModelEntry`
    so the rest of the registry-aware code (policy, picker, dialog)
    can treat BYOM entries uniformly.
    """

    id: str
    display_name: str
    kind: str
    file_path: str
    weight_sha256: str
    added_at: str
    ack_text_version: str
    ack_text_sha256: str

    def to_model_entry(self) -> ModelEntry:
        """Project the BYOM record into a :class:`ModelEntry`.

        Notes:

        * ``commercial_use_restriction_known=False`` and
          ``bppicker_commercial_default_allowed=True`` — Arkalogy is
          not in the rights chain. The policy layer treats BYOM
          entries as permissive for the commercial-mode gate.
        * ``requires_explicit_ack=True`` and
          ``ack_text_kind="byom"`` — the click-through dialog must
          still fire to record the user-responsibility
          acknowledgment, but it shows the BYOM disclaimer rather
          than the canonical restricted-model wording.
        * ``source_url=""`` and ``terms_url=""`` — there is no
          upstream URL to point at; the file is local.
        * ``status=AVAILABLE`` — the file exists on the user's disk,
          so the entry is loadable. A future maintenance pass that
          notices the file moved or was deleted can flip the entry's
          status to ``WITHDRAWN_NO_NEW_DOWNLOADS``.
        """
        return ModelEntry(
            id=self.id,
            display_name=self.display_name,
            kind=self.kind,
            source_url="",
            terms_url="",
            terms_permalink_url=None,
            terms_retrieved_at=self.added_at,
            license_summary=(f"Bring-your-own-model — user-supplied file at {self.file_path}"),
            requires_explicit_ack=True,
            ack_text_version=self.ack_text_version,
            ack_text_sha256=self.ack_text_sha256,
            upstream_claimed_license_class=LicenseClass.UNKNOWN,
            commercial_use_restriction_known=False,
            bppicker_commercial_default_allowed=True,
            commercial_unlock_requires_rights_assertion=False,
            status=ModelStatus.AVAILABLE,
            training_data="user-supplied (not declared)",
            weight_sha256=self.weight_sha256,
            default_for_kind=False,
            ack_text_kind="byom",
        )


@dataclass(frozen=True)
class _StoreContents:
    schema_version: int = SCHEMA_VERSION
    entries: tuple[BYOMEntry, ...] = field(default_factory=tuple)


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def _read_store(*, path: Path | None = None) -> _StoreContents:
    target = path or get_byom_store_path()
    if not target.exists():
        return _StoreContents()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _log.warning(
            "BYOM store at %s is unreadable (%s); treating as empty",
            target,
            exc,
        )
        return _StoreContents()
    entries: list[BYOMEntry] = []
    for raw_entry in raw.get("entries", []):
        try:
            entries.append(
                BYOMEntry(
                    id=raw_entry["id"],
                    display_name=raw_entry["display_name"],
                    kind=raw_entry["kind"],
                    file_path=raw_entry["file_path"],
                    weight_sha256=raw_entry["weight_sha256"],
                    added_at=raw_entry["added_at"],
                    ack_text_version=raw_entry["ack_text_version"],
                    ack_text_sha256=raw_entry["ack_text_sha256"],
                )
            )
        except KeyError as exc:
            _log.warning(
                "Skipping malformed BYOM entry (missing %s): %s",
                exc,
                raw_entry,
            )
    return _StoreContents(
        schema_version=int(raw.get("schema_version", SCHEMA_VERSION)),
        entries=tuple(entries),
    )


def _write_store(store: _StoreContents, *, path: Path | None = None) -> None:
    target = path or get_byom_store_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": store.schema_version,
        "entries": [
            {
                "id": e.id,
                "display_name": e.display_name,
                "kind": e.kind,
                "file_path": e.file_path,
                "weight_sha256": e.weight_sha256,
                "added_at": e.added_at,
                "ack_text_version": e.ack_text_version,
                "ack_text_sha256": e.ack_text_sha256,
            }
            for e in store.entries
        ],
    }
    fd, tmp_path = tempfile.mkstemp(prefix=".byom-models-", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.write("\n")
            f.flush()
            with contextlib.suppress(OSError):
                os.fsync(f.fileno())
        os.replace(tmp_path, target)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


def _compute_weight_sha256(file_path: Path) -> str:
    """Stream the file through SHA-256 in 1 MB chunks.

    Used at registration time to fingerprint the file the user
    selected. A future use that finds the bytes no longer match the
    recorded hash (file edited, replaced, moved) means the user is
    pointing at a different model and the acceptance row no longer
    applies. The policy layer can then re-prompt.
    """
    h = hashlib.sha256()
    with file_path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def add_byom_entry(
    *,
    display_name: str,
    kind: str,
    file_path: Path | str,
    path: Path | None = None,
) -> BYOMEntry:
    """Register a user-supplied model file.

    Computes the SHA-256 of the file at registration time. The
    returned :class:`BYOMEntry`'s ``id`` is derived from the hash
    so re-registering the same file (same bytes) produces the same
    id — the store deduplicates on id.

    The caller is responsible for completing the BYOM
    acknowledgment via :func:`bpp.registry.acceptance.confirm_acceptance`
    on the projected :class:`ModelEntry`. This function just lands
    the file in the store; the acceptance row gates the actual use.
    """
    fp = Path(file_path).expanduser().resolve()
    if not fp.is_file():
        raise FileNotFoundError(
            f"BYOM file not found: {fp}. The path must point to an existing local file."
        )
    weight_sha256 = _compute_weight_sha256(fp)
    entry_id = f"byom_{weight_sha256[:16]}"
    entry = BYOMEntry(
        id=entry_id,
        display_name=display_name.strip() or fp.name,
        kind=kind,
        file_path=str(fp),
        weight_sha256=weight_sha256,
        added_at=_utc_now_iso(),
        ack_text_version=BYOM_DISCLAIMER_VERSION,
        ack_text_sha256=byom_disclaimer_sha256(),
    )
    store = _read_store(path=path)
    # Deduplicate by id; same bytes → same entry.
    existing = [e for e in store.entries if e.id != entry_id]
    existing.append(entry)
    _write_store(
        _StoreContents(
            schema_version=SCHEMA_VERSION,
            entries=tuple(existing),
        ),
        path=path,
    )
    return entry


def remove_byom_entry(entry_id: str, *, path: Path | None = None) -> bool:
    """Remove a BYOM entry by id. Returns ``True`` if an entry was
    removed, ``False`` if the id was not registered.

    Does NOT delete the user's file from disk. BYOM is a pointer
    abstraction; removing the registry entry tells BPP to forget
    the file, not to delete it.
    """
    store = _read_store(path=path)
    remaining = [e for e in store.entries if e.id != entry_id]
    if len(remaining) == len(store.entries):
        return False
    _write_store(
        _StoreContents(
            schema_version=SCHEMA_VERSION,
            entries=tuple(remaining),
        ),
        path=path,
    )
    return True


def list_byom_entries(*, path: Path | None = None) -> list[BYOMEntry]:
    """Return every registered BYOM entry in insertion order."""
    return list(_read_store(path=path).entries)


def get_byom_entry(entry_id: str, *, path: Path | None = None) -> BYOMEntry | None:
    """Return the BYOM entry with ``entry_id`` or ``None``."""
    for entry in _read_store(path=path).entries:
        if entry.id == entry_id:
            return entry
    return None


def iter_byom_model_entries(*, path: Path | None = None) -> list[ModelEntry]:
    """Project every registered BYOM file into the :class:`ModelEntry`
    view the rest of the registry uses.

    The bp_model_registry blueprint and the CLI `bpp model list`
    surface call this so BYOM entries appear in the picker alongside
    the built-in and remote-registry entries.
    """
    return [e.to_model_entry() for e in list_byom_entries(path=path)]


# ── Test-only helpers ──


def _reset_for_tests(*, path: Path | None = None) -> None:
    """Delete the BYOM store file. Test-only."""
    target = path or get_byom_store_path()
    if target.exists():
        target.unlink()

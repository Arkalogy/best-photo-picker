"""Anti-rollback for the signed remote manifest.

Every published manifest carries a ``manifest_version`` (a positive
integer the maintainer raises on each publication; the verifier in
:mod:`bpp.registry.signed_manifest` refuses a manifest without one).
This module keeps, per registry URL, the highest version this machine
has applied and refuses anything lower.

Why
    Signatures prove who published a manifest, not that it is the
    current one. Without this check, anyone able to serve an older
    signed copy (a hijacked registry page, a stale mirror) could bring
    back the more permissive state that older manifest carried — a
    relaxation the maintainer later reverted, a model withdrawn since.

What it does not cover
    A refused manifest, like a failed fetch, leaves the bundled
    baseline, which can itself be more permissive than the latest
    manifest. Keeping the last applied manifest on disk and applying
    it in both cases is the follow-up; this module only stops a
    signed-but-stale manifest from being applied.

Where
    A small JSON file under :func:`bpp.utils.paths.cache_dir`
    (:data:`STATE_FILENAME`), one row per registry URL: the version,
    the manifest's ``generated_at`` and when it was applied. Keying by
    URL keeps a mock registry (``BPP_REMOTE_REGISTRY_URL``) and the
    real one from sharing a mark. A missing or damaged file reads as
    version 0, so the first versioned manifest always applies; a
    damaged file is set aside, not overwritten. Deleting the file
    resets every mark; that is the escape hatch when a registry's
    numbering is deliberately restarted.

Concurrency
    Every bpp process runs this at import (the server and each worker
    it spawns). The write is a read-modify-write of the whole file
    under a short-lived lock file, so two processes that fetched
    different versions at the same moment cannot leave the lower one.
    Equal versions are accepted (every startup refetches the same
    manifest), and the mark only moves up, so a write failure (read-
    only cache dir) is logged and otherwise ignored: the manifest
    still applies for this process.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from bpp.registry.acceptance import utc_now_iso
from bpp.utils.json_utils import load_for_rewrite, load_json_file
from bpp.utils.logging import get_logger
from bpp.utils.paths import cache_dir

_log = get_logger(__name__)

#: The high-water-mark file under ``cache_dir()``.
STATE_FILENAME = "registry-overlay-state.json"

#: A lock file older than this is from a process that died mid-write.
_LOCK_STALE_S = 10.0
_LOCK_WAIT_S = 2.0


def state_path() -> Path:
    return cache_dir() / STATE_FILENAME


def _version_of(row: Any) -> int:
    version = row.get("manifest_version") if isinstance(row, dict) else None
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        return 0
    return version


def _sources_of(state: Any) -> dict[str, Any]:
    sources = state.get("sources") if isinstance(state, dict) else None
    return dict(sources) if isinstance(sources, dict) else {}


def highest_applied_version(source_url: str) -> int:
    """The highest ``manifest_version`` applied from ``source_url``, or 0."""
    state = load_json_file(state_path(), {}, expected_type=dict, context="remote-registry state")
    return _version_of(_sources_of(state).get(source_url))


def rollback_reason(version: int, *, source_url: str) -> str | None:
    """``None`` when ``version`` may be applied, else why it is a rollback."""
    highest = highest_applied_version(source_url)
    if version >= highest:
        return None
    return (
        f"manifest_version {version} is older than the version {highest} already "
        f"applied from {source_url} on this machine; refusing the rollback (a replayed "
        f"older manifest could bring back a state the maintainer has since tightened). "
        f"Delete {state_path()} to reset the mark deliberately."
    )


@contextlib.contextmanager
def _locked(target: Path) -> Iterator[None]:
    """Hold ``<target>.lock`` (O_EXCL) for the read-modify-write. A lock
    older than :data:`_LOCK_STALE_S` is removed; if the lock cannot be
    won within :data:`_LOCK_WAIT_S` the write goes ahead anyway — the
    mark only moves up, so the worst case is the race this guards."""
    lock = target.with_name(target.name + ".lock")
    deadline = time.monotonic() + _LOCK_WAIT_S
    held = False
    while True:
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
            held = True
            break
        except FileExistsError:
            with contextlib.suppress(OSError):
                if time.time() - lock.stat().st_mtime > _LOCK_STALE_S:
                    lock.unlink()
                    continue
            if time.monotonic() >= deadline:
                _log.warning("Remote-registry state lock %s held too long; writing anyway", lock)
                break
            time.sleep(0.05)
    try:
        yield
    finally:
        if held:
            with contextlib.suppress(OSError):
                lock.unlink()


def record_applied_version(version: int, *, source_url: str, generated_at: str | None) -> None:
    """Raise the mark for ``source_url`` to ``version``; a lower or equal
    version leaves it alone. Other URLs' rows are kept."""
    if version <= highest_applied_version(source_url):
        return  # the common case: the same manifest as last start
    target = state_path()
    tmp_name = ""
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with _locked(target):
            # Re-read under the lock: another process may have raised
            # the mark since the check above.
            state = load_for_rewrite(
                target, {}, expected_type=dict, context="remote-registry state"
            )
            sources = _sources_of(state)
            if version <= _version_of(sources.get(source_url)):
                return
            sources[source_url] = {
                "manifest_version": version,
                "generated_at": generated_at,
                "applied_at": utc_now_iso(),
            }
            fd, tmp_name = tempfile.mkstemp(
                prefix=STATE_FILENAME + ".", suffix=".tmp", dir=target.parent
            )
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"sources": sources}, f, indent=2)
                f.write("\n")
            os.replace(tmp_name, target)
            tmp_name = ""
    except OSError as exc:
        _log.warning(
            "Could not record remote-registry manifest_version %d at %s: %s "
            "(the manifest still applies; the rollback check restarts from the "
            "last recorded version)",
            version,
            target,
            exc,
        )
        if tmp_name:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)

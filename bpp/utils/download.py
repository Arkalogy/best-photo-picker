"""Single-chokepoint model download helper.

Every model fetch in BPP MUST go through :func:`download_file`. The
function bundles four concerns that used to be scattered across each
loader:

1. **Legal policy gate** — looks up the entry in
   :mod:`bpp.registry` and calls
   :func:`bpp.registry.enforce_load_policy_for` before the network
   call. Permissive entries pass through silently; restricted /
   withdrawn / legally-blocked entries fail-closed with
   :class:`bpp.registry.policy.ModelLoadBlockedError`.

2. **Auto-downloader bypass** — opens the per-thread
   ``enter_registry_download`` / ``exit_registry_download`` window
   so any upstream library calling its own download path (currently
   ``insightface.utils.storage.download``) inherits the same
   chokepoint context. Without this, a fetch initiated through
   ``download_file`` would be silently blocked by its own
   sibling-patch protection if the underlying library tried a
   nested download.

3. **Integrity verification** — SHA-256 hash check after fetch.
   Defends against MITM, compromised CDNs, and upstream tampering.
   Every model we run does ONNX/native inference — a tampered
   binary is RCE on import.

4. **Timeout safety** — 120 s connect/read deadline so a flaky
   network can't hang the app indefinitely.

The ``registry_id`` keyword argument is REQUIRED with no default.
Callers must either name a registered legal entry or pass ``None``
to explicitly mark the download as "ancillary, no licensing
concern" (logs a warning so the gap is visible to auditors). This
forces every new download path to confront the gate question
rather than silently bypassing it.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import shutil
import urllib.request

_DOWNLOAD_TIMEOUT = 120  # seconds
log = logging.getLogger(__name__)


def _file_sha256(path: str) -> str:
    """Stream-hash a file. Reads in 1 MB chunks to bound RAM."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _run_policy_gate(registry_id: str | None) -> None:
    """Run the per-entry policy gate.

    ``registry_id`` of ``None`` is an explicit opt-out (ancillary
    detector with no legal-registry entry). Logs a warning so the
    gap is visible. A string id is looked up; unknown ids raise
    ``ValueError`` (programmer error — register the entry first).
    """
    if registry_id is None:
        log.warning(
            "download_file: registry_id=None — ancillary download "
            "with no policy gate. If this model has any licensing "
            "concern, register it in bpp/registry/builtins.py."
        )
        return

    # Late import: bpp.registry pulls in policy machinery; importing
    # it at module load would create a circular path through
    # bpp.scoring.model_base ← bpp.utils.download.
    from bpp.registry import enforce_load_policy_for
    from bpp.registry.model_registry import get_entry

    if get_entry(registry_id) is None:
        raise ValueError(
            f"download_file: unknown registry_id {registry_id!r}. "
            "Every model download must reference a registered "
            "legal entry. Add the entry in "
            "bpp/registry/builtins.py first."
        )
    enforce_load_policy_for(registry_id)


def download_file(
    url: str,
    dest: str,
    *,
    registry_id: str | None,
    timeout: int = _DOWNLOAD_TIMEOUT,
    sha256: str | None = None,
) -> None:
    """Download *url* to *dest* through the canonical gate.

    Steps in order:

    1. Policy gate — :func:`_run_policy_gate` raises if the entry's
       status / acceptance / use-context disallows the download.
    2. Open the registry-download bypass window so any nested
       upstream auto-downloader called from this thread inherits the
       same context (see
       :mod:`bpp.registry.download_chokepoint`).
    3. Fetch the bytes (``urllib.urlopen`` with ``timeout`` — chosen
       over ``urlretrieve`` precisely so this timeout is enforced).
    4. Close the bypass window.
    5. If ``sha256`` is provided, verify; on mismatch delete the
       file and raise :class:`~bpp.scoring.model_base.ModelIntegrityError`.

    Removes the dest file on any failure (network, integrity) so a
    partial / corrupt / unverified file never lingers in the cache.

    ``registry_id`` is keyword-only and REQUIRED — pass either a
    registered entry id or ``None`` (explicit opt-out for an
    ancillary detector). Callers cannot omit this argument; the
    point of the chokepoint is that every download confronts the
    gate question explicitly.
    """
    _run_policy_gate(registry_id)

    # Late import for the same circular-import reason as in
    # _run_policy_gate above.
    from bpp.registry.download_chokepoint import (
        enter_registry_download,
        exit_registry_download,
    )

    # Start/end logging at the chokepoint itself — a model download can
    # take a minute on a slow network, and server.log must show whether
    # it started, finished, or died without relying on each caller.
    log.info("Downloading %s -> %s", url, dest)
    enter_registry_download()
    try:
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp, open(dest, "wb") as f:
                shutil.copyfileobj(resp, f)
        except Exception:
            log.warning("Download failed for %s", url, exc_info=True)
            with contextlib.suppress(OSError):
                os.remove(dest)
            raise
    finally:
        exit_registry_download()

    if sha256 is not None:
        expected = sha256.lower().strip()
        actual = _file_sha256(dest)
        if actual != expected:
            with contextlib.suppress(OSError):
                os.remove(dest)
            from bpp.scoring.model_base import ModelIntegrityError

            raise ModelIntegrityError(
                f"SHA-256 mismatch for {url}: "
                f"expected {expected[:16]}…, got {actual[:16]}… "
                f"(file deleted; refusing to use unverified bytes)"
            )

    try:
        size_mb = os.path.getsize(dest) / 1_000_000
        log.info("Downloaded %s (%.1f MB, sha %s)", dest, size_mb, "verified" if sha256 else "n/a")
    except OSError:
        log.info("Downloaded %s", dest)


def verify_existing(path: str, *, sha256: str) -> None:
    """Verify an already-on-disk model file matches the pinned SHA-256.

    Use this before loading a cached model — the download path already
    verifies on first fetch, but a cached file could have been
    tampered with between sessions (compromised cache directory,
    cloud-sync replacing the file, malicious bind mount on Docker
    setups, etc.).

    Raises ModelIntegrityError on mismatch. Removes the bad file so
    the next caller can re-download cleanly.

    D-02: ModelSingleton.ensure_model previously returned existing
    files unconditionally — only the first download was verified.
    This helper closes that gap.
    """
    actual = _file_sha256(path)
    expected = sha256.lower().strip()
    if actual != expected:
        with contextlib.suppress(OSError):
            os.remove(path)
        from bpp.scoring.model_base import ModelIntegrityError

        raise ModelIntegrityError(
            f"SHA-256 mismatch for cached file {path}: "
            f"expected {expected[:16]}…, got {actual[:16]}… "
            f"(file deleted; refusing to load tampered bytes)"
        )

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

The ``registry_id`` keyword argument is REQUIRED with no default and
must name a registered legal entry — every model BPP downloads has
one, supporting models included. There is no "ancillary, ungated"
opt-out: a download with no entry is refused before any network call.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import threading
import urllib.request
from collections.abc import Callable, Iterator

_DOWNLOAD_TIMEOUT = 120  # seconds
_CHUNK = 256 * 1024
log = logging.getLogger(__name__)

#: Per-thread progress callback ``(bytes_done, bytes_total | None)`` set by
#: :func:`report_progress`. Thread-local so a background download job sees
#: the bytes of the downloads it triggers — including ones made deep inside
#: a model's own ensure helper — without every helper growing a parameter.
_progress = threading.local()


@contextlib.contextmanager
def report_progress(
    callback: Callable[[int, int | None], None],
    *,
    on_phase: Callable[[str], None] | None = None,
) -> Iterator[None]:
    """Report bytes for every :func:`download_file` call on this thread,
    and (``on_phase``) where it is: ``downloading`` when the fetch
    starts, ``finishing`` while the file is verified — repeated per
    hashed chunk, as a sign of life for a long verification."""
    previous = getattr(_progress, "callback", None)
    previous_phase = getattr(_progress, "on_phase", None)
    _progress.callback = callback
    _progress.on_phase = on_phase
    try:
        yield
    finally:
        _progress.callback = previous
        _progress.on_phase = previous_phase


def _report_phase(name: str) -> None:
    on_phase = getattr(_progress, "on_phase", None)
    if on_phase is not None:
        on_phase(name)


#: Per-thread "a download is starting" callback set by
#: :func:`announce_downloads`, so a long-running job can show a status
#: line for downloads made deep inside a model loader.
_announcer = threading.local()


@contextlib.contextmanager
def announce_downloads(callback: Callable[[str], None]) -> Iterator[None]:
    """Show a status line (``callback(message)``) for every model
    download started on this thread."""
    previous = getattr(_announcer, "callback", None)
    _announcer.callback = callback
    try:
        yield
    finally:
        _announcer.callback = previous


def announce(message: str) -> None:
    """Pass a download status line to the active announcer, if any."""
    callback = getattr(_announcer, "callback", None)
    if callback is not None:
        callback(message)


def plain_reason(exc: BaseException) -> str:
    """A download failure in words a user can act on. Never the raw
    text of a file error: it carries local paths, and these reasons
    reach the UI (and paired devices)."""
    import http.client
    import socket
    import ssl

    if isinstance(exc, urllib.error.HTTPError):
        return f"the download site returned HTTP {exc.code}"
    cause = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(cause, TimeoutError | socket.timeout):
        return "the download timed out"
    if isinstance(cause, ssl.SSLCertVerificationError):
        return "the download site's security certificate couldn't be verified"
    if isinstance(cause, ssl.SSLError):
        return "a secure connection to the download site failed"
    if isinstance(cause, ConnectionRefusedError) or isinstance(exc, urllib.error.URLError):
        return "no internet connection, or the download site is unreachable"
    if isinstance(cause, ConnectionError | http.client.IncompleteRead):
        return "the connection dropped"
    if isinstance(exc, OSError):
        return (
            "a file couldn't be written or deleted — check free disk space and folder permissions"
        )
    return str(exc) or type(exc).__name__


class DownloadSkippedError(RuntimeError):
    """This model's download already failed earlier in the same run."""


#: Registry ids whose download failed earlier in this analysis run. An
#: environment variable, so subprocesses (and their worker pools) inherit
#: it and don't silently try the same download again.
_SKIP_ENV = "BPP_SKIP_DOWNLOADS"


def skip_downloads_in_this_process(registry_ids: list[str]) -> None:
    """Refuse further downloads of *registry_ids* in this process and the
    processes it starts. Call it only in a run's own subprocess."""
    if registry_ids:
        os.environ[_SKIP_ENV] = ",".join(sorted(_skipped_downloads() | set(registry_ids)))


def _skipped_downloads() -> set[str]:
    return {i for i in os.environ.get(_SKIP_ENV, "").split(",") if i}


def _copy_with_progress(resp, f) -> None:
    callback = getattr(_progress, "callback", None)
    total_header = getattr(resp, "headers", {}).get("Content-Length")
    total = int(total_header) if total_header and str(total_header).isdigit() else None
    done = 0
    while chunk := resp.read(_CHUNK):
        f.write(chunk)
        done += len(chunk)
        if callback is not None:
            callback(done, total)


def _file_sha256(path: str, *, beat: bool = False) -> str:
    """Stream-hash a file. Reads in 1 MB chunks to bound RAM. With
    ``beat``, each chunk is a sign of life for a job waiting on the
    verification of what it just downloaded."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
            if beat:
                _report_phase("finishing")
    return h.hexdigest()


def _run_policy_gate(registry_id: str) -> None:
    """Run the per-entry policy gate.

    A missing or unknown id raises ``ValueError`` (programmer error —
    register the entry first); there is no ungated download.
    """
    if not registry_id:
        raise ValueError(
            "download_file: registry_id is required. Every model download "
            "must name its bpp.registry entry — register one in "
            "bpp/registry/builtins.py first."
        )

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
    registry_id: str,
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

    ``registry_id`` is keyword-only and REQUIRED — the registered entry
    for the weights. The point of the chokepoint is that every download
    runs the gate.
    """
    _run_policy_gate(registry_id)
    if registry_id in _skipped_downloads():
        raise DownloadSkippedError(f"{registry_id}: its download failed earlier in this run")

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
    _report_phase("downloading")
    enter_registry_download()
    try:
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp, open(dest, "wb") as f:
                _copy_with_progress(resp, f)
        except Exception:
            log.warning("Download failed for %s", url, exc_info=True)
            with contextlib.suppress(OSError):
                os.remove(dest)
            raise
    finally:
        exit_registry_download()

    if sha256 is not None:
        _report_phase("finishing")
        expected = sha256.lower().strip()
        actual = _file_sha256(dest, beat=True)
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


#: A temp file untouched this long belongs to a crashed run, not to a
#: download in progress (an active one is written to every few seconds).
_STALE_TEMP_SECONDS = 3600


def _partial_download_paths(dest: str) -> list[str]:
    import glob

    return [dest + ".tmp", *glob.glob(glob.escape(dest) + ".*.tmp")]


def download_temp_path(dest: str) -> str:
    """Temp file for one writer's download of ``dest``.

    Unique per process and thread, so parallel workers fetching the same
    model never write into one shared file. Also deletes this file's
    temp leftovers from crashed runs (but not another writer's
    in-progress download).
    """
    import time

    cutoff = time.time() - _STALE_TEMP_SECONDS
    for leftover in _partial_download_paths(dest):
        with contextlib.suppress(OSError):
            if os.path.getmtime(leftover) < cutoff:
                os.remove(leftover)
    return f"{dest}.{os.getpid()}.{threading.get_ident()}.tmp"


def remove_partial_downloads(dest: str) -> None:
    """Delete every temp leftover of downloads of ``dest`` (uninstall)."""
    for leftover in _partial_download_paths(dest):
        with contextlib.suppress(OSError):
            os.remove(leftover)


def verify_existing(path: str, *, sha256: str) -> None:
    """Verify an already-on-disk model file matches the pinned SHA-256.

    Use this before loading a cached model — the download path already
    verifies on first fetch, but a cached file could have been
    tampered with between sessions (compromised cache directory,
    cloud-sync replacing the file, malicious bind mount on Docker
    setups, etc.).

    Raises ModelIntegrityError on mismatch. Removes the bad file so
    the next caller can re-download cleanly.

    ModelSingleton.ensure_model previously returned existing
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

"""Path safety + cache directory utilities."""

from __future__ import annotations

import os
from pathlib import Path


def safe_join(base: str, filename: str) -> str:
    """Safely join base and filename, preventing path traversal."""
    # Strip any directory components from filename
    clean_name = os.path.basename(filename)
    result = os.path.join(base, clean_name)
    # Verify the result is within base
    real_base = os.path.realpath(base)
    real_result = os.path.realpath(result)
    if not real_result.startswith(real_base + os.sep) and real_result != real_base:
        raise ValueError(f"Path traversal detected: {filename}")
    return result


def is_safe_archive_member(member_name: str, extract_dir: str) -> bool:
    """Return True if extracting `member_name` lands inside `extract_dir`.

    Used by zip / tar extractors to reject malicious entries with
    `..`, absolute paths, or symlinks-via-name that would escape the
    target tree. Unlike `safe_join`, this preserves intra-archive
    subdirectories — `photos/2024/img.jpg` is allowed; `../escape.jpg`
    or `/etc/passwd` is not.

    The check uses `os.path.realpath` on the resolved target so that
    symlink components anywhere in `extract_dir`'s path are followed
    consistently with the comparison base. Returns False (rather than
    raising) so callers can attach domain-specific error reporting
    — analyze_worker emits an SSE error event, restore-backup logs
    and returns rc=2, etc.

    NOTE: this checks NAMES only. Tar members can also have type bits
    that are dangerous regardless of name (symlink, hardlink, device
    nodes). Use `is_safe_tar_member` for the full check on tar.
    """
    # Unpaired surrogates, NUL bytes, and other unencodable inputs crash
    # os.path.* on macOS — adversarial archives can include any of these.
    # Reject them outright; they're never legitimate filenames anyway.
    try:
        real_extract = os.path.realpath(extract_dir)
        target = os.path.realpath(os.path.join(extract_dir, member_name))
    except (UnicodeEncodeError, ValueError, OSError):
        return False
    return target == real_extract or target.startswith(real_extract + os.sep)


def is_safe_tar_member(member, extract_dir: str) -> bool:
    """Return True if a tarfile.TarInfo entry is safe to extract.

    Combines the name-based path-containment check (via
    `is_safe_archive_member`) with type filtering: only regular
    files and directories are allowed. Symlinks, hardlinks, device
    nodes, character devices, and FIFOs are rejected outright —
    Python 3.12 added tarfile filters that do this automatically,
    but on 3.11 we ship our own equivalent.

    Why type filtering matters: a tar entry named `safe.jpg` whose
    type is "symlink to /etc/passwd" passes the path check (the
    name lands inside extract_dir) but creates a symlink that, if
    followed by anything later in the pipeline, leaks the target
    or worse. Hardlinks similarly let an archive plant a reference
    to any file on disk. Device nodes are absurd as photo-import
    payload but a malicious one could DoS or escalate via /dev.

    Caller (analyze_worker on Py 3.11) uses this in place of the
    pure name check before extracting each member.
    """
    if not is_safe_archive_member(member.name, extract_dir):
        return False
    # Allow only regular files and directories. tarfile exposes
    # `isfile()` (REGTYPE / AREGTYPE), `isdir()` (DIRTYPE), and
    # `isreg()` (alias for isfile). Everything else — issym, islnk,
    # ischr, isblk, isfifo, isdev — is rejected.
    return bool(member.isfile() or member.isdir())


def cache_dir() -> Path:
    """Resolve the bpp cache root.

    Order of precedence:
    1. ``BPP_CACHE_DIR`` env var — explicit override (Docker / NAS).
    2. ``XDG_CACHE_HOME/bpp`` — XDG Base Directory spec.
    3. ``~/.cache/bpp`` — default.

    Used as the parent for downloaded ML weights, CLIP embeddings,
    and tokenizer vocab files.
    """
    # Every branch MUST expanduser(): a literal "~" that survives into a
    # registered model path reaches open()/makedirs() unexpanded and the
    # download writes to a bogus "~" directory (Errno 2). The XDG branch
    # previously skipped it — e.g. XDG_CACHE_HOME="~/.cache" broke
    # Redownload while the loader (which expanded elsewhere) still worked.
    explicit = os.environ.get("BPP_CACHE_DIR")
    if explicit:
        return Path(explicit).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return (Path(xdg) / "bpp").expanduser()
    return Path.home() / ".cache" / "bpp"


def models_dir() -> Path:
    """Resolve the directory where bpp downloads ML model weights.

    Order of precedence:
    1. ``BPP_MODELS_DIR`` env var — explicit override.
    2. Otherwise: ``cache_dir() / "models"``.

    The directory is NOT auto-created — callers create it on demand
    with `os.makedirs(..., exist_ok=True)` when downloading.
    """
    explicit = os.environ.get("BPP_MODELS_DIR")
    if explicit:
        return Path(explicit).expanduser()
    return cache_dir() / "models"

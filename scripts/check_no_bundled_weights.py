#!/usr/bin/env python3
"""Fail the build if any ML model weight file ships in an Arkalogy artifact.

Batch 2 (item 2) of the legal-posture rollout. Implements the
"zero restricted weights in any Arkalogy-controlled artifact" rule
from the legal-posture spec — even though we don't currently bundle any
restricted weights, the guard makes the policy enforceable rather
than convention. A future PR that adds a restricted weight file
(even accidentally) is caught by CI before merge.

What it scans
-------------

By default the script scans the ``bpp/`` source tree of the current
repo. ``--scan-wheel <path>`` extracts a built wheel (or zip / sdist)
to a tempdir and scans the extracted contents instead — this is the
form CI uses on the artifact built by ``python -m build``.

What counts as a weight file
----------------------------

Either:

* A file whose extension is one of ``WEIGHT_EXTENSIONS`` (the
  formats ML models ship in: ``.onnx``, ``.pt``, ``.ckpt``,
  ``.dat``, ``.h5``, ``.pkl``, ``.pickle``, ``.bin``, ``.safetensors``).
* A file whose name matches one of ``RESTRICTED_BLOCKLIST`` —
  specific known-restricted weight bundles like AdaFace checkpoints
  or InsightFace buffalo zips. The blocklist catches these even when
  they arrive under unusual extensions.

Exit codes
----------

* ``0`` — no offenders found.
* ``1`` — at least one offender; printed to stderr with full paths
  and reason. CI treats non-zero as a release blocker.

Allowlist semantics
-------------------

If a file looks like a weight but is genuinely a non-model binary
(e.g. an icon or font we happen to ship with a ``.bin`` extension),
add its path relative to the scan root to ``ALLOWLIST``. Each entry
documents why it's exempt. The allowlist is intentionally tiny;
growing it freely defeats the guard.

Manual invocation
-----------------

    # Scan the repo source tree (default).
    python3 scripts/check_no_bundled_weights.py

    # Scan a built wheel.
    python -m build --wheel
    python3 scripts/check_no_bundled_weights.py --scan-wheel dist/bppicker-*.whl

    # Scan a sdist tarball.
    python -m build --sdist
    python3 scripts/check_no_bundled_weights.py --scan-wheel dist/bppicker-*.tar.gz
"""

from __future__ import annotations

import argparse
import sys
import tarfile
import tempfile
import zipfile
from collections.abc import Iterator
from pathlib import Path

# Extensions that are almost always ML model weights. Any file with
# one of these extensions in a shipped artifact is treated as an
# offender unless explicitly allowlisted.
WEIGHT_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".onnx",
        ".pt",
        ".pth",
        ".ckpt",
        ".dat",
        ".h5",
        ".hdf5",
        ".pkl",
        ".pickle",
        ".bin",
        ".safetensors",
        ".tflite",
        ".pb",
    }
)

# Known-restricted bundle filenames or filename prefixes. Caught even
# under uncommon extensions because they are the specific things we
# never want to ship under any circumstances. Each entry pairs the
# match pattern with a short rationale that's surfaced when the guard
# fires so the build log explains *why* the file is blocked.
RESTRICTED_BLOCKLIST: tuple[tuple[str, str], ...] = (
    (
        "adaface_",
        "AdaFace pretrained checkpoint — trained-on data carries "
        "non-commercial / research-only restrictions per upstream",
    ),
    (
        "buffalo_s",
        "InsightFace buffalo_s bundle — research / non-commercial use only",
    ),
    (
        "buffalo_m",
        "InsightFace buffalo_m bundle — research / non-commercial use only",
    ),
    (
        "buffalo_l",
        "InsightFace buffalo_l bundle — research / non-commercial use only",
    ),
    (
        "antelopev2",
        "InsightFace antelopev2 bundle — research / non-commercial use only",
    ),
    (
        "w600k_",
        "InsightFace recognition checkpoint (WebFace600K-trained) — "
        "research / non-commercial use only",
    ),
)

# Paths (relative to the scan root) that look like weight files but
# are intentionally shipped. Each entry must come with a one-line
# rationale that future maintainers can audit. Intentionally tiny —
# growing it defeats the guard's purpose.
#
# Both keys are included because the scan root differs by mode: a
# source-tree scan rooted at ``bpp/`` sees the bare path; a wheel scan
# sees the path prefixed with the package directory. A future
# allowlist entry should include both forms.
ALLOWLIST: dict[str, str] = {
    "scoring/models/blaze_face_short_range.tflite": (
        "MediaPipe BlazeFace short-range face detector — Apache 2.0 "
        "(Google MediaPipe Solutions). Bundled as a fallback face "
        "detector. Permissive license, no known commercial-use "
        "restriction. Source: "
        "https://developers.google.com/mediapipe/solutions/vision/face_detector"
    ),
    "bpp/scoring/models/blaze_face_short_range.tflite": (
        "MediaPipe BlazeFace short-range face detector — Apache 2.0 "
        "(Google MediaPipe Solutions). Bundled as a fallback face "
        "detector. Permissive license, no known commercial-use "
        "restriction. Source: "
        "https://developers.google.com/mediapipe/solutions/vision/face_detector"
    ),
}


def _iter_files(root: Path) -> Iterator[Path]:
    """Yield every file beneath ``root`` recursively. Skips directories
    that are not part of any shipped artifact (``.git``, ``__pycache__``,
    ``.venv``, ``node_modules``) so the scan stays fast on dev trees."""
    skip_dirs: frozenset[str] = frozenset(
        {".git", "__pycache__", ".venv", "venv", "node_modules", "dist", "build"}
    )
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        parts = path.relative_to(root).parts
        if any(p in skip_dirs for p in parts):
            continue
        yield path


def _classify(path: Path, root: Path) -> tuple[bool, str]:
    """Decide whether ``path`` is an offender.

    Returns ``(is_offender, reason)``. ``reason`` is empty when the
    file is not an offender, and a human-readable explanation when
    it is.
    """
    rel = str(path.relative_to(root))
    name_lower = path.name.lower()

    # Restricted blocklist beats everything: if a file matches the
    # blocklist we treat it as an offender even if it's somehow on
    # the allowlist. There is no legitimate reason to ship one of
    # these as a payload file — including a maintainer who renamed
    # it to a benign extension to sneak it past CI.
    #
    # Source-code modules whose name LEGITIMATELY references the
    # model they load (a Python loader file like
    # ``face_embed_buffalo_s.py``) are exempt — those are .py / .md
    # files, not the weight bundle. Adding a new exempt extension
    # is a deliberate maintainer choice; .py and .md cover the
    # documented surface (Python loader modules, ADRs, docstrings
    # describing the model).
    BLOCKLIST_EXEMPT_EXTENSIONS = {".py", ".md", ".rst"}
    if path.suffix.lower() not in BLOCKLIST_EXEMPT_EXTENSIONS:
        for needle, rationale in RESTRICTED_BLOCKLIST:
            if needle.lower() in name_lower:
                return True, f"blocklisted: {rationale} (matched on '{needle}')"

    # Allowlist short-circuits the extension check for known non-weight
    # binaries that happen to share a weight extension.
    if rel in ALLOWLIST:
        return False, ""

    suffix = path.suffix.lower()
    if suffix in WEIGHT_EXTENSIONS:
        return True, (
            f"file has a weight-like extension ({suffix}) and is not on "
            "the allowlist; if this is a genuine non-model file, add it "
            f"to ALLOWLIST in {Path(__file__).name} with a one-line "
            "rationale, otherwise this artifact bundles a model weight "
            "that should not ship."
        )

    return False, ""


def _scan_directory(root: Path) -> list[tuple[Path, str]]:
    """Walk ``root`` and return a list of ``(path, reason)`` offenders.

    Empty list means a clean scan. Sorted for deterministic output so
    CI failure messages match across runs.
    """
    offenders: list[tuple[Path, str]] = []
    for path in _iter_files(root):
        is_offender, reason = _classify(path, root)
        if is_offender:
            offenders.append((path, reason))
    offenders.sort(key=lambda pair: str(pair[0]))
    return offenders


def _extract_archive(archive_path: Path, dest: Path) -> None:
    """Unpack a wheel / zip / tarball into ``dest``. Best-effort:
    raises if the archive type isn't recognised."""
    name_lower = archive_path.name.lower()
    if name_lower.endswith(".whl") or name_lower.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as zf:
            zf.extractall(dest)
        return
    if name_lower.endswith(".tar.gz") or name_lower.endswith(".tgz") or name_lower.endswith(".tar"):
        with tarfile.open(archive_path) as tf:
            tf.extractall(dest, filter="data")
        return
    raise ValueError(
        f"Unsupported artifact archive type: {archive_path!r}. Expected "
        ".whl, .zip, .tar.gz, .tgz, or .tar."
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fail the build if any ML model weight file ships in an Arkalogy "
            "artifact (Batch 2 / item 2 of the legal-posture rollout)."
        ),
    )
    parser.add_argument(
        "--scan-wheel",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Path to a built wheel, sdist tarball, or zip. The script "
            "unpacks it to a tempdir and scans the contents. When omitted, "
            "the repo's bpp/ source tree is scanned instead."
        ),
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        metavar="PATH",
        help="Repo root used as the scan target when --scan-wheel is omitted.",
    )
    args = parser.parse_args()

    if args.scan_wheel is not None:
        archive_path = args.scan_wheel.resolve()
        if not archive_path.is_file():
            sys.stderr.write(f"check_no_bundled_weights: artifact not found: {archive_path}\n")
            return 1
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            try:
                _extract_archive(archive_path, tmp_root)
            except Exception as exc:
                sys.stderr.write(
                    f"check_no_bundled_weights: failed to extract {archive_path}: {exc}\n"
                )
                return 1
            offenders = _scan_directory(tmp_root)
            scan_label = f"artifact {archive_path.name}"
    else:
        scan_root = (args.repo_root / "bpp").resolve()
        if not scan_root.is_dir():
            sys.stderr.write(f"check_no_bundled_weights: scan root not found: {scan_root}\n")
            return 1
        offenders = _scan_directory(scan_root)
        scan_label = f"repo source tree at {scan_root}"

    if not offenders:
        sys.stdout.write(
            f"check_no_bundled_weights: clean scan of {scan_label} — no model weight files found.\n"
        )
        return 0

    sys.stderr.write(
        f"check_no_bundled_weights: FAILED on {scan_label} ({len(offenders)} offender(s) found):\n"
    )
    for path, reason in offenders:
        sys.stderr.write(f"  - {path}: {reason}\n")
    sys.stderr.write(
        "\n"
        "Arkalogy must not ship model weight files in any release "
        "artifact (Batch 2 / item 2 of the legal-posture plan). If one "
        "of these files is a genuine non-model binary, add it to the "
        "ALLOWLIST in scripts/check_no_bundled_weights.py with a "
        "one-line rationale. Otherwise, remove it from the artifact.\n"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())


# Re-exports for the test suite (tests import the helpers directly so
# they don't have to shell out to subprocess for every assertion).
__all__ = [
    "ALLOWLIST",
    "RESTRICTED_BLOCKLIST",
    "WEIGHT_EXTENSIONS",
    "_classify",
    "_scan_directory",
    "main",
]

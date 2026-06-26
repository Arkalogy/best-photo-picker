"""Regression: every model URL/SHA constant cluster has a header
comment block above it.

Why this exists: the scoring modules (CLIP, BlazeFace, YuNet, SFace,
SCRFD, NudeNet, YOLOv11, MediaPipe pose / segmenter / hand /
landmarker) declare hardcoded `_*_MODEL_URL`, `_*_MODEL_SHA256`,
and similar constants at module scope. These constants are the
*only* canonical record of what specific model build the codebase
pins. A future contributor wanting to bump CLIP from ViT-B/32 to
ViT-L/14 — or to debug why a model URL 404s — would otherwise have
to reverse-engineer the provenance from the URL alone.

This test scans every `bpp/scoring/*.py` file for "model constant
clusters" (any module-level `_*_URL` or `_*_SHA256` line) and
asserts that a `# ── Model:` header comment sits within ~20 lines
above the cluster. The header itself doesn't need to follow a
strict grammar — we just want a docstring-style block that
explains what / where / why / license / how to bump.

If a contributor adds a new ML model, this test fires until they
add the header. If a contributor restructures the file and pushes
the header too far away, this test catches that too.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCORING = REPO_ROOT / "bpp" / "scoring"

# Match module-scope (no leading whitespace) constants that look like
# model download pointers. Narrowed to `_*_URL` and `_*_SHA256` lines
# only — these are the download identity. PATH/DIR/FILENAME constants
# are derived from these and don't carry independent provenance, so
# we don't need a header for each of them.
_MODEL_CONST_RE = re.compile(
    r"""^_?[A-Z_]*(?:URL|SHA256)\s*=\s*""",
    re.MULTILINE,
)

# A "header block" is at minimum a `# ── Model:` (or `# ── Vocabulary:`
# for clip_tokenizer) marker. The unicode em-dash characters give the
# block a distinctive visual ruler that's easy to spot when scrolling
# through a long scoring file.
_HEADER_LINE_RE = re.compile(r"^#\s*──\s*(?:Model|Vocabulary):\s+", re.MULTILINE)


def _scoring_files_with_model_constants() -> list[Path]:
    """Return every scoring/*.py file that defines at least one
    model artifact constant."""
    files: list[Path] = []
    for py in sorted(SCORING.glob("*.py")):
        text = py.read_text(encoding="utf-8")
        if _MODEL_CONST_RE.search(text):
            files.append(py)
    return files


def test_each_model_constant_cluster_has_a_header():
    """For each scoring module that defines model constants, find the
    first occurrence of a `_*_URL`/`_*_SHA256`/`_*_FILENAME` line and
    assert a `# ── Model:` (or Vocabulary:) header sits within 25
    lines above it."""
    offenders: list[str] = []
    for py in _scoring_files_with_model_constants():
        text = py.read_text(encoding="utf-8")
        lines = text.splitlines()

        # Find the first model-constant line (1-indexed)
        first_const_line: int | None = None
        for i, line in enumerate(lines, start=1):
            if _MODEL_CONST_RE.match(line):
                first_const_line = i
                break
        if first_const_line is None:
            continue  # paranoia; the file made it into the list

        # Look for a header block in the 25 lines above. 25 leaves
        # room for a ~10-line docstring + the actual file header
        # imports above the constants.
        window_start = max(0, first_const_line - 25)
        window = "\n".join(lines[window_start:first_const_line])
        if not _HEADER_LINE_RE.search(window):
            offenders.append(
                f"{py.relative_to(REPO_ROOT)}:{first_const_line} "
                f"— first model constant has no `# ── Model: …` "
                "header within the 25 lines above it"
            )

    assert not offenders, (
        f"Found {len(offenders)} scoring module(s) with model "
        "constants but no documenting header block. Add a `# ── Model: "
        "<name>` block above the first `_*_URL` declaration with: "
        "what (one line), where (source repo), why this build/variant, "
        "license, and how-to-bump notes. See bpp/scoring/clip_embed.py "
        "for the canonical shape.\n\n" + "\n".join(f"  - {o}" for o in offenders)
    )


def test_known_modules_present_in_scan():
    """Sanity floor: at least these 11 scoring modules should ship
    model constants today. If this drops below the floor, either
    the scan regex broke or someone removed an ML model — both
    warrant a deliberate update of this test alongside the change."""
    files = {p.name for p in _scoring_files_with_model_constants()}
    expected = {
        "clip_embed.py",
        "clip_tokenizer.py",
        # face.py's model constants moved to face_yunet.py + face_mediapipe.py
        # during the v0.1 split — the model definitions live alongside the
        # singletons that own them.
        "face_yunet.py",
        "face_mediapipe.py",
        "face_blazeface_fr.py",
        # SFace's model constants moved from face_embed.py to
        # face_embed_sface_runtime.py during the SFace-runtime extraction
        # (f2a47a7) — the definitions live alongside the runtime that owns them.
        "face_embed_sface_runtime.py",
        "face_expression.py",
        "face_hand_filter.py",
        "face_scrfd.py",
        "face_embed_buffalo_s.py",
        "nudity.py",
        "pets.py",
        "pose.py",
        "segmentation.py",
    }
    missing = expected - files
    assert not missing, (
        "Expected these scoring modules to define model constants, "
        "but the scan didn't find any:\n"
        + "\n".join(f"  - {n}" for n in sorted(missing))
        + "\n\nIf one was intentionally removed or the constants were "
        "moved out, drop it from this test's expected set in the same "
        "commit."
    )

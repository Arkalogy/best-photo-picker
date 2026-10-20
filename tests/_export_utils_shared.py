"""Shared fixtures and helpers of the test_export_utils.py suite (split for the 500-LOC cap)."""

from __future__ import annotations

from tests._export_sidecar_shared import (
    _make_test_image,
)

# ── helpers ──────────────────────────────────────────────────────────────


def _double(x):
    """Top-level function so ProcessPoolExecutor can pickle it."""
    return x * 2


def _fail_on_three(x):
    """Top-level function that raises on x==3."""
    if x == 3:
        raise RuntimeError("intentional failure")
    return x * 2


def _always_fail(x):
    """Top-level function that always raises."""
    raise RuntimeError("boom")


def _make_selected(tmp_path, n=3):
    """Build a list of selected items backed by real image files."""
    items = []
    src_dir = tmp_path / "src"
    src_dir.mkdir(exist_ok=True)
    for i in range(n):
        p = src_dir / f"photo_{i}.jpg"
        _make_test_image(p, color=["red", "green", "blue"][i % 3])
        items.append(
            {
                "filepath": str(p),
                "date": f"2024-06-{10 + i:02d}",
                "aggregate_score": 0.8 - i * 0.1,
                "blur_score": 0.7,
                "exposure_score": 0.9,
                "face_score": 0.0,
                "composition_score": 0.6,
                "selection_reason": "top",
                "cluster_size": 1,
            }
        )
    return items


def _make_analysis(selected, extra=2):
    """Build analysis list = selected + some skipped items."""
    skipped = []
    for i in range(extra):
        skipped.append(
            {
                "filepath": f"/fake/skipped_{i}.jpg",
                "aggregate_score": 0.2,
            }
        )
    return selected + skipped

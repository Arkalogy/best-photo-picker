"""Shared fixtures and helpers of the test_export_sidecar.py suite (split for the 500-LOC cap)."""

from __future__ import annotations

from PIL import Image

# ── helpers ──────────────────────────────────────────────────────────────


def _make_test_image(path, size=(100, 100), color="red"):
    """Create a minimal JPEG image for testing."""
    img = Image.new("RGB", size, color)
    img.save(str(path), "JPEG")
    return str(path)


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
                "blur_score": 0.90,
                "exposure_score": 0.75,
                "face_score": 0.85,
                "composition_score": 0.78,
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

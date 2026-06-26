"""Tests for perceptual hashing and deduplication."""

from __future__ import annotations

import cv2
import numpy as np

from bpp.dedupe.phash import compute_dhash, hamming_distance


def _make_image(seed: int = 0, size: int = 100) -> np.ndarray:
    """Create a deterministic synthetic image."""
    rng = np.random.RandomState(seed)
    return rng.randint(0, 256, (size, size, 3), dtype=np.uint8)


def test_identical_images_have_zero_distance():
    img = _make_image(seed=42)
    h1 = compute_dhash(img)
    h2 = compute_dhash(img)
    assert hamming_distance(h1, h2) == 0


def test_similar_images_have_small_distance():
    """Slightly modified image should have small hash distance."""
    img = _make_image(seed=42)
    # Add slight blur
    blurred = cv2.GaussianBlur(img, (3, 3), 1)
    h1 = compute_dhash(img)
    h2 = compute_dhash(blurred)
    dist = hamming_distance(h1, h2)
    assert dist < 15  # Should be very similar


def test_different_images_have_large_distance():
    """Completely different images should have large hash distance."""
    img1 = _make_image(seed=1)
    img2 = _make_image(seed=999)
    h1 = compute_dhash(img1)
    h2 = compute_dhash(img2)
    dist = hamming_distance(h1, h2)
    assert dist > 5  # Should be noticeably different


def test_hamming_distance_symmetric():
    h1 = 0b10101010
    h2 = 0b01010101
    assert hamming_distance(h1, h2) == hamming_distance(h2, h1)


def test_hamming_distance_known():
    assert hamming_distance(0b1111, 0b0000) == 4
    assert hamming_distance(0b1111, 0b1111) == 0
    assert hamming_distance(0b1000, 0b0000) == 1


class TestGlobalDedupMonthProtection:
    """Global dedup must preserve at least one photo per month."""

    def test_month_rep_survives_global_dedup(self):
        """Photos from different months with identical hashes should both survive."""
        from bpp.dedupe.cluster import _global_dedup

        # Two photos with identical hashes but different months
        items = [
            {
                "filepath": "jan.jpg",
                "phash": 0xABCD,
                "ahash": 0x1234,
                "date_month": "2024-01",
                "aggregate_score": 5,
            },
            {
                "filepath": "feb.jpg",
                "phash": 0xABCD,
                "ahash": 0x1234,
                "date_month": "2024-02",
                "aggregate_score": 4,
            },
        ]
        result = _global_dedup(items, threshold=10)
        paths = {r["filepath"] for r in result}
        assert "jan.jpg" in paths
        assert "feb.jpg" in paths

    def test_same_month_duplicates_still_deduped(self):
        """Two identical photos in the same month: only one survives."""
        from bpp.dedupe.cluster import _global_dedup

        items = [
            {
                "filepath": "a.jpg",
                "phash": 0xABCD,
                "ahash": 0x1234,
                "date_month": "2024-01",
                "aggregate_score": 8,
            },
            {
                "filepath": "b.jpg",
                "phash": 0xABCD,
                "ahash": 0x1234,
                "date_month": "2024-01",
                "aggregate_score": 3,
            },
        ]
        result = _global_dedup(items, threshold=10)
        assert len(result) == 1
        assert result[0]["filepath"] == "a.jpg"

    def test_three_months_all_similar(self):
        """Three months of similar baby photos: one from each month kept."""
        from bpp.dedupe.cluster import _global_dedup

        items = [
            {
                "filepath": "jan.jpg",
                "phash": 0xABCD,
                "ahash": 0x1234,
                "date_month": "2024-01",
                "aggregate_score": 7,
            },
            {
                "filepath": "feb.jpg",
                "phash": 0xABCE,
                "ahash": 0x1235,
                "date_month": "2024-02",
                "aggregate_score": 6,
            },
            {
                "filepath": "mar.jpg",
                "phash": 0xABCF,
                "ahash": 0x1236,
                "date_month": "2024-03",
                "aggregate_score": 5,
            },
        ]
        # Threshold high enough that all match each other
        result = _global_dedup(items, threshold=10)
        months = {r.get("date_month") for r in result}
        assert months == {"2024-01", "2024-02", "2024-03"}

    def test_no_date_month_still_works(self):
        """Items without date_month don't crash; treated as 'unknown' month."""
        from bpp.dedupe.cluster import _global_dedup

        items = [
            {"filepath": "a.jpg", "phash": 0xABCD, "ahash": 0x1234, "aggregate_score": 5},
            {"filepath": "b.jpg", "phash": 0xABCD, "ahash": 0x1234, "aggregate_score": 3},
        ]
        result = _global_dedup(items, threshold=10)
        # Both in "unknown" month — only best kept
        assert len(result) == 1

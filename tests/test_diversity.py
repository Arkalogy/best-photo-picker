"""Tests for diversity constraint logic."""

from __future__ import annotations

import numpy as np

from bpp.selection.choose import choose
from bpp.selection.diversity import DiversityTracker


def _make_candidate(
    filepath: str,
    score: float,
    day: str = "2024-01-15",
    month: str = "2024-01",
    photo_id: int | None = None,
    phash: int | None = None,
    ahash: int | None = None,
) -> dict:
    d: dict = {
        "filepath": filepath,
        "aggregate_score": score,
        "date": f"{day}T12:00:00",
        "date_day": day,
        "date_month": month,
    }
    if photo_id is not None:
        d["id"] = photo_id
    if phash is not None:
        d["phash"] = phash
    if ahash is not None:
        d["ahash"] = ahash
    return d


def test_tracker_respects_daily_cap():
    tracker = DiversityTracker(max_per_day=2)
    item1 = _make_candidate("a.jpg", 0.9, day="2024-01-15")
    item2 = _make_candidate("b.jpg", 0.8, day="2024-01-15")
    item3 = _make_candidate("c.jpg", 0.7, day="2024-01-15")

    ok1, _ = tracker.can_accept(item1)
    assert ok1
    tracker.accept(item1)

    ok2, _ = tracker.can_accept(item2)
    assert ok2
    tracker.accept(item2)

    ok3, reason = tracker.can_accept(item3)
    assert not ok3
    assert "already has 2" in reason


def test_tracker_different_days_independent():
    tracker = DiversityTracker(max_per_day=1)
    item1 = _make_candidate("a.jpg", 0.9, day="2024-01-15")
    item2 = _make_candidate("b.jpg", 0.8, day="2024-01-16")

    tracker.accept(item1)
    ok, _ = tracker.can_accept(item2)
    assert ok


def test_choose_selects_k():
    candidates = [
        _make_candidate(f"{i}.jpg", score=0.5 + i * 0.01, day=f"2024-01-{i + 1:02d}")
        for i in range(20)
    ]
    selected = choose(candidates, k=5)
    assert len(selected) == 5


def test_choose_deterministic():
    candidates = [
        _make_candidate(f"{i}.jpg", score=0.5 + i * 0.01, day=f"2024-01-{i + 1:02d}")
        for i in range(20)
    ]
    s1 = choose(candidates, k=5, seed=42)
    s2 = choose(candidates, k=5, seed=42)
    assert [x["filepath"] for x in s1] == [x["filepath"] for x in s2]


def test_choose_respects_daily_cap():
    # 10 photos all from same day
    candidates = [
        _make_candidate(f"{i}.jpg", score=0.9 - i * 0.05, day="2024-06-15", month="2024-06")
        for i in range(10)
    ]
    selected = choose(candidates, k=5, config={"max_per_day": 3})
    # First pass gets 3 from day cap, then relaxation fills rest
    assert len(selected) == 5


def test_choose_covers_months():
    """Selection should try to cover all months."""
    candidates = []
    for m in range(1, 7):
        for i in range(5):
            candidates.append(
                _make_candidate(
                    f"m{m}_{i}.jpg",
                    score=0.5 + i * 0.01,
                    day=f"2024-{m:02d}-{10 + i:02d}",
                    month=f"2024-{m:02d}",
                )
            )
    selected = choose(candidates, k=12, config={"max_per_day": 3})
    months_covered = {s["date_month"] for s in selected}
    assert len(months_covered) == 6  # All 6 months should be covered


def test_choose_handles_k_greater_than_candidates():
    candidates = [_make_candidate(f"{i}.jpg", 0.8) for i in range(3)]
    selected = choose(candidates, k=10)
    assert len(selected) == 3  # Can't select more than available


# ── Visual similarity tests ──


def _make_clip_embedding(seed: int = 0) -> np.ndarray:
    """Create a deterministic L2-normalized 512-dim embedding."""
    rng = np.random.RandomState(seed)
    emb = rng.randn(512).astype(np.float32)
    emb /= np.linalg.norm(emb)
    return emb


class TestSimilarityDiversity:
    """Tests for CLIP and hash-based similarity rejection in selection."""

    def test_clip_rejects_similar_photos(self):
        """Photos with high CLIP similarity should not both be selected."""
        base_emb = _make_clip_embedding(42)
        similar_emb = base_emb + np.random.RandomState(1).randn(512).astype(np.float32) * 0.01
        similar_emb /= np.linalg.norm(similar_emb)
        assert float(np.dot(base_emb, similar_emb)) > 0.95

        clip_embs = {1: base_emb, 2: similar_emb}
        candidates = [
            _make_candidate("a.jpg", 0.9, photo_id=1, day="2024-01-01", month="2024-01"),
            _make_candidate("b.jpg", 0.85, photo_id=2, day="2024-01-02", month="2024-01"),
        ]
        selected = choose(
            candidates,
            k=10,
            config={"selection_similarity_threshold": 0.85},
            clip_embeddings=clip_embs,
        )
        assert len(selected) == 1
        assert selected[0]["filepath"] == "a.jpg"

    def test_clip_allows_different_photos(self):
        """Photos with low CLIP similarity should both be selected."""
        emb1 = _make_clip_embedding(42)
        emb2 = _make_clip_embedding(99)
        assert float(np.dot(emb1, emb2)) < 0.5

        clip_embs = {1: emb1, 2: emb2}
        candidates = [
            _make_candidate("a.jpg", 0.9, photo_id=1, day="2024-01-01", month="2024-01"),
            _make_candidate("b.jpg", 0.85, photo_id=2, day="2024-01-02", month="2024-01"),
        ]
        selected = choose(
            candidates,
            k=10,
            config={"selection_similarity_threshold": 0.85},
            clip_embeddings=clip_embs,
        )
        assert len(selected) == 2

    def test_threshold_zero_disables_similarity(self):
        """Threshold 0 should disable similarity checking entirely."""
        base_emb = _make_clip_embedding(42)
        clip_embs = {1: base_emb, 2: base_emb.copy()}
        candidates = [
            _make_candidate("a.jpg", 0.9, photo_id=1, day="2024-01-01", month="2024-01"),
            _make_candidate("b.jpg", 0.85, photo_id=2, day="2024-01-02", month="2024-01"),
        ]
        selected = choose(
            candidates,
            k=10,
            config={"selection_similarity_threshold": 0.0},
            clip_embeddings=clip_embs,
        )
        assert len(selected) == 2

    def test_hash_fallback_rejects_similar(self):
        """When no CLIP embeddings, hash distance should reject similar photos."""
        candidates = [
            _make_candidate(
                "a.jpg", 0.9, phash=0xABCD1234, ahash=0x1234ABCD, day="2024-01-01", month="2024-01"
            ),
            _make_candidate(
                "b.jpg", 0.85, phash=0xABCD1234, ahash=0x1234ABCD, day="2024-01-02", month="2024-01"
            ),
        ]
        selected = choose(
            candidates,
            k=10,
            config={"selection_similarity_threshold": 0.85},
        )
        assert len(selected) == 1
        assert selected[0]["filepath"] == "a.jpg"

    def test_hash_fallback_allows_different(self):
        """Very different hashes should both pass."""
        candidates = [
            _make_candidate(
                "a.jpg",
                0.9,
                phash=0x0000000000000000,
                ahash=0x0000000000000000,
                day="2024-01-01",
                month="2024-01",
            ),
            _make_candidate(
                "b.jpg",
                0.85,
                phash=0xFFFFFFFFFFFFFFFF,
                ahash=0xFFFFFFFFFFFFFFFF,
                day="2024-01-02",
                month="2024-01",
            ),
        ]
        selected = choose(
            candidates,
            k=10,
            config={"selection_similarity_threshold": 0.85},
        )
        assert len(selected) == 2

    def test_similarity_respected_in_relaxed_phase(self):
        """Even when day constraint is relaxed, similarity should still filter."""
        base_emb = _make_clip_embedding(42)
        similar_emb = base_emb + np.random.RandomState(1).randn(512).astype(np.float32) * 0.01
        similar_emb /= np.linalg.norm(similar_emb)

        clip_embs = {1: base_emb, 2: similar_emb}
        candidates = [
            _make_candidate("a.jpg", 0.9, photo_id=1, day="2024-01-15", month="2024-01"),
            _make_candidate("b.jpg", 0.85, photo_id=2, day="2024-01-15", month="2024-01"),
        ]
        selected = choose(
            candidates,
            k=10,
            config={"selection_similarity_threshold": 0.85, "max_per_day": 1},
            clip_embeddings=clip_embs,
        )
        assert len(selected) == 1

    def test_tracker_rejects_similar_clip(self):
        """DiversityTracker.can_accept rejects CLIP-similar photos."""
        emb = _make_clip_embedding(42)
        tracker = DiversityTracker(
            similarity_threshold=0.85,
            clip_embeddings={1: emb, 2: emb},
        )
        tracker.set_available_months({"2024-01"})

        item1 = _make_candidate("a.jpg", 0.9, photo_id=1)
        item2 = _make_candidate("b.jpg", 0.85, photo_id=2)

        ok1, _ = tracker.can_accept(item1)
        assert ok1
        tracker.accept(item1)

        ok2, reason = tracker.can_accept(item2)
        assert not ok2
        assert "similar" in reason

    def test_tracker_no_clip_no_hash_passes(self):
        """Items without CLIP or hash data should always pass similarity check."""
        tracker = DiversityTracker(similarity_threshold=0.85)
        tracker.set_available_months({"2024-01"})

        item1 = _make_candidate("a.jpg", 0.9)
        item2 = _make_candidate("b.jpg", 0.85)

        tracker.accept(item1)
        ok, _ = tracker.can_accept(item2)
        assert ok

    def test_clip_passes_but_hash_catches(self):
        """Photos different enough for CLIP but perceptually identical by hash."""
        emb1 = _make_clip_embedding(42)
        # Create embedding that's similar but below CLIP threshold
        emb2 = _make_clip_embedding(99)
        assert float(np.dot(emb1, emb2)) < 0.85  # CLIP would pass

        # But give them identical hashes (perceptually identical)
        candidates = [
            _make_candidate(
                "a.jpg",
                0.9,
                photo_id=1,
                phash=0xABCD1234,
                ahash=0x1234ABCD,
                day="2024-01-01",
                month="2024-01",
            ),
            _make_candidate(
                "b.jpg",
                0.85,
                photo_id=2,
                phash=0xABCD1234,
                ahash=0x1234ABCD,
                day="2024-01-02",
                month="2024-01",
            ),
        ]
        clip_embs = {1: emb1, 2: emb2}
        selected = choose(
            candidates,
            k=10,
            config={"selection_similarity_threshold": 0.85},
            clip_embeddings=clip_embs,
        )
        # Hash should catch the duplicate even though CLIP didn't
        assert len(selected) == 1
        assert selected[0]["filepath"] == "a.jpg"

"""Tests for face cluster feedback loop — adaptive threshold + hard negatives."""

from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture
def conn(tmp_path):
    """Fresh DB with schema for face feedback tests."""
    db_path = str(tmp_path / "test.db")
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    from bpp.db.schema import create_tables

    create_tables(c)
    return c


# ── Feedback recording ──


class TestStoreFeedback:
    def test_store_merge_feedback(self, conn):
        from bpp.db.face_feedback import store_face_feedback

        store_face_feedback(conn, "merge", cluster_id_a=3, cluster_id_b=7, distance=0.42)
        rows = conn.execute("SELECT * FROM face_cluster_feedback").fetchall()
        assert len(rows) == 1
        assert rows[0]["action"] == "merge"
        assert rows[0]["cluster_id_a"] == 3
        assert rows[0]["cluster_id_b"] == 7
        assert abs(rows[0]["distance"] - 0.42) < 1e-6

    def test_store_reassign_out_feedback(self, conn):
        from bpp.db.face_feedback import store_face_feedback

        store_face_feedback(conn, "reassign_out", cluster_id_a=5, distance=0.61)
        rows = conn.execute("SELECT * FROM face_cluster_feedback").fetchall()
        assert len(rows) == 1
        assert rows[0]["action"] == "reassign_out"

    def test_store_reassign_in_feedback(self, conn):
        from bpp.db.face_feedback import store_face_feedback

        store_face_feedback(conn, "reassign_in", cluster_id_a=8, distance=0.35)
        rows = conn.execute("SELECT * FROM face_cluster_feedback").fetchall()
        assert len(rows) == 1
        assert rows[0]["action"] == "reassign_in"

    def test_invalid_action_rejected(self, conn):
        from bpp.db.face_feedback import store_face_feedback

        with pytest.raises(ValueError, match="action must be"):
            store_face_feedback(conn, "invalid", cluster_id_a=1, distance=0.5)

    def test_multiple_feedback_entries(self, conn):
        from bpp.db.face_feedback import store_face_feedback

        store_face_feedback(conn, "merge", cluster_id_a=1, cluster_id_b=2, distance=0.4)
        store_face_feedback(conn, "merge", cluster_id_a=3, cluster_id_b=4, distance=0.5)
        store_face_feedback(conn, "reassign_out", cluster_id_a=5, distance=0.6)
        rows = conn.execute("SELECT * FROM face_cluster_feedback").fetchall()
        assert len(rows) == 3


class TestGetFeedback:
    def test_get_all_feedback(self, conn):
        from bpp.db.face_feedback import get_face_feedback, store_face_feedback

        store_face_feedback(conn, "merge", cluster_id_a=1, cluster_id_b=2, distance=0.4)
        store_face_feedback(conn, "reassign_out", cluster_id_a=3, distance=0.6)
        fb = get_face_feedback(conn)
        assert len(fb) == 2
        assert fb[0]["action"] == "merge"
        assert fb[1]["action"] == "reassign_out"

    def test_get_feedback_empty(self, conn):
        from bpp.db.face_feedback import get_face_feedback

        assert get_face_feedback(conn) == []


# ── Hard negatives ──


class TestHardNegatives:
    def test_store_hard_negative(self, conn):
        from bpp.db.face_feedback import store_hard_negative

        store_hard_negative(conn, 3, 7)
        rows = conn.execute("SELECT * FROM face_hard_negatives").fetchall()
        assert len(rows) == 1
        # Should normalize ordering
        assert rows[0]["cluster_id_a"] == 3
        assert rows[0]["cluster_id_b"] == 7
        assert rows[0]["count"] == 1

    def test_store_hard_negative_normalized_order(self, conn):
        from bpp.db.face_feedback import store_hard_negative

        store_hard_negative(conn, 10, 2)
        rows = conn.execute("SELECT * FROM face_hard_negatives").fetchall()
        assert rows[0]["cluster_id_a"] == 2
        assert rows[0]["cluster_id_b"] == 10

    def test_store_hard_negative_increments_count(self, conn):
        from bpp.db.face_feedback import store_hard_negative

        store_hard_negative(conn, 3, 7)
        store_hard_negative(conn, 3, 7)
        store_hard_negative(conn, 7, 3)  # reversed — same pair
        rows = conn.execute("SELECT * FROM face_hard_negatives").fetchall()
        assert len(rows) == 1
        assert rows[0]["count"] == 3

    def test_remove_hard_negative_on_merge(self, conn):
        from bpp.db.face_feedback import remove_hard_negative, store_hard_negative

        store_hard_negative(conn, 3, 7)
        remove_hard_negative(conn, 3, 7)
        rows = conn.execute("SELECT * FROM face_hard_negatives").fetchall()
        assert len(rows) == 0

    def test_remove_hard_negative_reversed(self, conn):
        from bpp.db.face_feedback import remove_hard_negative, store_hard_negative

        store_hard_negative(conn, 3, 7)
        remove_hard_negative(conn, 7, 3)  # reversed
        rows = conn.execute("SELECT * FROM face_hard_negatives").fetchall()
        assert len(rows) == 0

    def test_get_hard_negatives(self, conn):
        from bpp.db.face_feedback import get_hard_negatives, store_hard_negative

        store_hard_negative(conn, 1, 5)
        store_hard_negative(conn, 2, 8)
        negs = get_hard_negatives(conn)
        assert len(negs) == 2

    def test_get_hard_negatives_for_cluster(self, conn):
        from bpp.db.face_feedback import get_hard_negatives_for_cluster, store_hard_negative

        store_hard_negative(conn, 1, 5)
        store_hard_negative(conn, 1, 8)
        store_hard_negative(conn, 2, 3)
        negs = get_hard_negatives_for_cluster(conn, 1)
        assert set(negs) == {5, 8}

    def test_get_hard_negatives_for_cluster_reversed(self, conn):
        from bpp.db.face_feedback import get_hard_negatives_for_cluster, store_hard_negative

        store_hard_negative(conn, 5, 1)  # stored as (1, 5)
        negs = get_hard_negatives_for_cluster(conn, 1)
        assert negs == [5]
        negs2 = get_hard_negatives_for_cluster(conn, 5)
        assert negs2 == [1]


# ── Adaptive threshold ──


class TestAdaptiveThreshold:
    def test_no_feedback_returns_default(self, conn):
        from bpp.db.face_feedback import compute_adaptive_face_threshold

        threshold, info = compute_adaptive_face_threshold(conn)
        assert threshold == 0.55
        assert info["source"] == "default"
        assert info["feedback_count"] == 0

    def test_merge_only_lowers_threshold(self, conn):
        """Merges = 'same' signals. If merges had distance 0.6, threshold should increase."""
        from bpp.db.face_feedback import (
            compute_adaptive_face_threshold,
            store_face_feedback,
        )

        # Merges at distance 0.6 — user says faces 0.6 apart are the same person
        for _ in range(20):
            store_face_feedback(conn, "merge", cluster_id_a=1, cluster_id_b=2, distance=0.6)
        threshold, info = compute_adaptive_face_threshold(conn)
        assert info["source"] != "default"
        # With 20 samples, alpha≈1.0, threshold should be near 0.6 + margin
        assert threshold > 0.55

    def test_reassign_out_raises_threshold(self, conn):
        """Reassign-out = 'different' signal. Distance was 0.45 but face didn't belong."""
        from bpp.db.face_feedback import (
            compute_adaptive_face_threshold,
            store_face_feedback,
        )

        for _ in range(20):
            store_face_feedback(conn, "reassign_out", cluster_id_a=1, distance=0.45)
        threshold, _info = compute_adaptive_face_threshold(conn)
        # Should push threshold lower than default since close faces were wrong matches
        assert threshold < 0.55

    def test_mixed_signals_find_boundary(self, conn):
        from bpp.db.face_feedback import (
            compute_adaptive_face_threshold,
            store_face_feedback,
        )

        # Same signals at distances 0.50, 0.55, 0.60
        for d in [0.50, 0.55, 0.60]:
            for _ in range(5):
                store_face_feedback(conn, "merge", cluster_id_a=1, cluster_id_b=2, distance=d)
        # Different signals at distances 0.65, 0.70
        for d in [0.65, 0.70]:
            for _ in range(5):
                store_face_feedback(conn, "reassign_out", cluster_id_a=3, distance=d)
        threshold, info = compute_adaptive_face_threshold(conn)
        assert info["same_count"] == 15
        assert info["different_count"] == 10
        # Boundary should be between 0.60 (max same) and 0.65 (min different)
        assert 0.55 < threshold < 0.70

    def test_confidence_increases_with_samples(self, conn):
        from bpp.db.face_feedback import (
            compute_adaptive_face_threshold,
            store_face_feedback,
        )

        store_face_feedback(conn, "merge", cluster_id_a=1, cluster_id_b=2, distance=0.5)
        _, info1 = compute_adaptive_face_threshold(conn)
        for _ in range(19):
            store_face_feedback(conn, "merge", cluster_id_a=1, cluster_id_b=2, distance=0.5)
        _, info2 = compute_adaptive_face_threshold(conn)
        assert info2["confidence"] > info1["confidence"]

    def test_custom_default(self, conn):
        from bpp.db.face_feedback import compute_adaptive_face_threshold

        threshold, _ = compute_adaptive_face_threshold(conn, default=0.72)
        assert threshold == 0.72

    def test_clamped_to_range(self, conn):
        from bpp.db.face_feedback import (
            compute_adaptive_face_threshold,
            store_face_feedback,
        )

        # Extreme merge distances
        for _ in range(20):
            store_face_feedback(conn, "merge", cluster_id_a=1, cluster_id_b=2, distance=2.0)
        threshold, _ = compute_adaptive_face_threshold(conn)
        assert threshold <= 1.0

    def test_reassign_in_counted_as_same(self, conn):
        from bpp.db.face_feedback import (
            compute_adaptive_face_threshold,
            store_face_feedback,
        )

        for _ in range(20):
            store_face_feedback(conn, "reassign_in", cluster_id_a=1, distance=0.48)
        _, info = compute_adaptive_face_threshold(conn)
        assert info["same_count"] == 20


# ── Nudge logic ──


class TestFeedbackNudge:
    def test_no_nudge_without_feedback(self, conn):
        from bpp.db.face_feedback import should_suggest_recluster

        assert should_suggest_recluster(conn, current_threshold=0.55) is False

    def test_nudge_when_threshold_diverges(self, conn):
        from bpp.db.face_feedback import (
            should_suggest_recluster,
            store_face_feedback,
        )

        # Enough feedback to be confident, and learned threshold differs from current
        for _ in range(20):
            store_face_feedback(conn, "merge", cluster_id_a=1, cluster_id_b=2, distance=0.65)
        assert should_suggest_recluster(conn, current_threshold=0.55) is True

    def test_no_nudge_when_threshold_close(self, conn):
        from bpp.db.face_feedback import (
            should_suggest_recluster,
            store_face_feedback,
        )

        # Feedback consistent with current threshold
        for _ in range(20):
            store_face_feedback(conn, "merge", cluster_id_a=1, cluster_id_b=2, distance=0.53)
        assert should_suggest_recluster(conn, current_threshold=0.55) is False

    def test_no_nudge_with_low_confidence(self, conn):
        from bpp.db.face_feedback import (
            should_suggest_recluster,
            store_face_feedback,
        )

        # Only 2 feedback entries — not enough confidence
        store_face_feedback(conn, "merge", cluster_id_a=1, cluster_id_b=2, distance=0.8)
        store_face_feedback(conn, "merge", cluster_id_a=3, cluster_id_b=4, distance=0.9)
        assert should_suggest_recluster(conn, current_threshold=0.55) is False

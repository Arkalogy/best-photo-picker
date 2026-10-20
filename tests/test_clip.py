"""Tests for CLIP-based deduplication and feedback learning — part 1 of 2."""

# Shared fixtures are imported by name and re-bound as test parameters.
# ruff: noqa: F811

from __future__ import annotations

import numpy as np
import pytest

from bpp.db.clip import (
    compute_adaptive_threshold,
    get_all_clip_embeddings,
    get_clip_embedding_count,
    get_dedup_feedback,
    record_dedup_feedback,
    upsert_clip_embedding,
)
from tests._clip_shared import db  # noqa: F401


class TestClipEmbeddings:
    def test_upsert_and_get(self, db):
        emb = np.random.randn(512).astype(np.float32)
        emb = emb / np.linalg.norm(emb)
        upsert_clip_embedding(db, photo_id=1, embedding=emb)

        all_embs = get_all_clip_embeddings(db)
        assert 1 in all_embs
        np.testing.assert_array_almost_equal(all_embs[1], emb, decimal=5)

    def test_count(self, db):
        assert get_clip_embedding_count(db) == 0
        upsert_clip_embedding(db, 1, np.random.randn(512).astype(np.float32))
        upsert_clip_embedding(db, 2, np.random.randn(512).astype(np.float32))
        assert get_clip_embedding_count(db) == 2

    def test_upsert_replaces(self, db):
        emb1 = np.ones(512, dtype=np.float32)
        emb2 = np.zeros(512, dtype=np.float32)
        upsert_clip_embedding(db, 1, emb1)
        upsert_clip_embedding(db, 1, emb2)
        assert get_clip_embedding_count(db) == 1
        all_embs = get_all_clip_embeddings(db)
        np.testing.assert_array_almost_equal(all_embs[1], emb2)

    def test_load_refused_above_max_rows(self, db, monkeypatch):
        """Hard ceiling on the load size. Without the cap, a
        library with hundreds of thousands of embeddings allocates
        ~500MB-1GB before WebAppState.load_clip_embeddings even gets
        a chance to wrap it in a try/except. With the cap, we raise
        a typed exception BEFORE the row read so callers can degrade
        gracefully (skip CLIP semantic dedupe instead of OOM-killing
        the server)."""
        from bpp.db import clip as clip_mod
        from bpp.db.clip import ClipEmbeddingsTooLarge

        # Drop the cap so we don't have to insert hundreds of
        # thousands of fixture rows.
        monkeypatch.setattr(clip_mod, "CLIP_EMBEDDING_MAX_ROWS", 2)

        upsert_clip_embedding(db, 1, np.random.randn(512).astype(np.float32))
        upsert_clip_embedding(db, 2, np.random.randn(512).astype(np.float32))
        upsert_clip_embedding(db, 3, np.random.randn(512).astype(np.float32))

        with pytest.raises(ClipEmbeddingsTooLarge) as excinfo:
            get_all_clip_embeddings(db)
        assert excinfo.value.count == 3
        assert excinfo.value.cap == 2

    def test_load_succeeds_at_or_below_max_rows(self, db, monkeypatch):
        """Inverse: the cap is inclusive on the safe side. count == cap
        loads normally; count == cap + 1 trips the guard."""
        from bpp.db import clip as clip_mod

        monkeypatch.setattr(clip_mod, "CLIP_EMBEDDING_MAX_ROWS", 2)

        upsert_clip_embedding(db, 1, np.random.randn(512).astype(np.float32))
        upsert_clip_embedding(db, 2, np.random.randn(512).astype(np.float32))

        # Exactly at cap — must load
        embs = get_all_clip_embeddings(db)
        assert len(embs) == 2


class TestClipCapOverride:
    """Cap-bypass via per-library setting (the Settings → Library banner flow).

    Verifies the override is the SINGLE knob that flips the cap check:
    same library + same row count + same env var, but with the override
    setting present, the load succeeds instead of raising.
    """

    def test_override_bypasses_cap(self, db, monkeypatch):
        from bpp.db import clip as clip_mod
        from bpp.db.clip import (
            CLIP_MAX_OVERRIDE_BYPASS,
            CLIP_MAX_OVERRIDE_KEY,
        )
        from bpp.db.settings import set_setting

        monkeypatch.setattr(clip_mod, "CLIP_EMBEDDING_MAX_ROWS", 2)

        upsert_clip_embedding(db, 1, np.random.randn(512).astype(np.float32))
        upsert_clip_embedding(db, 2, np.random.randn(512).astype(np.float32))
        upsert_clip_embedding(db, 3, np.random.randn(512).astype(np.float32))

        # Without the override, this raises (covered by
        # test_load_refused_above_max_rows above — repeated here as the
        # control case so a failure of this assertion is read as "the
        # cap was never actually being enforced").
        from bpp.db.clip import ClipEmbeddingsTooLarge

        with pytest.raises(ClipEmbeddingsTooLarge):
            get_all_clip_embeddings(db)

        # With the override, the same DB + cap loads cleanly.
        set_setting(db, CLIP_MAX_OVERRIDE_KEY, CLIP_MAX_OVERRIDE_BYPASS)
        embs = get_all_clip_embeddings(db)
        assert len(embs) == 3

    def test_override_with_non_bypass_value_does_not_unlock(self, db, monkeypatch):
        """Only the literal 'bypass' sentinel unlocks the cap. Any other
        value (truthy / falsy / typo) leaves the cap enforced. Locks the
        constant in case someone later changes the sentinel string.
        """
        from bpp.db import clip as clip_mod
        from bpp.db.clip import CLIP_MAX_OVERRIDE_KEY, ClipEmbeddingsTooLarge
        from bpp.db.settings import set_setting

        monkeypatch.setattr(clip_mod, "CLIP_EMBEDDING_MAX_ROWS", 2)

        upsert_clip_embedding(db, 1, np.random.randn(512).astype(np.float32))
        upsert_clip_embedding(db, 2, np.random.randn(512).astype(np.float32))
        upsert_clip_embedding(db, 3, np.random.randn(512).astype(np.float32))

        for stray_value in ("true", "1", "yes", "enabled", ""):
            set_setting(db, CLIP_MAX_OVERRIDE_KEY, stray_value)
            with pytest.raises(ClipEmbeddingsTooLarge):
                get_all_clip_embeddings(db)


class TestDedupFeedback:
    def test_record_and_get(self, db):
        record_dedup_feedback(db, 1, 2, 0.95, "same")
        feedback = get_dedup_feedback(db)
        assert len(feedback) == 1
        assert feedback[0]["similarity"] == 0.95
        assert feedback[0]["verdict"] == "same"

    def test_pair_ordering_normalized(self, db):
        record_dedup_feedback(db, 3, 1, 0.90, "different")
        feedback = get_dedup_feedback(db)
        assert feedback[0]["photo_id_a"] == 1
        assert feedback[0]["photo_id_b"] == 3

    def test_upsert_updates_verdict(self, db):
        record_dedup_feedback(db, 1, 2, 0.95, "same")
        record_dedup_feedback(db, 1, 2, 0.93, "different")
        feedback = get_dedup_feedback(db)
        assert len(feedback) == 1
        assert feedback[0]["verdict"] == "different"

    def test_invalid_verdict_raises(self, db):
        with pytest.raises(ValueError, match="verdict must be"):
            record_dedup_feedback(db, 1, 2, 0.95, "maybe")


class TestAdaptiveThreshold:
    def test_no_feedback_returns_default(self, db):
        threshold, info = compute_adaptive_threshold(db, default=0.92)
        assert threshold == 0.92
        assert info["feedback_count"] == 0
        assert info["source"] == "default"

    def test_same_feedback_lowers_threshold(self, db):
        record_dedup_feedback(db, 1, 2, 0.90, "same")
        threshold, info = compute_adaptive_threshold(db, default=0.92)
        # With 1 feedback: alpha = 1/20 = 0.05
        # computed = 0.90 - 0.02 = 0.88
        # threshold = 0.05 * 0.88 + 0.95 * 0.92 ≈ 0.918
        assert threshold < 0.92
        assert info["same_count"] == 1

    def test_different_feedback_raises_threshold(self, db):
        record_dedup_feedback(db, 1, 2, 0.93, "different")
        threshold, info = compute_adaptive_threshold(db, default=0.92)
        # computed = 0.93 + 0.02 = 0.95
        # threshold = 0.05 * 0.95 + 0.95 * 0.92 ≈ 0.9215
        assert threshold > 0.92
        assert info["different_count"] == 1

    def test_many_feedback_converges(self, db):
        # Insert enough photos for 25 unique pairs
        for i in range(5, 30):
            db.execute(
                "INSERT INTO photos (filepath, original_filename, file_size, file_mtime)"
                " VALUES (?, ?, 1000, 1.0)",
                (f"/extra_{i}.jpg", f"extra_{i}.jpg"),
            )
        db.commit()
        # 25 "same" at similarity 0.85 with unique pairs
        for i in range(25):
            record_dedup_feedback(db, 1, i + 2, 0.85, "same")
        threshold, info = compute_adaptive_threshold(db, default=0.92)
        assert info["confidence"] == 1.0  # fully confident
        assert threshold < 0.85  # below the lowest same similarity

    def test_threshold_clamped(self, db):
        record_dedup_feedback(db, 1, 2, 0.70, "same")
        for _ in range(25):
            record_dedup_feedback(db, 1, 2, 0.70, "same")
        threshold, _ = compute_adaptive_threshold(db, default=0.92)
        assert threshold >= 0.75  # clamped floor

"""Tests for CLIP-based deduplication and feedback learning."""

from __future__ import annotations

import sqlite3

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
from bpp.db.schema import create_tables
from bpp.dedupe.semantic import semantic_deduplicate


@pytest.fixture()
def db():
    """In-memory SQLite DB with all tables."""
    conn = sqlite3.connect(":memory:")
    # Row factory matches the production get_db() pool. get_setting()
    # reads `row["value"]`, which needs this — without it the new cap-
    # override tests trip on raw tuple indexing in settings.py.
    conn.row_factory = sqlite3.Row
    create_tables(conn)
    # Insert test photos
    for i in range(5):
        conn.execute(
            "INSERT INTO photos (filepath, original_filename, file_size, file_mtime, date,"
            " date_day, date_month, aggregate_score)"
            " VALUES (?, ?, 1000, 1.0, ?, ?, ?, ?)",
            (
                f"/photo_{i}.jpg",
                f"photo_{i}.jpg",
                f"2024-01-01T12:00:0{i}",
                "2024-01-01",
                "2024-01",
                0.5 + i * 0.1,
            ),
        )
    conn.commit()
    yield conn
    conn.close()


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
        """R8-H8: hard ceiling on the load size. Without the cap, a
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


class TestSemanticDeduplicate:
    def _make_analysis(self, n=5):
        return [
            {
                "filepath": f"/photo_{i}.jpg",
                "id": i + 1,
                "date": f"2024-01-01T12:00:0{i}",
                "date_day": "2024-01-01",
                "date_month": "2024-01",
                "aggregate_score": 0.5 + i * 0.1,
            }
            for i in range(n)
        ]

    def _make_similar_embeddings(self, n, similarity=0.98):
        """Generate n embeddings that are mutually similar."""
        base = np.random.randn(512).astype(np.float32)
        base = base / np.linalg.norm(base)
        embs = {}
        for i in range(n):
            noise = np.random.randn(512).astype(np.float32) * (1 - similarity)
            emb = base + noise
            emb = emb / np.linalg.norm(emb)
            embs[i + 1] = emb
        return embs

    def test_identical_embeddings_cluster(self):
        analysis = self._make_analysis(3)
        emb = np.random.randn(512).astype(np.float32)
        emb = emb / np.linalg.norm(emb)
        clip_embs = {1: emb, 2: emb.copy(), 3: emb.copy()}

        result = semantic_deduplicate(analysis, clip_embs, threshold=0.9)
        assert len(result) == 1
        assert result[0]["cluster_size"] == 3

    def test_different_embeddings_stay_separate(self):
        analysis = self._make_analysis(3)
        clip_embs = {}
        for i in range(3):
            emb = np.zeros(512, dtype=np.float32)
            emb[i * 170 : (i + 1) * 170] = 1.0
            emb = emb / np.linalg.norm(emb)
            clip_embs[i + 1] = emb

        result = semantic_deduplicate(analysis, clip_embs, threshold=0.9)
        assert len(result) == 3

    def test_best_score_selected_as_representative(self):
        analysis = self._make_analysis(3)
        # All same embedding
        emb = np.random.randn(512).astype(np.float32)
        emb = emb / np.linalg.norm(emb)
        clip_embs = {1: emb, 2: emb.copy(), 3: emb.copy()}

        result = semantic_deduplicate(analysis, clip_embs, threshold=0.9)
        assert len(result) == 1
        # Photo 3 has highest score (0.7)
        assert result[0]["filepath"] == "/photo_2.jpg"

    def test_no_embeddings_passes_through(self):
        analysis = self._make_analysis(3)
        result = semantic_deduplicate(analysis, {}, threshold=0.9)
        assert len(result) == 3

    def test_similar_photos_annotated(self):
        analysis = self._make_analysis(3)
        emb = np.random.randn(512).astype(np.float32)
        emb = emb / np.linalg.norm(emb)
        clip_embs = {1: emb, 2: emb.copy(), 3: emb.copy()}

        result = semantic_deduplicate(analysis, clip_embs, threshold=0.9)
        assert "similar_photos" in result[0]
        assert len(result[0]["similar_photos"]) == 2

    def test_empty_analysis_returns_empty(self):
        """No photos in, no photos out — must not crash on empty matrices."""
        result = semantic_deduplicate([], {}, threshold=0.9)
        assert result == []

    def test_single_item_passes_through(self):
        """One photo can't be a duplicate of anything; it survives untouched."""
        analysis = [
            {"filepath": "/p.jpg", "id": 1, "date": "2024-01-01T00:00:00", "aggregate_score": 0.5}
        ]
        emb = np.random.randn(512).astype(np.float32)
        emb /= np.linalg.norm(emb)
        result = semantic_deduplicate(analysis, {1: emb}, threshold=0.9)
        assert len(result) == 1
        assert result[0]["filepath"] == "/p.jpg"

    def test_mixed_embeddings_and_no_embeddings(self):
        """Items without embeddings can't be matched OR matched-against —
        they pass through as unique. Critical edge for libraries where
        CLIP analysis hasn't covered every photo."""
        analysis = [
            {"filepath": "/a.jpg", "id": 1, "date": "2024-01-01T00:00:00", "aggregate_score": 0.5},
            {"filepath": "/b.jpg", "id": 2, "date": "2024-01-01T00:00:05", "aggregate_score": 0.5},
            {"filepath": "/c.jpg", "id": 3, "date": "2024-01-01T00:00:10", "aggregate_score": 0.5},
        ]
        emb = np.random.randn(512).astype(np.float32)
        emb /= np.linalg.norm(emb)
        # Only b has an embedding.
        clip_embs = {2: emb}
        result = semantic_deduplicate(analysis, clip_embs, threshold=0.9)
        assert len(result) == 3, "items without embeddings must survive — never silently dropped"
        paths = {r["filepath"] for r in result}
        assert paths == {"/a.jpg", "/b.jpg", "/c.jpg"}

    def test_threshold_boundary_inclusive(self):
        """`sim >= threshold` is inclusive — equality MUST merge, not split.
        A strict-greater regression would silently break dedup on
        anything that lands exactly on the threshold (common with
        rounded thresholds like 0.9)."""
        # Construct two embeddings with cosine exactly 0.9.
        a = np.zeros(512, dtype=np.float32)
        a[0] = 1.0
        b = np.zeros(512, dtype=np.float32)
        b[0] = 0.9
        b[1] = float(np.sqrt(1 - 0.81))  # makes |b|=1, dot(a,b)=0.9 exactly
        analysis = [
            {"filepath": "/a.jpg", "id": 1, "date": "2024-01-01T00:00:00", "aggregate_score": 0.9},
            {"filepath": "/b.jpg", "id": 2, "date": "2024-01-01T00:00:05", "aggregate_score": 0.5},
        ]
        result = semantic_deduplicate(analysis, {1: a, 2: b}, threshold=0.9)
        assert len(result) == 1, "cosine exactly == threshold must merge"

    def test_threshold_just_below_does_not_merge(self):
        """The inverse of the inclusive-equality test: a similarity
        strictly below threshold must NOT merge."""
        a = np.zeros(512, dtype=np.float32)
        a[0] = 1.0
        b = np.zeros(512, dtype=np.float32)
        b[0] = 0.89
        b[1] = float(np.sqrt(1 - 0.89**2))
        analysis = [
            {"filepath": "/a.jpg", "id": 1, "date": "2024-01-01T00:00:00", "aggregate_score": 0.9},
            {"filepath": "/b.jpg", "id": 2, "date": "2024-01-01T00:00:05", "aggregate_score": 0.5},
        ]
        result = semantic_deduplicate(analysis, {1: a, 2: b}, threshold=0.9)
        assert len(result) == 2, "cosine strictly below threshold must NOT merge"

    def test_time_window_boundary_inclusive(self):
        """`abs(dt) <= window_seconds` is inclusive — items exactly at
        the boundary must still be considered for clustering. A
        strict-less regression would silently fail to merge bursts
        that land on a round-number gap."""
        emb = np.random.randn(512).astype(np.float32)
        emb /= np.linalg.norm(emb)
        analysis = [
            {"filepath": "/a.jpg", "id": 1, "date": "2024-01-01T00:00:00", "aggregate_score": 0.9},
            # Exactly 30 seconds later — must still merge at window=30.
            {"filepath": "/b.jpg", "id": 2, "date": "2024-01-01T00:00:30", "aggregate_score": 0.5},
        ]
        result = semantic_deduplicate(
            analysis,
            {1: emb, 2: emb.copy()},
            threshold=0.9,
            config={"time_window_seconds": 30},
        )
        # Pass 1 should merge (within window). Pass 2 would also catch
        # them, so this only proves pass-1 boundary if pass 1 is hit
        # first. Either way, cluster_size must be 2.
        assert len(result) == 1
        assert result[0].get("cluster_size") == 2

    def test_time_window_just_past_falls_to_pass_2(self):
        """Beyond the time window pass 1 leaves them separate; pass 2
        still merges identical embeddings globally. Demonstrates the
        two-pass split is intact."""
        emb = np.random.randn(512).astype(np.float32)
        emb /= np.linalg.norm(emb)
        analysis = [
            {"filepath": "/a.jpg", "id": 1, "date": "2024-01-01T00:00:00", "aggregate_score": 0.9},
            # 31s gap > 30s window → pass 1 skips, pass 2 catches.
            {"filepath": "/b.jpg", "id": 2, "date": "2024-01-01T00:00:31", "aggregate_score": 0.5},
        ]
        result = semantic_deduplicate(
            analysis,
            {1: emb, 2: emb.copy()},
            threshold=0.9,
            config={"time_window_seconds": 30},
        )
        assert len(result) == 1, "pass 2 must catch what pass 1 skipped"

    def test_pass_1_picks_most_recent_qualifying_cluster(self):
        """When several recent clusters qualify, pass 1 binds to the
        MOST RECENT one — preserves the original "iterate in reverse,
        break on first match" semantic from the pre-vectorized loop.
        Encoded with `flatnonzero().max()` in the vectorized version;
        an `argmax` regression would bind to "highest similarity" instead.
        """
        # Two prior clusters, both within time window AND threshold,
        # but the most recent has LOWER similarity — must still win.
        c0_emb = np.zeros(512, dtype=np.float32)
        c0_emb[0] = 1.0  # cluster 0 rep — perfect match to candidate
        c1_emb = np.zeros(512, dtype=np.float32)
        c1_emb[0] = 0.95
        c1_emb[1] = float(np.sqrt(1 - 0.95**2))  # 0.95 cosine — lower
        candidate_emb = c0_emb.copy()

        analysis = [
            {"filepath": "/c0.jpg", "id": 1, "date": "2024-01-01T00:00:00", "aggregate_score": 0.9},
            {"filepath": "/c1.jpg", "id": 2, "date": "2024-01-01T00:00:05", "aggregate_score": 0.8},
            {
                "filepath": "/cand.jpg",
                "id": 3,
                "date": "2024-01-01T00:00:10",
                "aggregate_score": 0.5,
            },
        ]
        clip_embs = {1: c0_emb, 2: c1_emb, 3: candidate_emb}
        result = semantic_deduplicate(
            analysis,
            clip_embs,
            threshold=0.9,
            config={"time_window_seconds": 30},
        )
        # In pass 1 the candidate sees c1 first (most recent) — c1
        # qualifies (cos 0.95 >= 0.9, within window) so candidate joins
        # c1's cluster, NOT c0's. Pass 2 then merges c1 INTO c0 (because
        # c0 has the higher aggregate_score so it becomes the global rep).
        # Final result: 1 rep with cluster_size 3.
        assert len(result) == 1
        assert result[0]["filepath"] == "/c0.jpg", "highest-score rep wins pass 2"
        assert result[0].get("cluster_size") == 3

    def test_lookback_window_boundary(self):
        """Pass 1 only considers the last 100 clusters when matching —
        items beyond the LOOKBACK window can't be merged via pass 1
        even if they'd otherwise qualify. Pass 2 picks up the slack."""
        emb = np.random.randn(512).astype(np.float32)
        emb /= np.linalg.norm(emb)
        # 102 photos: first and last are within time window AND have
        # identical embeddings. The 100 photos in between have DIFFERENT
        # embeddings so they form their own clusters, pushing the first
        # rep outside the LOOKBACK=100 window by the time we process
        # the last.
        analysis = []
        clip_embs = {}
        for i in range(102):
            unique = np.random.randn(512).astype(np.float32)
            unique /= np.linalg.norm(unique)
            # First and last share the target embedding.
            clip_embs[i] = emb if (i == 0 or i == 101) else unique
            analysis.append(
                {
                    "filepath": f"/p_{i}.jpg",
                    "id": i,
                    # All within the time window so time alone wouldn't
                    # block; only LOOKBACK separates them in pass 1.
                    "date": f"2024-01-01T00:00:{i:02d}",
                    "aggregate_score": 0.5,
                }
            )
        result = semantic_deduplicate(
            analysis,
            clip_embs,
            threshold=0.9,
            config={"time_window_seconds": 1000},
        )
        # Even though pass 1 misses (last is 101 clusters away from
        # first), pass 2 must still merge the two matching embeddings.
        # The two matched ones share a cluster — total reps = 1 (merged)
        # + 100 unique = 101.
        assert len(result) == 101, (
            f"pass 2 should merge the two identical-embedding photos even "
            f"when pass 1 missed them past LOOKBACK; got {len(result)}"
        )
        # The first photo is the merged rep (higher in sorted-by-id ties
        # not guaranteed, but both have the same score so pass 2 picks
        # one — assert the cluster size is 2 somewhere).
        merged = [r for r in result if r.get("cluster_size", 1) == 2]
        assert len(merged) == 1, "exactly one cluster should have size 2"

    def test_malformed_date_does_not_crash_pass_1(self):
        """Pass 1 pre-parses dates to float timestamps. Items with
        unparseable dates must not break the run — they should fail
        the time-window check naturally and either start their own
        cluster or get caught by pass 2."""
        analysis = [
            {
                "filepath": "/a.jpg",
                "id": 1,
                "date": "2024-01-01T12:00:00",
                "aggregate_score": 0.5,
            },
            {
                "filepath": "/b.jpg",
                "id": 2,
                "date": "not-a-date",  # ← malformed
                "aggregate_score": 0.5,
            },
        ]
        emb = np.random.randn(512).astype(np.float32)
        emb = emb / np.linalg.norm(emb)
        clip_embs = {1: emb, 2: emb.copy()}
        # Should not raise; pass 2 will still merge them since embeddings
        # match.
        result = semantic_deduplicate(analysis, clip_embs, threshold=0.9)
        assert len(result) == 1, "identical embeddings should merge via pass 2"

    def test_pass_2_scales_to_thousands(self):
        """Regression for the O(N^2) Python loop in pass 2 that ate ~10s
        on a 3,500-photo demo library. Vectorized rep matching brings it
        well under a second. Threshold is loose (3s) so the test isn't
        flaky on slow CI workers; the actual speedup is ~80x."""
        import time

        rng = np.random.default_rng(seed=12345)
        N = 3000
        D = 512
        analysis = []
        clip_embs = {}
        for i in range(N):
            v = rng.normal(size=D).astype(np.float32)
            v /= np.linalg.norm(v)
            clip_embs[i] = v
            # Spread dates out far enough that pass 1 doesn't merge
            # anything — forces pass 2 to do most of the work.
            day = (i // 100) % 28 + 1
            month = (i // 2800) % 12 + 1
            analysis.append(
                {
                    "id": i,
                    "filepath": f"/p/{i:05d}.jpg",
                    "date": f"2024-{month:02d}-{day:02d}T12:00:{(i % 60):02d}",
                    "aggregate_score": float(rng.random()),
                }
            )

        t0 = time.perf_counter()
        result = semantic_deduplicate(analysis, clip_embs, threshold=0.9)
        elapsed = time.perf_counter() - t0
        assert elapsed < 3.0, (
            f"semantic_deduplicate on {N} photos must complete in <3s "
            f"(took {elapsed:.2f}s) — pass 2 has regressed to O(N^2) Python loop"
        )
        # Sanity: we get a reasonable number of representatives back.
        assert 0 < len(result) <= N

    def test_global_pass_catches_cross_time_dupes(self):
        # Photos at different times but same embedding
        analysis = [
            {
                "filepath": f"/photo_{i}.jpg",
                "id": i + 1,
                "date": f"2024-0{i + 1}-01T12:00:00",
                "date_day": f"2024-0{i + 1}-01",
                "date_month": f"2024-0{i + 1}",
                "aggregate_score": 0.5 + i * 0.1,
            }
            for i in range(3)
        ]
        emb = np.random.randn(512).astype(np.float32)
        emb = emb / np.linalg.norm(emb)
        clip_embs = {1: emb, 2: emb.copy(), 3: emb.copy()}

        # Pass 1 won't catch them (different time windows)
        # Pass 2 should catch them (global dedup)
        result = semantic_deduplicate(analysis, clip_embs, threshold=0.9)
        assert len(result) == 1

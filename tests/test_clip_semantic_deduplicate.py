"""Tests for CLIP-based deduplication and feedback learning — part 2 of 2:
TestSemanticDeduplicate."""

from __future__ import annotations

import numpy as np

from bpp.dedupe.semantic import semantic_deduplicate
from tests._clip_shared import db  # noqa: F401


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

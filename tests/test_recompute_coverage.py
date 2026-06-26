"""Comprehensive tests for recompute.py and composition.py."""

from __future__ import annotations

import numpy as np
import pytest

from bpp.config import DEFAULTS
from bpp.scoring.composition import score_composition
from bpp.web.recompute import RecomputeOptions, optimize, recompute

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_analysis(n=10):
    items = []
    for i in range(n):
        items.append(
            {
                "filepath": f"/tmp/test_photos/img_{i:03d}.jpg",
                "date": f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}T12:00:00",
                "date_day": f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}",
                "date_month": f"2024-{(i % 12) + 1:02d}",
                "file_size": 1024 * (i + 1),
                "file_mtime": 1700000000.0 + i,
                "blur_raw": 100.0 + i * 50,
                "blur_score": i / max(n - 1, 1),
                "exposure_score": 0.5 + (i % 3) * 0.15,
                "face_score": 0.3 + (i % 4) * 0.1,
                "face_count": i % 3,
                "largest_face_ratio": 0.05,
                "face_center_dist": 0.3,
                "composition_score": 0.4 + (i % 5) * 0.1,
                "aggregate_score": 0.3 + i * 0.05,
            }
        )
    return items


def _default_config():
    return dict(DEFAULTS)


def _make_image(h=300, w=400):
    """Create a dummy numpy image (h, w, 3) with uniform grey pixels."""
    return np.full((h, w, 3), 128, dtype=np.uint8)


# ===========================================================================
# Recompute tests
# ===========================================================================


class TestRecomputeBasic:
    """Basic recompute() pipeline behaviour."""

    def test_basic_recompute_returns_expected_keys(self):
        """recompute() result contains all expected top-level keys."""
        analysis = _make_analysis(5)
        opts = RecomputeOptions(analysis=analysis, config=_default_config(), k=3)
        result = recompute(opts)

        assert "photos" in result
        assert "selected_paths" in result
        assert "score_map" in result
        assert "stats" in result
        assert result["stats"]["total"] == 5
        assert result["stats"]["total_selected"] <= 3

    def test_does_not_mutate_original_analysis(self):
        """recompute() must deep-copy; original list must stay unchanged."""
        analysis = _make_analysis(5)
        original_scores = [item["aggregate_score"] for item in analysis]
        opts = RecomputeOptions(analysis=analysis, config=_default_config(), k=3)
        recompute(opts)

        for orig, item in zip(original_scores, analysis, strict=True):
            assert item["aggregate_score"] == orig


class TestRecomputeFaceSelection:
    """Face selection boost logic."""

    def test_face_boost_increases_scores(self):
        """Photos matching selected faces should have boosted aggregate_score."""
        analysis = _make_analysis(5)
        config = _default_config()
        config["face_selection_boost"] = 0.15

        # Cluster map: img_000 has cluster 1, img_001 has cluster 2
        face_cluster_map = {
            "/tmp/test_photos/img_000.jpg": [1],
            "/tmp/test_photos/img_001.jpg": [2],
            "/tmp/test_photos/img_002.jpg": [1, 2],
        }

        # Baseline: no face selection
        opts_base = RecomputeOptions(analysis=analysis, config=config, k=5)
        result_base = recompute(opts_base)
        base_scores = result_base["score_map"]

        # With face selection on cluster 1
        opts_face = RecomputeOptions(
            analysis=analysis,
            config=config,
            k=5,
            selected_faces=[1],
            face_cluster_map=face_cluster_map,
        )
        result_face = recompute(opts_face)
        face_scores = result_face["score_map"]

        # img_000 has cluster 1 -> should be boosted
        p0 = "/tmp/test_photos/img_000.jpg"
        assert face_scores[p0] > base_scores[p0]
        # img_002 also has cluster 1 -> should be boosted
        p2 = "/tmp/test_photos/img_002.jpg"
        assert face_scores[p2] > base_scores[p2]
        # img_001 only has cluster 2, no boost
        assert face_scores["/tmp/test_photos/img_001.jpg"] == pytest.approx(
            base_scores["/tmp/test_photos/img_001.jpg"]
        )

    def test_face_boost_multiple_overlaps_capped(self):
        """Face boost is capped at face_selection_boost * min(overlap, 3) / 3."""
        analysis = _make_analysis(3)
        config = _default_config()
        config["face_selection_boost"] = 0.30

        face_cluster_map = {
            "/tmp/test_photos/img_000.jpg": [1, 2, 3, 4],  # 4 overlaps, but capped at 3
        }

        opts = RecomputeOptions(
            analysis=analysis,
            config=config,
            k=3,
            selected_faces=[1, 2, 3, 4],
            face_cluster_map=face_cluster_map,
        )
        result = recompute(opts)
        # Boost should be 0.30 * min(4,3)/3 = 0.30 (full boost)
        # Verify score is capped at 1.0
        score = result["score_map"]["/tmp/test_photos/img_000.jpg"]
        assert score <= 1.0


class TestRecomputeSensitivePolicy:
    """Sensitive-photo pick-time policy (sensitive_in_picks: allow|exclude).

    Quality scoring is nudity-agnostic — sensitivity NEVER changes a
    photo's aggregate_score. It only gates auto-picks: "allow" (default)
    lets sensitive photos compete normally; "exclude" hard-filters them
    from the candidate pool. A force-included sensitive photo always
    survives (manual include wins). A photo is sensitive when
    sensitive_override=1 or nudity_score >= SENSITIVE_NUDITY_THRESHOLD
    (see is_sensitive_item)."""

    def test_scoring_is_nudity_agnostic(self):
        """nudity_score no longer changes aggregate_score (penalty removed)."""
        base = dict(DEFAULTS)
        clean = _make_analysis(1)
        nud = _make_analysis(1)
        nud[0]["nudity_score"] = 0.95  # would have been heavily penalized before

        p = "/tmp/test_photos/img_000.jpg"
        clean_score = recompute(RecomputeOptions(analysis=clean, config=base, k=1))["score_map"][p]
        nud_score = recompute(RecomputeOptions(analysis=nud, config=base, k=1))["score_map"][p]
        assert nud_score == pytest.approx(clean_score)

    def test_allow_mode_keeps_sensitive_in_picks(self):
        """Default 'allow' — a sensitive photo still competes for selection."""
        analysis = _make_analysis(4)
        analysis[0]["nudity_score"] = 0.95  # sensitive
        config = _default_config()  # sensitive_in_picks defaults to "allow"
        result = recompute(
            RecomputeOptions(analysis=analysis, config=config, k=4, skip_dedupe=True)
        )
        assert "/tmp/test_photos/img_000.jpg" in result["selected_paths"]

    def test_exclude_mode_filters_sensitive_from_picks(self):
        """'exclude' drops sensitive photos from the auto-pick pool."""
        analysis = _make_analysis(4)
        analysis[0]["nudity_score"] = 0.95  # sensitive
        config = _default_config()
        config["sensitive_in_picks"] = "exclude"
        result = recompute(
            RecomputeOptions(analysis=analysis, config=config, k=4, skip_dedupe=True)
        )
        sel = result["selected_paths"]
        assert "/tmp/test_photos/img_000.jpg" not in sel
        # Non-sensitive photos are unaffected.
        assert "/tmp/test_photos/img_001.jpg" in sel

    def test_exclude_mode_respects_override_flag(self):
        """sensitive_override wins over nudity_score for the exclude filter."""
        analysis = _make_analysis(2)
        analysis[0]["nudity_score"] = 0.0  # NudeNet says safe...
        analysis[0]["sensitive_override"] = 1  # ...but the user marked it sensitive
        config = _default_config()
        config["sensitive_in_picks"] = "exclude"
        result = recompute(
            RecomputeOptions(analysis=analysis, config=config, k=2, skip_dedupe=True)
        )
        assert "/tmp/test_photos/img_000.jpg" not in result["selected_paths"]

    def test_exclude_mode_force_include_overrides(self):
        """A force-included sensitive photo is kept even in exclude mode."""
        analysis = _make_analysis(2)
        analysis[0]["nudity_score"] = 0.95  # sensitive
        config = _default_config()
        config["sensitive_in_picks"] = "exclude"
        result = recompute(
            RecomputeOptions(
                analysis=analysis,
                config=config,
                k=2,
                skip_dedupe=True,
                force_include=["/tmp/test_photos/img_000.jpg"],
            )
        )
        assert "/tmp/test_photos/img_000.jpg" in result["selected_paths"]


class TestRecomputeSkipDedupe:
    """skip_dedupe mode."""

    def test_skip_dedupe_mode(self):
        """When skip_dedupe=True, dedup_mode should be 'skipped' and no photos removed."""
        analysis = _make_analysis(5)
        config = _default_config()

        opts = RecomputeOptions(analysis=analysis, config=config, k=5, skip_dedupe=True)
        result = recompute(opts)

        assert result["stats"]["dedup_mode"] == "skipped"
        assert result["stats"]["after_dedupe"] == result["stats"]["after_exclude"]


class TestRecomputeUnanalyzed:
    """Unanalyzed photos (blur_raw=None)."""

    def test_unanalyzed_photos_get_zero_score(self):
        """Photos with blur_raw=None should receive aggregate_score=0."""
        analysis = _make_analysis(5)
        # Make two photos truly unanalyzed: blur_raw=None and no aggregate_score
        analysis[0]["blur_raw"] = None
        analysis[0].pop("aggregate_score", None)
        analysis[1]["blur_raw"] = None
        analysis[1].pop("aggregate_score", None)

        opts = RecomputeOptions(analysis=analysis, config=_default_config(), k=5)
        result = recompute(opts)

        assert result["score_map"]["/tmp/test_photos/img_000.jpg"] == 0.0
        assert result["score_map"]["/tmp/test_photos/img_001.jpg"] == 0.0
        # Analyzed photos should have non-zero scores
        assert result["score_map"]["/tmp/test_photos/img_002.jpg"] > 0.0


class TestRecomputeCLIP:
    """CLIP semantic deduplication mode."""

    def test_clip_dedup_mode_set(self):
        """When clip_embeddings provided, dedup_mode should be 'clip'."""
        analysis = _make_analysis(4)
        config = _default_config()
        # Add 'id' field required for CLIP dedup lookup
        for i, item in enumerate(analysis):
            item["id"] = i

        # Create embeddings: items 0 and 1 are nearly identical
        emb_base = np.random.RandomState(42).randn(512).astype(np.float32)
        emb_base /= np.linalg.norm(emb_base)
        clip_embeddings = {
            0: emb_base,
            1: emb_base + np.random.RandomState(43).randn(512) * 0.001,  # nearly identical
            2: np.random.RandomState(44).randn(512).astype(np.float32),
            3: np.random.RandomState(45).randn(512).astype(np.float32),
        }
        # Normalize all
        for k in clip_embeddings:
            clip_embeddings[k] = clip_embeddings[k] / np.linalg.norm(clip_embeddings[k])

        opts = RecomputeOptions(
            analysis=analysis,
            config=config,
            k=4,
            clip_embeddings=clip_embeddings,
            clip_threshold=0.90,
        )
        result = recompute(opts)

        assert result["stats"]["dedup_mode"] == "clip"
        # Nearly identical items 0 and 1 should be deduped, so fewer candidates
        assert result["stats"]["after_dedupe"] < result["stats"]["after_exclude"]

    def test_clip_dedup_keeps_distinct_photos(self):
        """CLIP dedup should keep photos with very different embeddings."""
        analysis = _make_analysis(3)
        config = _default_config()
        for i, item in enumerate(analysis):
            item["id"] = i

        # All embeddings are very different (orthogonal-ish)
        rng = np.random.RandomState(100)
        clip_embeddings = {}
        for i in range(3):
            emb = rng.randn(512).astype(np.float32)
            emb /= np.linalg.norm(emb)
            clip_embeddings[i] = emb

        opts = RecomputeOptions(
            analysis=analysis,
            config=config,
            k=3,
            clip_embeddings=clip_embeddings,
            clip_threshold=0.95,
        )
        result = recompute(opts)

        assert result["stats"]["dedup_mode"] == "clip"
        # All photos are distinct, none should be deduped
        assert result["stats"]["after_dedupe"] == result["stats"]["after_exclude"]


class TestRecomputeForceIncludeExclude:
    """Force include and force exclude together."""

    def test_force_include_and_exclude_together(self):
        """Force-included photos appear in selection; force-excluded do not."""
        analysis = _make_analysis(10)
        config = _default_config()

        force_include = ["/tmp/test_photos/img_000.jpg"]
        force_exclude = ["/tmp/test_photos/img_009.jpg"]

        opts = RecomputeOptions(
            analysis=analysis,
            config=config,
            k=5,
            force_include=force_include,
            force_exclude=force_exclude,
            skip_dedupe=True,
        )
        result = recompute(opts)

        assert "/tmp/test_photos/img_000.jpg" in result["selected_paths"]
        assert "/tmp/test_photos/img_009.jpg" not in result["selected_paths"]
        # Force-excluded should be removed before dedupe
        assert result["stats"]["after_exclude"] == 9

    def test_force_include_reduces_k_budget(self):
        """Force-included photos reduce the remaining k budget for auto-selection."""
        analysis = _make_analysis(10)
        config = _default_config()

        force_include = [
            "/tmp/test_photos/img_000.jpg",
            "/tmp/test_photos/img_001.jpg",
        ]

        opts = RecomputeOptions(
            analysis=analysis,
            config=config,
            k=3,
            force_include=force_include,
            skip_dedupe=True,
        )
        result = recompute(opts)

        assert result["stats"]["force_included"] == 2
        # Total selected should be at most k=3
        assert result["stats"]["total_selected"] <= 3

    def test_force_include_survives_hash_dedup(self):
        """Force-included photos must NOT be removed by hash dedup."""
        analysis = _make_analysis(5)
        config = _default_config()
        config["hash_distance_threshold"] = 100  # very aggressive dedup

        # Make img_000 and img_001 identical hashes so one would be deduped
        analysis[0]["phash"] = 0xDEADBEEF
        analysis[0]["ahash"] = 0xCAFEBABE
        analysis[1]["phash"] = 0xDEADBEEF
        analysis[1]["ahash"] = 0xCAFEBABE

        # Force-include img_001 (the one that would lose to img_000 in dedup)
        force_include = ["/tmp/test_photos/img_001.jpg"]

        opts = RecomputeOptions(
            analysis=analysis,
            config=config,
            k=5,
            force_include=force_include,
        )
        result = recompute(opts)

        assert "/tmp/test_photos/img_001.jpg" in result["selected_paths"], (
            "Force-included photo was silently dropped by dedup"
        )
        assert result["stats"]["force_included"] == 1

    def test_force_include_survives_clip_dedup(self):
        """Force-included photos must NOT be removed by CLIP semantic dedup."""
        analysis = _make_analysis(4)
        config = _default_config()
        for i, item in enumerate(analysis):
            item["id"] = i

        # Make items 0 and 1 near-identical in CLIP space
        rng = np.random.RandomState(42)
        emb_base = rng.randn(512).astype(np.float32)
        emb_base /= np.linalg.norm(emb_base)
        clip_embeddings = {
            0: emb_base,
            1: emb_base + rng.randn(512) * 0.0001,  # nearly identical
            2: rng.randn(512).astype(np.float32),
            3: rng.randn(512).astype(np.float32),
        }
        for k in clip_embeddings:
            clip_embeddings[k] = clip_embeddings[k] / np.linalg.norm(clip_embeddings[k])

        # Force-include img_001 (the one CLIP dedup would drop)
        force_include = ["/tmp/test_photos/img_001.jpg"]

        opts = RecomputeOptions(
            analysis=analysis,
            config=config,
            k=4,
            force_include=force_include,
            clip_embeddings=clip_embeddings,
            clip_threshold=0.90,
        )
        result = recompute(opts)

        assert "/tmp/test_photos/img_001.jpg" in result["selected_paths"], (
            "Force-included photo was silently dropped by CLIP dedup"
        )
        assert result["stats"]["force_included"] == 1


# ===========================================================================
# Optimize tests
# ===========================================================================


class TestOptimize:
    """Tests for the optimize() weight sweep."""

    def test_basic_optimize_returns_expected_keys(self):
        """optimize() returns settings dict with expected weight keys."""
        analysis = _make_analysis(20)
        config = _default_config()

        opts = RecomputeOptions(analysis=analysis, config=config, k=5, skip_dedupe=True)
        result = optimize(opts)

        assert "settings" in result
        assert "breakdown" in result
        settings = result["settings"]
        assert "blur_weight" in settings
        assert "exposure_weight" in settings
        assert "composition_weight" in settings
        assert "face_weight" in settings
        assert "face_selection_boost" in settings

        breakdown = result["breakdown"]
        assert "avg_quality" in breakdown
        assert "composite_score" in breakdown
        assert "total_selected" in breakdown

    def test_optimize_without_faces_no_face_coverage(self):
        """Without faces, breakdown should not have face_coverage."""
        analysis = _make_analysis(15)
        config = _default_config()

        opts = RecomputeOptions(analysis=analysis, config=config, k=5, skip_dedupe=True)
        result = optimize(opts)

        assert "face_coverage" not in result["breakdown"]

    def test_optimize_with_faces(self):
        """With faces, breakdown should include face_coverage."""
        analysis = _make_analysis(15)
        config = _default_config()

        face_cluster_map = {}
        face_filepaths = set()
        for i, item in enumerate(analysis):
            fp = item["filepath"]
            if i % 3 == 0:
                face_cluster_map[fp] = [1]
                face_filepaths.add(fp)

        opts = RecomputeOptions(
            analysis=analysis,
            config=config,
            k=5,
            selected_faces=[1],
            face_cluster_map=face_cluster_map,
            skip_dedupe=True,
        )
        result = optimize(opts, face_filepaths=face_filepaths)

        assert "face_coverage" in result["breakdown"]
        assert "face_photos_selected" in result["breakdown"]
        assert result["breakdown"]["face_coverage"] >= 0.0

    def test_optimize_composite_quality_positive(self):
        """The composite score from optimize should be positive for valid data."""
        analysis = _make_analysis(20)
        config = _default_config()

        opts = RecomputeOptions(analysis=analysis, config=config, k=5, skip_dedupe=True)
        result = optimize(opts)

        assert result["breakdown"]["composite_score"] > 0.0


# ===========================================================================
# Composition scoring tests
# ===========================================================================


class TestCompositionNoFaces:
    """score_composition with no faces."""

    def test_no_faces_returns_neutral(self):
        """With no face_boxes, score should be 0.5."""
        img = _make_image(300, 400)
        assert score_composition(img, []) == pytest.approx(0.5)


class TestCompositionFaceCentered:
    """Face centered in the image."""

    def test_face_centered_high_score(self):
        """A face centered at (1/2, ~2/5) should score high (near rule-of-thirds)."""
        img = _make_image(300, 400)
        # Face at center: x=150, y=90 (center of face near 2/5 height), w=100, h=120
        # Face center: (200/400=0.5, 150/300=0.5) -- exactly at 1/2 for both
        face = (150, 90, 100, 120)  # center_x=200/400=0.5, center_y=150/300=0.5
        score = score_composition(img, [face])
        # Center aligns with 1/2 in both x and y thirds -> should be high
        assert score > 0.7


class TestCompositionRuleOfThirds:
    """Face placed at rule-of-thirds intersection."""

    def test_face_at_one_third(self):
        """A face at (1/3, 1/3) should score high."""
        h, w = 300, 400
        img = _make_image(h, w)
        # Target face center at (w/3, h/3) = (133.3, 100)
        face_w, face_h = 60, 80
        fx = int(w / 3 - face_w / 2)
        fy = int(h / 3 - face_h / 2)
        face = (fx, fy, face_w, face_h)
        score = score_composition(img, [face])
        assert score > 0.7


class TestCompositionCorner:
    """Face placed in extreme corner."""

    def test_face_at_corner_lower_score(self):
        """A face in the extreme bottom-right corner should score lower."""
        h, w = 300, 400
        img = _make_image(h, w)
        # Face in bottom-right corner
        face = (w - 40, h - 40, 30, 30)  # center near (385/400, 285/300) = (0.96, 0.95)
        score = score_composition(img, [face])
        # Far from any third -> low x_score and y_score
        assert score < 0.5


class TestCompositionHeadroom:
    """Face near top edge with low headroom."""

    def test_low_headroom_penalty(self):
        """A face very near the top edge should get a headroom penalty."""
        h, w = 300, 400
        img = _make_image(h, w)
        # Face touching top edge: fy=0 means face_top = 0/300 = 0.0
        # Put face center at x=1/2 to maximize x_score
        face_low_headroom = (150, 0, 100, 80)
        score_low = score_composition(img, [face_low_headroom])

        # Same face but with headroom: fy=60 -> face_top = 60/300 = 0.2
        face_good_headroom = (150, 60, 100, 80)
        score_good = score_composition(img, [face_good_headroom])

        # The one with no headroom should score lower
        assert score_low < score_good


class TestCompositionMultipleFaces:
    """Multiple faces: largest is used."""

    def test_uses_largest_face(self):
        """When multiple faces are present, the largest face determines score."""
        img = _make_image(300, 400)
        # Small face in corner (would give low score if used)
        small_face = (380, 270, 10, 10)
        # Large face at center (gives high score)
        large_face = (150, 90, 100, 120)

        score = score_composition(img, [small_face, large_face])
        # Should use large_face (area 12000 vs 100) -> high score
        assert score > 0.7


class TestCompositionEmptyImage:
    """Edge case: tiny image dimensions with a face box."""

    def test_tiny_image_with_face(self):
        """A 1x1 image with a face should not crash and return a valid score."""
        img = _make_image(1, 1)
        face = (0, 0, 1, 1)
        score = score_composition(img, [face])
        assert 0.0 <= score <= 1.0

    def test_small_image_with_face(self):
        """A small image with a face at centre should return valid score."""
        img = _make_image(10, 10)
        face = (2, 2, 6, 6)  # center at (5/10, 5/10) = (0.5, 0.5)
        score = score_composition(img, [face])
        assert 0.0 <= score <= 1.0
        # Center placement should yield decent score
        assert score > 0.5

"""TDD tests for CLIP-powered semantic search."""

from __future__ import annotations

import json
import os

import numpy as np
import pytest

from bpp.constants import CLIP_MODEL_NAME

# ---------------------------------------------------------------------------
# Tokenizer tests
# ---------------------------------------------------------------------------


class TestClipTokenizer:
    """Tests for the CLIP BPE tokenizer."""

    def test_tokenize_returns_correct_shape(self):
        from bpp.scoring.clip_tokenizer import tokenize

        tokens = tokenize("a photo of a cat")
        assert tokens.shape == (1, 77)
        assert tokens.dtype in (np.int32, np.int64)

    def test_tokenize_starts_with_sot(self):
        from bpp.scoring.clip_tokenizer import SOT_TOKEN, tokenize

        tokens = tokenize("hello")[0]
        assert tokens[0] == SOT_TOKEN

    def test_tokenize_has_eot_after_text(self):
        from bpp.scoring.clip_tokenizer import EOT_TOKEN, tokenize

        tokens = tokenize("hello")[0]
        # EOT should appear somewhere after SOT and text tokens
        eot_positions = np.where(tokens == EOT_TOKEN)[0]
        assert len(eot_positions) >= 1
        assert eot_positions[0] > 0  # Not at position 0

    def test_tokenize_pads_with_zeros(self):
        from bpp.scoring.clip_tokenizer import EOT_TOKEN, tokenize

        tokens = tokenize("hi")[0]
        eot_pos = np.where(tokens == EOT_TOKEN)[0][0]
        # Everything after EOT should be zero
        assert np.all(tokens[eot_pos + 1 :] == 0)

    def test_tokenize_different_texts_differ(self):
        from bpp.scoring.clip_tokenizer import tokenize

        t1 = tokenize("a cat")
        t2 = tokenize("a dog")
        assert not np.array_equal(t1, t2)

    def test_tokenize_empty_text(self):
        from bpp.scoring.clip_tokenizer import EOT_TOKEN, SOT_TOKEN, tokenize

        tokens = tokenize("")[0]
        assert tokens[0] == SOT_TOKEN
        assert tokens[1] == EOT_TOKEN
        assert np.all(tokens[2:] == 0)

    def test_tokenize_long_text_truncated(self):
        from bpp.scoring.clip_tokenizer import tokenize

        # 77 tokens max (including SOT and EOT), so long text gets truncated
        long_text = " ".join(["word"] * 200)
        tokens = tokenize(long_text)
        assert tokens.shape == (1, 77)


# ---------------------------------------------------------------------------
# Text embedding tests (mocked ONNX)
# ---------------------------------------------------------------------------


class TestTextEmbedding:
    """Tests for compute_text_embedding (mocked model)."""

    def test_text_embedding_returns_512d(self, monkeypatch):
        from bpp.scoring import clip_embed

        # Mock the text session to return a random 512-d vector
        fake_emb = np.random.randn(1, 512).astype(np.float32)

        class FakeSession:
            def get_inputs(self):
                class FakeInput:
                    name = "input_ids"

                return [FakeInput()]

            def get_outputs(self):
                class FakeOutput:
                    name = "embeddings"

                return [FakeOutput()]

            def run(self, _names, _inputs):
                return [fake_emb]

        monkeypatch.setattr(clip_embed, "_get_text_session", lambda: FakeSession())

        result = clip_embed.compute_text_embedding("a sunset over the ocean")
        assert result is not None
        assert result.shape == (512,)
        # Should be L2-normalized
        assert abs(np.linalg.norm(result) - 1.0) < 1e-5

    def test_text_embedding_none_on_error(self, monkeypatch):
        from bpp.scoring import clip_embed

        def _raise():
            raise RuntimeError("no model")

        monkeypatch.setattr(clip_embed, "_get_text_session", _raise)
        result = clip_embed.compute_text_embedding("anything")
        assert result is None


# ---------------------------------------------------------------------------
# Semantic search ranking tests
# ---------------------------------------------------------------------------


class TestSemanticSearchRanking:
    """Tests for ranking photos by CLIP text similarity."""

    def _make_embeddings(self):
        """Create fake embeddings: photo 1 is 'cat-like', photo 2 is 'dog-like'."""
        rng = np.random.RandomState(42)
        # Create a "cat" direction and a "dog" direction
        cat_dir = rng.randn(512).astype(np.float32)
        cat_dir /= np.linalg.norm(cat_dir)
        dog_dir = rng.randn(512).astype(np.float32)
        dog_dir /= np.linalg.norm(dog_dir)

        # Photo 1: close to cat_dir
        p1 = cat_dir + 0.1 * rng.randn(512).astype(np.float32)
        p1 /= np.linalg.norm(p1)
        # Photo 2: close to dog_dir
        p2 = dog_dir + 0.1 * rng.randn(512).astype(np.float32)
        p2 /= np.linalg.norm(p2)
        # Photo 3: random
        p3 = rng.randn(512).astype(np.float32)
        p3 /= np.linalg.norm(p3)

        return {1: p1, 2: p2, 3: p3}, cat_dir, dog_dir

    def test_rank_by_similarity(self):
        from bpp.scoring.clip_embed import cosine_similarity

        embeddings, cat_dir, _dog_dir = self._make_embeddings()

        # Rank by similarity to cat_dir
        scores = {pid: cosine_similarity(emb, cat_dir) for pid, emb in embeddings.items()}
        ranked = sorted(scores, key=scores.get, reverse=True)

        # Photo 1 (cat-like) should rank first
        assert ranked[0] == 1

    def test_rank_returns_top_k(self):
        embeddings, cat_dir, _dog_dir = self._make_embeddings()

        # Simulate top-2 selection
        scores = {pid: float(np.dot(emb, cat_dir)) for pid, emb in embeddings.items()}
        ranked = sorted(scores, key=scores.get, reverse=True)[:2]
        assert len(ranked) == 2


# ---------------------------------------------------------------------------
# Search API endpoint tests
# ---------------------------------------------------------------------------


def _make_analysis(n: int = 5) -> list[dict]:
    items = []
    for i in range(n):
        items.append(
            {
                "filepath": f"/tmp/test_photos/img_{i:03d}.jpg",
                "original_filename": f"img_{i:03d}.jpg",
                "date": f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}T12:00:00",
                "date_day": f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}",
                "date_month": f"2024-{(i % 12) + 1:02d}",
                "file_size": 1024 * (i + 1),
                "file_mtime": 1700000000.0 + i,
                "blur_raw": 100.0 + i * 50,
                "blur_score": i / max(n - 1, 1),
                "exposure_score": 0.5,
                "face_score": 0.3,
                "face_count": 0,
                "largest_face_ratio": 0.0,
                "face_center_dist": 0.0,
                "composition_score": 0.5,
                "aggregate_score": 0.3 + i * 0.1,
            }
        )
    return items


@pytest.fixture
def semantic_client(tmp_path, monkeypatch):
    """Flask test client with CLIP embeddings seeded in DB."""
    from bpp.db.connection import get_db
    from bpp.web.app import create_app

    workdir = str(tmp_path / "workdir")
    os.makedirs(workdir)

    analysis = _make_analysis(5)
    with open(os.path.join(workdir, "analysis.json"), "w") as f:
        json.dump(analysis, f)

    app = create_app(workdir=workdir)
    app.config["TESTING"] = True
    client = app.test_client()

    # Init photos in DB
    client.get("/api/v1/photos")

    # Seed CLIP embeddings for 3 photos
    db_path = os.path.join(workdir, "photopicker.db")
    conn = get_db(db_path)

    rng = np.random.RandomState(42)
    for i in range(3):
        photo = conn.execute(
            "SELECT id FROM photos WHERE filepath=?", (analysis[i]["filepath"],)
        ).fetchone()
        if photo:
            emb = rng.randn(512).astype(np.float32)
            emb /= np.linalg.norm(emb)
            conn.execute(
                "INSERT INTO clip_embeddings (photo_id, model_name, embedding) VALUES (?, ?, ?)",
                (photo[0], CLIP_MODEL_NAME, emb.tobytes()),
            )
    conn.commit()

    # Mock compute_text_embedding to return a known vector
    # (same direction as photo 0's embedding for deterministic ranking)
    photo0 = conn.execute(
        "SELECT id FROM photos WHERE filepath=?", (analysis[0]["filepath"],)
    ).fetchone()
    photo0_emb = np.frombuffer(
        conn.execute(
            "SELECT embedding FROM clip_embeddings WHERE photo_id=?", (photo0[0],)
        ).fetchone()[0],
        dtype=np.float32,
    ).copy()

    def fake_text_embed(text):
        # Return vector close to photo 0
        return photo0_emb.copy()

    monkeypatch.setattr("bpp.web.bp_search.compute_text_embedding", fake_text_embed)

    return client


class TestSemanticSearchAPI:
    """Tests for semantic CLIP search via /api/search."""

    def test_semantic_results_returned(self, semantic_client):
        resp = semantic_client.get("/api/v1/search?q=sunset+beach")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "semantic" in data
        assert len(data["semantic"]) > 0

    def test_semantic_results_have_similarity(self, semantic_client):
        resp = semantic_client.get("/api/v1/search?q=sunset+beach")
        data = resp.get_json()
        for item in data["semantic"]:
            assert "similarity" in item
            assert 0 <= item["similarity"] <= 1

    def test_semantic_results_ordered_by_similarity(self, semantic_client):
        resp = semantic_client.get("/api/v1/search?q=sunset+beach")
        data = resp.get_json()
        sims = [item["similarity"] for item in data["semantic"]]
        assert sims == sorted(sims, reverse=True)

    def test_semantic_results_have_photo_fields(self, semantic_client):
        resp = semantic_client.get("/api/v1/search?q=a+photo")
        data = resp.get_json()
        if data["semantic"]:
            item = data["semantic"][0]
            assert "filepath" in item
            assert "thumb_hash" in item

    def test_semantic_results_capped(self, semantic_client):
        resp = semantic_client.get("/api/v1/search?q=anything")
        data = resp.get_json()
        assert len(data["semantic"]) <= 20

    def test_existing_search_still_works(self, semantic_client):
        """Non-semantic search categories should still work."""
        resp = semantic_client.get("/api/v1/search?q=img_001")
        data = resp.get_json()
        assert "photos" in data
        assert "albums" in data

    def test_score_qualifier_skips_semantic(self, semantic_client):
        """Score keywords like 'great' should not trigger semantic search."""
        resp = semantic_client.get("/api/v1/search?q=great")
        data = resp.get_json()
        # Score qualifier returns early, no semantic results
        assert data.get("semantic", []) == [] or "semantic" not in data

    def test_semantic_search_no_embeddings(self, tmp_path, monkeypatch):
        """When no CLIP embeddings exist, semantic results should be empty."""
        from bpp.web.app import create_app

        workdir = str(tmp_path / "workdir2")
        os.makedirs(workdir)
        analysis = _make_analysis(3)
        with open(os.path.join(workdir, "analysis.json"), "w") as f:
            json.dump(analysis, f)

        app = create_app(workdir=workdir)
        app.config["TESTING"] = True
        client = app.test_client()
        client.get("/api/v1/photos")

        # No CLIP embeddings seeded, and mock text embedding
        monkeypatch.setattr(
            "bpp.web.bp_search.compute_text_embedding",
            lambda text: np.random.randn(512).astype(np.float32),
        )

        resp = client.get("/api/v1/search?q=sunset")
        data = resp.get_json()
        assert data.get("semantic", []) == []

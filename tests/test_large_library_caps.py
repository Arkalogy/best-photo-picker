"""Second-pass performance review (v0.1.3): the recluster endpoint honours
the face cap, blocked clustering and face assignment cast per chunk,
pass 2 of the semantic dedupe sizes its block by bytes, and the selection
diversity check is one vector op per candidate."""

from __future__ import annotations

import numpy as np
import pytest


def test_recluster_refuses_above_the_face_cap(tmp_path, monkeypatch):
    import bpp.db.face_queries as fq
    from bpp.db.photos import upsert_photo
    from bpp.web.app import create_app
    from bpp.web.state import get_ctx

    app = create_app(library_path=str(tmp_path / "lib"))
    app.config["TESTING"] = True
    monkeypatch.setattr(fq, "FACE_EMBEDDINGS_MAX_ROWS", 1)
    with app.app_context():
        ctx = get_ctx()
        conn = ctx.get_conn()
        for i in range(2):
            p = tmp_path / f"p{i}.jpg"
            p.write_bytes(b"x")
            pid = upsert_photo(conn, {"filepath": str(p)})
            conn.execute(
                "INSERT INTO face_embeddings (photo_id, face_index, embedding, cluster_id) "
                "VALUES (?, 0, ?, ?)",
                (pid, np.zeros(128, np.float32).tobytes(), i),
            )
        conn.commit()
    with app.test_client() as c:
        r = c.post("/api/v1/faces/recluster", json={"threshold": 0.6})
    assert r.status_code == 503, r.get_data(as_text=True)


def test_blocked_clustering_casts_only_a_chunk_at_a_time(monkeypatch):
    from bpp.scoring import face_cluster as fc

    rng = np.random.default_rng(2)
    matrix = rng.normal(size=(60, 8)).astype(np.float32)
    labels = rng.integers(0, 5, size=20)
    monkeypatch.setattr(fc, "_CHUNK_BUDGET_BYTES", 8 * 20 * 4)  # 4 rows per chunk
    sizes: list[int] = []
    real = fc._f64

    def spy(a):
        sizes.append(int(np.asarray(a).shape[0]))
        return real(a)

    monkeypatch.setattr(fc, "_f64", spy)
    tail = np.arange(20, 60)
    best, dist = fc._nearest_cluster_by_mean(matrix, tail, matrix[:20], labels, 5)
    assert len(best) == 40
    assert max(sizes) <= 20, f"a cast bigger than the chunk or the members: {sizes}"
    for i, row in enumerate(tail):
        means = [
            np.linalg.norm(matrix[:20][labels == k].astype(np.float64) - matrix[row], axis=1).mean()
            for k in range(5)
        ]
        assert best[i] == int(np.argmin(means)), f"row {row}"
        assert dist[i] == pytest.approx(min(means), rel=1e-5), f"row {row}"


def test_face_assignment_keeps_the_new_faces_in_float32(monkeypatch):
    from bpp.db import face_identity_remap as fir
    from bpp.scoring import face_cluster as fc

    rng = np.random.default_rng(4)
    centers = rng.normal(size=(3, 8)) * 5
    assigned = [
        (
            f"/a{c}_{k}.jpg",
            c * 10 + k,
            0,
            (centers[c] + rng.normal(size=8) * 0.1).astype(np.float32),
            c,
            80,
            0.5,
        )
        for c in range(3)
        for k in range(3)
    ]
    unassigned = [
        (
            f"/u{i}.jpg",
            100 + i,
            0,
            (centers[i % 3] + rng.normal(size=8) * 0.1).astype(np.float32),
            80,
            None,
        )
        for i in range(30)
    ]
    sizes: list[int] = []
    real = fc._f64
    monkeypatch.setattr(fc, "_f64", lambda a: sizes.append(int(np.asarray(a).shape[0])) or real(a))
    monkeypatch.setattr(fc, "_CHUNK_BUDGET_BYTES", 8 * 3 * 5)  # 5 rows per chunk
    labels = fir._assign_new_faces(assigned, unassigned, 0.8)
    assert labels == [i % 3 for i in range(30)]
    assert max(sizes) <= 5, sizes


def test_pass_two_block_is_bounded_by_bytes():
    from bpp.dedupe import semantic as sem

    assert sem._pass2_block_rows(0) == sem._PASS2_BLOCK
    assert sem._pass2_block_rows(100) == sem._PASS2_BLOCK
    rows = sem._pass2_block_rows(200_000)
    assert 1 <= rows < sem._PASS2_BLOCK
    assert rows * 200_000 * 4 <= sem._PASS2_BLOCK_BYTES


class _Reference:
    """The per-accepted-photo loop this review replaces."""

    def __init__(self, threshold, clip):
        from bpp.dedupe.phash import dual_hash_distance, hamming_distance

        self.t, self.clip, self.acc, self.hashes = threshold, clip, [], []
        self.dual, self.ham = dual_hash_distance, hamming_distance

    def too_similar(self, item):
        emb = self.clip.get(item.get("id"))
        if emb is not None and any(float(np.dot(emb, a)) >= self.t for a in self.acc):
            return True
        ph, ah = item.get("phash"), item.get("ahash")
        if ph is not None:
            for aph, aah in self.hashes:
                if aph is None:
                    continue
                d = (
                    self.dual(ph, ah, aph, aah)
                    if (aah is not None and ah is not None)
                    else self.ham(ph, aph)
                )
                if d <= 16:
                    return True
        return False

    def accept(self, item):
        emb = self.clip.get(item.get("id"))
        if emb is not None:
            self.acc.append(emb)
        self.hashes.append((item.get("phash"), item.get("ahash")))


def test_vectorized_diversity_matches_the_loop():
    from bpp.selection.diversity import DiversityTracker

    rng = np.random.default_rng(9)
    scenes = rng.normal(size=(20, 16))
    clip = {}
    items = []
    for i in range(300):
        item = {"id": i, "date_day": f"2024-01-{(i % 28) + 1:02d}", "date_month": "2024-01"}
        if i % 7:
            v = scenes[i % 20] + rng.normal(size=16) * (0.05 if i % 3 else 3.0)
            clip[i] = (v / np.linalg.norm(v)).astype(np.float32)
        if i % 5:
            base = int(rng.integers(0, 2**63))
            item["phash"] = base ^ int(rng.integers(0, 2 ** (i % 20 + 1)))
            item["ahash"] = None if i % 4 == 0 else base ^ int(rng.integers(0, 2 ** (i % 12 + 1)))
        items.append(item)
    ours = DiversityTracker(max_per_day=1000, similarity_threshold=0.9, clip_embeddings=clip)
    ref = _Reference(0.9, clip)
    for item in items:
        a, b = ours.is_too_similar(item), ref.too_similar(item)
        assert a == b, item["id"]
        if not a:
            ours.accept(item)
            ref.accept(item)


def test_nearest_centroid_chunk_counts_the_embedding_width():
    from bpp.scoring import face_cluster as fc

    cent = np.zeros((3, 512), np.float32)
    rows = fc._rows_per_chunk(max(len(cent), cent.shape[1]), len(cent))
    assert rows * 512 * 8 <= fc._CHUNK_BUDGET_BYTES


def test_hidden_files_are_not_photos(tmp_path):
    from bpp.io_scan import scan_images

    (tmp_path / "a.jpg").write_bytes(b"x")
    (tmp_path / ".a.k2j.trim.mov").write_bytes(b"x")
    (tmp_path / "._a.jpg").write_bytes(b"x")
    found = scan_images(str(tmp_path), recursive=True, extensions=["jpg", "mov"])
    assert found == [str(tmp_path / "a.jpg")]
    found = scan_images(str(tmp_path), recursive=False, extensions=["jpg", "mov"])
    assert found == [str(tmp_path / "a.jpg")]

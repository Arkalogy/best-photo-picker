"""Tests for face review flow — find_ambiguous_pairs helper and the
/api/faces/review-pairs/{next,verdict} endpoints.

Covers:
- Ambiguity band (threshold ± delta) filters correctly
- Hard-negative exclusion
- Pair ordering (ascending by distance) and limit
- Verdict round-trip: same → merge feedback row; different → hard negative
- After "different" verdict, pair no longer appears in /next
"""

from __future__ import annotations

import numpy as np
import pytest

from bpp.db.connection import get_db, init_db
from bpp.db.face_feedback import (
    find_ambiguous_pairs,
    get_face_feedback,
    get_hard_negatives,
    store_hard_negative,
)

# ────────────────────────── fixtures ──────────────────────────


@pytest.fixture
def conn(tmp_path):
    db_path = str(tmp_path / "test_face_review.db")
    init_db(db_path)
    return get_db(db_path)


@pytest.fixture
def client(tmp_path):
    from bpp.web.app import create_app

    d = str(tmp_path.resolve())
    # library_path=None keeps serve_mode=False, which skips
    # _start_file_health_checks. Otherwise its background _startup_scan
    # thread can race the seed: if it queries `photos` after the test
    # inserts fake-path rows, check_missing() flags them all
    # missing=1 → load_face_clusters filters them out → the handler
    # silently drops the pair (bp_faces_manage.py:849). Reproduces only
    # under the slower thread scheduling on Linux CI runners.
    app = create_app(workdir=d, library_path=None)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _make_photo(conn, filepath: str) -> int:
    cur = conn.execute(
        "INSERT INTO photos (filepath, original_filename, file_size, file_mtime)"
        " VALUES (?, ?, 1000, 1000000)",
        (filepath, filepath.rsplit("/", 1)[-1]),
    )
    conn.commit()
    return cur.lastrowid


def _add_cluster(
    conn,
    cluster_id: int,
    embedding: np.ndarray,
    *,
    n_faces: int = 1,
    photo_prefix: str = "/tmp/",
) -> list[int]:
    """Create N face_embeddings rows for a cluster, all centered on `embedding`.

    Returns the list of face_embedding IDs.
    """
    ids = []
    for i in range(n_faces):
        pid = _make_photo(conn, f"{photo_prefix}c{cluster_id}_f{i}.jpg")
        cur = conn.execute(
            "INSERT INTO face_embeddings"
            " (photo_id, face_index, embedding, cluster_id,"
            "  bbox_x, bbox_y, bbox_w, bbox_h)"
            " VALUES (?, 0, ?, ?, 10, 20, 50, 60)",
            (pid, embedding.astype(np.float32).tobytes(), cluster_id),
        )
        ids.append(cur.lastrowid)
    conn.commit()
    return ids


def _unit_vec(dim: int = 128, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(dim).astype(np.float32)
    return v / np.linalg.norm(v)


# ────────── find_ambiguous_pairs (unit) ──────────


def test_find_ambiguous_pairs_empty_db(conn):
    """No face embeddings → no pairs."""
    assert find_ambiguous_pairs(conn) == []


def test_find_ambiguous_pairs_single_cluster(conn):
    """One cluster → no pairs (need at least two to pair)."""
    _add_cluster(conn, 1, _unit_vec(seed=1))
    assert find_ambiguous_pairs(conn) == []


def test_find_ambiguous_pairs_returns_pair_under_ceiling(conn):
    """Pair within max_distance is returned."""
    a = _unit_vec(seed=1)
    b = a + 0.55 * _unit_vec(seed=2)  # distance ~0.55
    _add_cluster(conn, 1, a)
    _add_cluster(conn, 2, b)
    pairs = find_ambiguous_pairs(conn, max_distance=0.75)
    assert len(pairs) == 1
    assert pairs[0]["cluster_a"] == 1
    assert pairs[0]["cluster_b"] == 2


def test_find_ambiguous_pairs_excludes_above_ceiling(conn):
    """Pair above max_distance is excluded."""
    a = _unit_vec(seed=1)
    b = -a  # distance ~2.0
    _add_cluster(conn, 1, a)
    _add_cluster(conn, 2, b)
    assert find_ambiguous_pairs(conn, max_distance=0.75) == []


def test_find_ambiguous_pairs_excludes_hard_negatives(conn):
    """A hard-negative pair never appears, even if below the ceiling."""
    a = _unit_vec(seed=1)
    b = a + 0.55 * _unit_vec(seed=2)
    _add_cluster(conn, 1, a)
    _add_cluster(conn, 2, b)
    store_hard_negative(conn, 1, 2)
    assert find_ambiguous_pairs(conn, max_distance=0.75) == []


def test_find_ambiguous_pairs_excludes_same_verdicts(conn):
    """A pair answered 'same person' never reappears in later runs.

    Regression: only hard negatives were excluded, so a same-verdict
    pair was re-presented in every review session forever (live DB had
    the same pair answered 4x across runs)."""
    from bpp.db.face_feedback import store_face_feedback

    a = _unit_vec(seed=1)
    b = a + 0.55 * _unit_vec(seed=2)
    _add_cluster(conn, 1, a)
    _add_cluster(conn, 2, b)
    store_face_feedback(conn, "merge", cluster_id_a=1, cluster_id_b=2, distance=0.55)
    assert find_ambiguous_pairs(conn, max_distance=0.75) == []
    # Reversed storage order is matched too (normalized lookup).
    _add_cluster(conn, 3, a + 0.5 * _unit_vec(seed=3))
    store_face_feedback(conn, "merge", cluster_id_a=3, cluster_id_b=1, distance=0.5)
    remaining = find_ambiguous_pairs(conn, max_distance=0.75)
    assert (1, 3) not in {(p["cluster_a"], p["cluster_b"]) for p in remaining}


def test_find_ambiguous_pairs_undo_restores_same_verdict_pair(conn):
    """Undoing a 'same' verdict puts the pair back in rotation."""
    from bpp.db.face_feedback import store_face_feedback, undo_last_pair_feedback

    a = _unit_vec(seed=1)
    b = a + 0.55 * _unit_vec(seed=2)
    _add_cluster(conn, 1, a)
    _add_cluster(conn, 2, b)
    store_face_feedback(conn, "merge", cluster_id_a=1, cluster_id_b=2, distance=0.55)
    assert find_ambiguous_pairs(conn, max_distance=0.75) == []
    assert undo_last_pair_feedback(conn, 1, 2)
    assert len(find_ambiguous_pairs(conn, max_distance=0.75)) == 1


def test_find_ambiguous_pairs_reassign_feedback_does_not_exclude(conn):
    """Only 'merge' rows settle a pair — reassign_in/out feedback
    (face moved between clusters) says nothing about the pair."""
    from bpp.db.face_feedback import store_face_feedback

    a = _unit_vec(seed=1)
    b = a + 0.55 * _unit_vec(seed=2)
    _add_cluster(conn, 1, a)
    _add_cluster(conn, 2, b)
    store_face_feedback(conn, "reassign_in", cluster_id_a=1, cluster_id_b=2, distance=0.55)
    assert len(find_ambiguous_pairs(conn, max_distance=0.75)) == 1


def test_find_ambiguous_pairs_orders_by_distance_asc(conn):
    """Closer pairs come first."""
    a = _unit_vec(seed=1)
    close = a + 0.45 * _unit_vec(seed=2)
    far = a + 0.65 * _unit_vec(seed=3)
    _add_cluster(conn, 1, a)
    _add_cluster(conn, 2, close)
    _add_cluster(conn, 3, far)
    pairs = find_ambiguous_pairs(conn, max_distance=0.75)
    assert len(pairs) >= 2
    # First pair must be the 1-2 (closer)
    assert (pairs[0]["cluster_a"], pairs[0]["cluster_b"]) == (1, 2)
    assert pairs[0]["distance"] < pairs[1]["distance"]


def test_find_ambiguous_pairs_respects_limit(conn):
    """Caps result count."""
    base = _unit_vec(seed=0)
    for i in range(5):
        _add_cluster(conn, i + 1, base + 0.45 * _unit_vec(seed=i + 10))
    pairs = find_ambiguous_pairs(conn, max_distance=0.75, limit=3)
    assert len(pairs) <= 3


def test_find_ambiguous_pairs_normalized_ordering(conn):
    """Pair always returned as (min_id, max_id)."""
    a = _unit_vec(seed=1)
    b = a + 0.55 * _unit_vec(seed=2)
    _add_cluster(conn, 7, a)
    _add_cluster(conn, 3, b)
    pairs = find_ambiguous_pairs(conn)
    assert len(pairs) == 1
    assert pairs[0]["cluster_a"] < pairs[0]["cluster_b"]


def test_count_ambiguous_pairs_matches_find(conn):
    """count_ambiguous_pairs returns the same total as find_ambiguous_pairs."""
    from bpp.db.face_feedback import count_ambiguous_pairs

    a = _unit_vec(seed=1)
    _add_cluster(conn, 1, a)
    _add_cluster(conn, 2, a + 0.5 * _unit_vec(seed=2))
    _add_cluster(conn, 3, a + 0.6 * _unit_vec(seed=3))
    assert count_ambiguous_pairs(conn) == len(find_ambiguous_pairs(conn))


# ────────── /api/faces/review-pairs/next + /verdict (integration) ──────────


def _seed_library(ctx, *, with_pair: bool = True):
    """Seed a library with clusters 1 and 2 under the ambiguity ceiling (0.75)."""
    conn = ctx.get_conn()
    a = _unit_vec(seed=1)
    if with_pair:
        b = a + 0.55 * _unit_vec(seed=2)
        _add_cluster(conn, 1, a, n_faces=3)
        _add_cluster(conn, 2, b, n_faces=2)
    else:
        _add_cluster(conn, 1, a)


def test_review_pairs_next_returns_empty_when_no_ambiguous(client):
    """Empty library → empty pairs list with 200."""
    resp = client.get("/api/v1/faces/review-pairs/next")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["pairs"] == []


def test_review_pairs_next_returns_enriched_pair(client):
    """Seeded pair returns with cluster metadata + representative thumb."""
    ctx = client.application.extensions["bpp"]
    with client.application.app_context():
        _seed_library(ctx)

    resp = client.get("/api/v1/faces/review-pairs/next?limit=10")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["pairs"]) == 1
    pair = data["pairs"][0]
    for side in ("cluster_a", "cluster_b"):
        c = pair[side]
        assert "id" in c
        assert "name" in c
        assert "face_count" in c
        assert "representative" in c
        assert "thumb_hash" in c["representative"]


def test_verdict_same_records_merge_feedback(client):
    """verdict=same inserts a face_cluster_feedback row with action=merge."""
    ctx = client.application.extensions["bpp"]
    with client.application.app_context():
        _seed_library(ctx)
    resp = client.post(
        "/api/v1/faces/review-pairs/verdict",
        json={"cluster_a": 1, "cluster_b": 2, "verdict": "same"},
    )
    assert resp.status_code == 200
    feedback = get_face_feedback(ctx.get_conn())
    assert any(
        f["action"] == "merge" and {f["cluster_id_a"], f["cluster_id_b"]} == {1, 2}
        for f in feedback
    )


def test_verdict_same_merges_clusters(client):
    """verdict=same MERGES: absorbed cluster empties into the primary
    (more faces wins when neither is named) and the response carries an
    undo snapshot + refreshed albums."""
    ctx = client.application.extensions["bpp"]
    with client.application.app_context():
        _seed_library(ctx)  # cluster 1: 3 faces, cluster 2: 2 faces
    resp = client.post(
        "/api/v1/faces/review-pairs/verdict",
        json={"cluster_a": 1, "cluster_b": 2, "verdict": "same"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["merged"] is True
    assert data["primary_cluster_id"] == 1
    assert data["absorbed_cluster_id"] == 2
    assert len(data["undo"]["faces"]) == 2
    assert "albums" in data

    conn = ctx.get_conn()
    counts = dict(
        conn.execute(
            "SELECT cluster_id, COUNT(*) FROM face_embeddings GROUP BY cluster_id"
        ).fetchall()
    )
    assert counts.get(1) == 5, f"primary should hold all faces, got {counts}"
    assert 2 not in counts, f"absorbed cluster should be empty, got {counts}"


def test_verdict_same_primary_prefers_named_cluster(client):
    """A named cluster wins as primary even with fewer faces — a name is
    user investment the merge must not discard."""
    ctx = client.application.extensions["bpp"]
    with client.application.app_context():
        _seed_library(ctx)
    conn = ctx.get_conn()
    conn.execute(
        "INSERT INTO albums (name, album_type, rule_json)"
        " VALUES ('Rita', 'smart_person', '{\"cluster_id\": 2}')"
    )
    conn.commit()
    resp = client.post(
        "/api/v1/faces/review-pairs/verdict",
        json={"cluster_a": 1, "cluster_b": 2, "verdict": "same"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["primary_cluster_id"] == 2, "named cluster must absorb the unnamed one"
    assert data["absorbed_cluster_id"] == 1


def test_verdict_same_undo_reverses_the_merge(client):
    """Round-trip: verdict=same merges; POSTing the undo snapshot back
    restores faces, identities, and puts the pair back in /next."""
    ctx = client.application.extensions["bpp"]
    with client.application.app_context():
        _seed_library(ctx)
    conn = ctx.get_conn()
    # Primary faces carry an identity so the merge's propagation fills
    # the absorbed faces' NULLs — undo must restore those NULLs.
    conn.execute("UPDATE face_embeddings SET identity='Leo' WHERE cluster_id=1")
    conn.commit()

    verdict_resp = client.post(
        "/api/v1/faces/review-pairs/verdict",
        json={"cluster_a": 1, "cluster_b": 2, "verdict": "same"},
    )
    assert verdict_resp.status_code == 200
    undo_snapshot = verdict_resp.get_json()["undo"]

    undo_resp = client.post(
        "/api/v1/faces/review-pairs/verdict/undo",
        json={"cluster_a": 1, "cluster_b": 2, "verdict": "same", "undo": undo_snapshot},
    )
    assert undo_resp.status_code == 200
    assert undo_resp.get_json()["undone"] is True
    assert "albums" in undo_resp.get_json()

    counts = dict(
        conn.execute(
            "SELECT cluster_id, COUNT(*) FROM face_embeddings GROUP BY cluster_id"
        ).fetchall()
    )
    assert counts.get(1) == 3 and counts.get(2) == 2, f"split not restored: {counts}"
    identities = [
        r[0]
        for r in conn.execute("SELECT identity FROM face_embeddings WHERE cluster_id=2").fetchall()
    ]
    assert identities == [None, None], f"pre-merge identities not restored: {identities}"
    # Feedback row deleted → the pair is reviewable again.
    next_resp = client.get("/api/v1/faces/review-pairs/next")
    assert len(next_resp.get_json()["pairs"]) == 1


def test_verdict_same_undo_restores_absorbed_name(client):
    """If the absorbed cluster was named, undo restores the name on the
    re-created person album."""
    ctx = client.application.extensions["bpp"]
    with client.application.app_context():
        _seed_library(ctx)
    conn = ctx.get_conn()
    # Name BOTH so primary selection falls back to face count (cluster 1
    # wins) and the absorbed cluster (2) carries a name through undo.
    conn.execute(
        "INSERT INTO albums (name, album_type, rule_json)"
        " VALUES ('Leo', 'smart_person', '{\"cluster_id\": 1}')"
    )
    conn.execute(
        "INSERT INTO albums (name, album_type, rule_json)"
        " VALUES ('Rita', 'smart_person', '{\"cluster_id\": 2}')"
    )
    conn.commit()

    verdict_resp = client.post(
        "/api/v1/faces/review-pairs/verdict",
        json={"cluster_a": 1, "cluster_b": 2, "verdict": "same"},
    )
    data = verdict_resp.get_json()
    assert data["primary_cluster_id"] == 1
    assert data["undo"]["absorbed_name"] == "Rita"

    client.post(
        "/api/v1/faces/review-pairs/verdict/undo",
        json={"cluster_a": 1, "cluster_b": 2, "verdict": "same", "undo": data["undo"]},
    )
    row = conn.execute(
        "SELECT name FROM albums WHERE album_type='smart_person' AND smart_person_cluster_id=2"
    ).fetchone()
    assert row is not None and row[0] == "Rita", f"absorbed name lost: {row}"


def test_verdict_same_undo_name_survives_refresh_crash(client, monkeypatch):
    """The absorbed cluster's name is restored in the SAME transaction as
    the faces — a crash during the smart-album refresh must not leave the
    cluster restored but renamed back to 'Person N'."""
    ctx = client.application.extensions["bpp"]
    with client.application.app_context():
        _seed_library(ctx)
    conn = ctx.get_conn()
    conn.execute(
        "INSERT INTO albums (name, album_type, rule_json)"
        " VALUES ('Leo', 'smart_person', '{\"cluster_id\": 1}')"
    )
    conn.execute(
        "INSERT INTO albums (name, album_type, rule_json)"
        " VALUES ('Rita', 'smart_person', '{\"cluster_id\": 2}')"
    )
    conn.commit()
    verdict_resp = client.post(
        "/api/v1/faces/review-pairs/verdict",
        json={"cluster_a": 1, "cluster_b": 2, "verdict": "same"},
    )
    undo_snapshot = verdict_resp.get_json()["undo"]
    assert undo_snapshot["absorbed_name"] == "Rita"

    import bpp.db.smart_albums as sa

    def _boom(*args, **kwargs):
        raise RuntimeError("refresh crashed")

    monkeypatch.setattr(sa, "refresh_smart_albums", _boom)
    # TESTING=True re-raises instead of mapping to a 500 — either way the
    # refresh failure surfaces to the caller…
    with pytest.raises(RuntimeError, match="refresh crashed"):
        client.post(
            "/api/v1/faces/review-pairs/verdict/undo",
            json={"cluster_a": 1, "cluster_b": 2, "verdict": "same", "undo": undo_snapshot},
        )

    # …but faces AND the name were already committed atomically.
    counts = dict(
        conn.execute(
            "SELECT cluster_id, COUNT(*) FROM face_embeddings GROUP BY cluster_id"
        ).fetchall()
    )
    assert counts.get(2) == 2, f"faces not restored before crash: {counts}"
    row = conn.execute(
        "SELECT name FROM albums WHERE album_type='smart_person' AND smart_person_cluster_id=2"
    ).fetchone()
    assert row is not None and row[0] == "Rita", f"name lost to the crash window: {row}"


def test_verdict_different_records_hard_negative(client):
    """verdict=different inserts a face_hard_negatives row."""
    ctx = client.application.extensions["bpp"]
    with client.application.app_context():
        _seed_library(ctx)
    resp = client.post(
        "/api/v1/faces/review-pairs/verdict",
        json={"cluster_a": 1, "cluster_b": 2, "verdict": "different"},
    )
    assert resp.status_code == 200
    hn = get_hard_negatives(ctx.get_conn())
    assert any({h["cluster_id_a"], h["cluster_id_b"]} == {1, 2} for h in hn)


def test_verdict_different_hides_pair_from_next(client):
    """After verdict=different, /next no longer returns the pair."""
    ctx = client.application.extensions["bpp"]
    with client.application.app_context():
        _seed_library(ctx)
    client.post(
        "/api/v1/faces/review-pairs/verdict",
        json={"cluster_a": 1, "cluster_b": 2, "verdict": "different"},
    )
    resp = client.get("/api/v1/faces/review-pairs/next")
    assert resp.get_json()["pairs"] == []


def test_verdict_rejects_invalid_input(client):
    """Malformed body returns 400 without touching feedback tables."""
    for body, expected in [
        ({}, 400),
        ({"cluster_a": 1}, 400),
        ({"cluster_a": 1, "cluster_b": 1, "verdict": "same"}, 400),  # same IDs
        ({"cluster_a": -1, "cluster_b": 2, "verdict": "same"}, 400),
        ({"cluster_a": 1, "cluster_b": 2, "verdict": "maybe"}, 400),
    ]:
        resp = client.post("/api/v1/faces/review-pairs/verdict", json=body)
        assert resp.status_code == expected, f"body={body}"


def test_review_pairs_count_matches_next_total(client):
    """The /count endpoint matches the `total` field of /next."""
    ctx = client.application.extensions["bpp"]
    with client.application.app_context():
        _seed_library(ctx)
    count_resp = client.get("/api/v1/faces/review-pairs/count")
    next_resp = client.get("/api/v1/faces/review-pairs/next")
    assert count_resp.status_code == 200
    assert next_resp.status_code == 200
    assert count_resp.get_json()["count"] == next_resp.get_json()["total"]


def test_verdict_unknown_cluster_returns_404(client):
    """Cluster ID that doesn't exist → 404."""
    ctx = client.application.extensions["bpp"]
    with client.application.app_context():
        _seed_library(ctx)
    resp = client.post(
        "/api/v1/faces/review-pairs/verdict",
        json={"cluster_a": 1, "cluster_b": 999, "verdict": "same"},
    )
    assert resp.status_code == 404

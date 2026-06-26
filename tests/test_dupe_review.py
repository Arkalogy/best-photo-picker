"""TDD tests for duplicate review flow.

RED phase: tests define the expected API for duplicate group review
BEFORE implementation.

The duplicate review API should:
1. Return groups of photos with matching phashes
2. Sort groups by score difference (most useful to review first)
3. Each group has photos with scores for comparison
4. Support marking a group as reviewed
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


@pytest.fixture()
def client(tmp_path):
    """Create a test Flask client with seeded dupe data."""
    from bpp.web.app import create_app

    d = str(tmp_path.resolve())
    app = create_app(workdir=d, library_path=d)
    app.config["TESTING"] = True
    with app.test_client() as c:
        ctx = app.extensions["bpp"]
        conn = ctx.get_conn()
        _seed_dupe_data(conn, d)
        yield c


def _seed_dupe_data(conn: sqlite3.Connection, base_dir: str) -> None:
    """Insert test photos with known phash duplicates."""
    import os

    _COLS = (
        "filepath, original_filename, file_size, file_mtime,"
        " phash, aggregate_score, blur_score,"
        " exposure_score, face_score, composition_score, date, missing"
    )

    photos_dir = os.path.join(base_dir, "photos")
    os.makedirs(photos_dir, exist_ok=True)

    def _ins(name, phash, score, blur=0.5, exp=0.5, face=0.5, comp=0.5, date="2024-01-01"):
        fp = os.path.join(photos_dir, name)
        Path(fp).touch()
        conn.execute(
            f"INSERT INTO photos ({_COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,0)",
            (fp, name, 1000, 1000000, phash, score, blur, exp, face, comp, date),
        )

    # Group 1: two photos with same phash, different scores
    _ins("a1.jpg", 1000, 0.8, blur=0.7, exp=0.9, face=0.5, comp=0.6)
    _ins("a2.jpg", 1000, 0.6, blur=0.5, exp=0.8, face=0.4, comp=0.5)
    # Group 2: three photos with same phash, big score diff
    _ins("b0.jpg", 2000, 0.9, date="2024-02-01")
    _ins("b1.jpg", 2000, 0.3, date="2024-02-01")
    _ins("b2.jpg", 2000, 0.7, date="2024-02-01")
    # Group 3: two photos same phash, identical scores
    _ins("c1.jpg", 3000, 0.5, date="2024-03-01")
    _ins("c2.jpg", 3000, 0.5, date="2024-03-01")
    # Solo photo (no duplicate) — should NOT appear in groups
    _ins("solo.jpg", 9999, 0.7, date="2024-04-01")
    conn.commit()


# ── 1. Endpoint exists ──


def test_dupe_groups_endpoint_exists(client):
    """GET /api/duplicates/groups must return 200."""
    resp = client.get("/api/v1/duplicates/groups")
    assert resp.status_code == 200


# ── 2. Returns groups ──


def test_dupe_groups_returns_list(client):
    """Response must have a 'groups' list."""
    resp = client.get("/api/v1/duplicates/groups")
    data = resp.get_json()
    assert "groups" in data
    assert isinstance(data["groups"], list)


def test_dupe_groups_correct_count(client):
    """Should return 3 groups (matching our seeded data)."""
    resp = client.get("/api/v1/duplicates/groups")
    data = resp.get_json()
    assert len(data["groups"]) == 3


def test_dupe_groups_no_singletons(client):
    """Solo photos (no phash match) should not appear."""
    resp = client.get("/api/v1/duplicates/groups")
    data = resp.get_json()
    all_fps = []
    for g in data["groups"]:
        for p in g["photos"]:
            all_fps.append(p["filepath"])
    assert not any("solo.jpg" in fp for fp in all_fps)


# ── 3. Group structure ──


def test_dupe_group_has_photos(client):
    """Each group must have a 'photos' list with 2+ items."""
    resp = client.get("/api/v1/duplicates/groups")
    data = resp.get_json()
    for g in data["groups"]:
        assert "photos" in g
        assert len(g["photos"]) >= 2


def test_dupe_group_photo_has_scores(client):
    """Each photo in a group must have scoring fields."""
    resp = client.get("/api/v1/duplicates/groups")
    data = resp.get_json()
    assert len(data["groups"]) > 0, "No groups found"
    photo = data["groups"][0]["photos"][0]
    assert "filepath" in photo
    assert "aggregate_score" in photo
    assert "thumb_hash" in photo


def test_dupe_group_has_best_photo(client):
    """Each group must identify the best photo (highest aggregate_score)."""
    resp = client.get("/api/v1/duplicates/groups")
    data = resp.get_json()
    for g in data["groups"]:
        assert "best_filepath" in g
        scores = {p["filepath"]: p["aggregate_score"] for p in g["photos"]}
        best = max(scores, key=scores.get)
        assert g["best_filepath"] == best


# ── 4. Sorting ──


def test_dupe_groups_sorted_by_score_diff(client):
    """Groups should be sorted by score difference (largest first).

    Group 2 has scores 0.9/0.3/0.7 → diff=0.6 (most useful to review).
    Group 1 has 0.8/0.6 → diff=0.2.
    Group 3 has 0.5/0.5 → diff=0.0 (least useful).
    """
    resp = client.get("/api/v1/duplicates/groups")
    data = resp.get_json()
    diffs = []
    for g in data["groups"]:
        scores = [p["aggregate_score"] for p in g["photos"]]
        diffs.append(max(scores) - min(scores))
    # Should be descending
    assert diffs == sorted(diffs, reverse=True)


# ── 5. Metadata ──


def test_dupe_groups_total_count(client):
    """Response must include total group count."""
    resp = client.get("/api/v1/duplicates/groups")
    data = resp.get_json()
    assert data["total"] == 3

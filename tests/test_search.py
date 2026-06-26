"""Tests for the /api/search endpoint."""

from __future__ import annotations

import json
import os
import sqlite3

import pytest


def _make_analysis(n: int = 10) -> list[dict]:
    """Create synthetic analysis data for testing."""
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


def _setup_face_db(conn: sqlite3.Connection, analysis: list[dict]) -> None:
    """Seed face_embeddings for two clusters."""
    for i in range(3):
        photo = conn.execute(
            "SELECT id FROM photos WHERE filepath=?", (analysis[i]["filepath"],)
        ).fetchone()
        if photo:
            conn.execute(
                "INSERT OR REPLACE INTO face_embeddings "
                "(photo_id, face_index, embedding, cluster_id) "
                "VALUES (?, 0, X'00', 0)",
                (photo[0],),
            )
    for i in range(3, 5):
        photo = conn.execute(
            "SELECT id FROM photos WHERE filepath=?", (analysis[i]["filepath"],)
        ).fetchone()
        if photo:
            conn.execute(
                "INSERT OR REPLACE INTO face_embeddings "
                "(photo_id, face_index, embedding, cluster_id) "
                "VALUES (?, 0, X'00', 1)",
                (photo[0],),
            )
    conn.commit()


@pytest.fixture
def search_client(tmp_path):
    """Flask test client with photos, albums, and face clusters seeded."""
    from bpp.db.connection import get_db
    from bpp.db.smart_albums import refresh_smart_albums
    from bpp.web.app import create_app

    workdir = str(tmp_path / "workdir")
    os.makedirs(workdir)

    analysis = _make_analysis(10)
    with open(os.path.join(workdir, "analysis.json"), "w") as f:
        json.dump(analysis, f)

    app = create_app(workdir=workdir)
    app.config["TESTING"] = True
    client = app.test_client()

    # Trigger DB init + photo load
    client.get("/api/v1/photos")

    db_path = os.path.join(workdir, "photopicker.db")
    conn = get_db(db_path)

    # Seed face clusters and smart person albums
    _setup_face_db(conn, analysis)
    refresh_smart_albums(conn)

    # Rename person albums to have real names
    conn.execute(
        "UPDATE albums SET name='Alice' WHERE album_type='smart_person'"
        " AND rule_json LIKE '%\"cluster_id\": 0%'"
    )
    conn.execute(
        "UPDATE albums SET name='Bob' WHERE album_type='smart_person'"
        " AND rule_json LIKE '%\"cluster_id\": 1%'"
    )
    conn.commit()

    # Create a manual album
    client.post("/api/v1/albums", json={"name": "Vacation 2024"})

    return client


class TestSearchEndpoint:
    """Tests for GET /api/search?q=..."""

    def test_search_requires_query(self, search_client):
        resp = search_client.get("/api/v1/search")
        assert resp.status_code == 400

    def test_search_empty_query(self, search_client):
        resp = search_client.get("/api/v1/search?q=")
        assert resp.status_code == 400

    def test_search_by_filename(self, search_client):
        resp = search_client.get("/api/v1/search?q=img_003")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "photos" in data
        assert len(data["photos"]) >= 1
        assert any("img_003" in p["filepath"] for p in data["photos"])

    def test_search_by_filename_case_insensitive(self, search_client):
        resp = search_client.get("/api/v1/search?q=IMG_003")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["photos"]) >= 1

    def test_search_by_person_name(self, search_client):
        resp = search_client.get("/api/v1/search?q=Alice")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "people" in data
        assert len(data["people"]) >= 1
        assert data["people"][0]["name"] == "Alice"
        assert "album_id" in data["people"][0]

    def test_search_by_person_case_insensitive(self, search_client):
        resp = search_client.get("/api/v1/search?q=alice")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["people"]) >= 1

    def test_search_by_album_name(self, search_client):
        resp = search_client.get("/api/v1/search?q=Vacation")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "albums" in data
        assert len(data["albums"]) >= 1
        assert "Vacation" in data["albums"][0]["name"]
        assert "id" in data["albums"][0]

    def test_search_by_album_case_insensitive(self, search_client):
        resp = search_client.get("/api/v1/search?q=vacation")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["albums"]) >= 1

    def test_search_by_date_year(self, search_client):
        resp = search_client.get("/api/v1/search?q=2024")
        assert resp.status_code == 200
        data = resp.get_json()
        # Should match photos with date containing "2024"
        assert len(data["photos"]) >= 1

    def test_search_by_date_month_name(self, search_client):
        resp = search_client.get("/api/v1/search?q=January")
        assert resp.status_code == 200
        data = resp.get_json()
        # Photos with date_month 2024-01 should match
        assert "dates" in data
        assert len(data["dates"]) >= 1

    def test_search_no_results(self, search_client):
        resp = search_client.get("/api/v1/search?q=zzzznonexistent")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["photos"]) == 0
        assert len(data["albums"]) == 0
        assert len(data["people"]) == 0
        assert len(data["dates"]) == 0

    def test_search_by_score_great(self, search_client):
        resp = search_client.get("/api/v1/search?q=great")
        assert resp.status_code == 200
        data = resp.get_json()
        # "great" = aggregate_score >= 0.7
        for p in data["photos"]:
            assert p["aggregate_score"] >= 0.7

    def test_search_by_score_low(self, search_client):
        resp = search_client.get("/api/v1/search?q=low quality")
        assert resp.status_code == 200
        data = resp.get_json()
        # "low" = aggregate_score < 0.3
        for p in data["photos"]:
            assert p["aggregate_score"] < 0.3

    def test_search_results_limited(self, search_client):
        """Search should not return unlimited results."""
        resp = search_client.get("/api/v1/search?q=img")
        assert resp.status_code == 200
        data = resp.get_json()
        # Should cap results (photos should be limited)
        assert len(data["photos"]) <= 50

    def test_search_response_shape(self, search_client):
        """Verify the response has all expected category keys."""
        resp = search_client.get("/api/v1/search?q=test")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "photos" in data
        assert "albums" in data
        assert "people" in data
        assert "dates" in data

    def test_search_photo_result_has_thumb_hash(self, search_client):
        """Photo results should include thumb_hash for display."""
        resp = search_client.get("/api/v1/search?q=img_000")
        assert resp.status_code == 200
        data = resp.get_json()
        if data["photos"]:
            p = data["photos"][0]
            assert "thumb_hash" in p
            assert "filepath" in p
            assert "aggregate_score" in p

    def test_search_whitespace_trimmed(self, search_client):
        resp = search_client.get("/api/v1/search?q=  img_003  ")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["photos"]) >= 1

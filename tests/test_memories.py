"""Tests for memories / auto-stories feature."""

from __future__ import annotations

import pytest

from bpp.db.connection import init_db
from bpp.db.memories import (
    _cluster_events,
    _score_memory,
    _title_for_cluster,
    generate_memories,
    get_memory,
    list_memories,
)


@pytest.fixture()
def conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    c = init_db(db_path)
    yield c
    c.close()


def _insert_photo(conn, filepath, date, score=0.5, face_count=0):
    """Helper to insert a photo with date and score."""
    conn.execute(
        "INSERT INTO photos (filepath, original_filename, file_size, file_mtime, "
        "date, date_day, aggregate_score, face_count, missing, deleted_at, hidden_at) "
        "VALUES (?, ?, 100, 1.0, ?, ?, ?, ?, 0, NULL, NULL)",
        (filepath, filepath.split("/")[-1], date, date[:10] if date else None, score, face_count),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


class TestClusterEvents:
    """Test time-based event clustering."""

    def test_empty_photos(self):
        assert _cluster_events([]) == []

    def test_single_photo(self):
        photos = [{"id": 1, "date": "2024-06-15 10:00:00", "aggregate_score": 0.5}]
        clusters = _cluster_events(photos, gap_hours=4)
        assert len(clusters) == 1
        assert len(clusters[0]) == 1

    def test_photos_same_day(self):
        photos = [
            {"id": 1, "date": "2024-06-15 10:00:00", "aggregate_score": 0.5},
            {"id": 2, "date": "2024-06-15 11:00:00", "aggregate_score": 0.6},
            {"id": 3, "date": "2024-06-15 12:00:00", "aggregate_score": 0.7},
        ]
        clusters = _cluster_events(photos, gap_hours=4)
        assert len(clusters) == 1
        assert len(clusters[0]) == 3

    def test_two_events_same_day(self):
        photos = [
            {"id": 1, "date": "2024-06-15 08:00:00", "aggregate_score": 0.5},
            {"id": 2, "date": "2024-06-15 09:00:00", "aggregate_score": 0.6},
            # 6 hour gap
            {"id": 3, "date": "2024-06-15 15:00:00", "aggregate_score": 0.7},
            {"id": 4, "date": "2024-06-15 16:00:00", "aggregate_score": 0.8},
        ]
        clusters = _cluster_events(photos, gap_hours=4)
        assert len(clusters) == 2
        assert len(clusters[0]) == 2
        assert len(clusters[1]) == 2

    def test_multi_day_trip(self):
        """Photos across 3 days with short gaps should be one event."""
        photos = [
            {"id": 1, "date": "2024-06-15 10:00:00", "aggregate_score": 0.5},
            {"id": 2, "date": "2024-06-15 14:00:00", "aggregate_score": 0.6},
            {"id": 3, "date": "2024-06-15 18:00:00", "aggregate_score": 0.7},
            # Overnight gap = new event
            {"id": 4, "date": "2024-06-16 09:00:00", "aggregate_score": 0.5},
            {"id": 5, "date": "2024-06-16 12:00:00", "aggregate_score": 0.6},
        ]
        clusters = _cluster_events(photos, gap_hours=4)
        # 10->14 (4h, boundary), 14->18 (4h, boundary), 18->09 next day (15h gap) = split
        assert len(clusters) == 2

    def test_photos_without_dates_skipped(self):
        photos = [
            {"id": 1, "date": None, "aggregate_score": 0.5},
            {"id": 2, "date": "2024-06-15 10:00:00", "aggregate_score": 0.6},
        ]
        clusters = _cluster_events(photos, gap_hours=4)
        assert len(clusters) == 1
        assert clusters[0][0]["id"] == 2

    def test_custom_gap(self):
        photos = [
            {"id": 1, "date": "2024-06-15 10:00:00", "aggregate_score": 0.5},
            {"id": 2, "date": "2024-06-15 13:00:00", "aggregate_score": 0.6},
        ]
        # 3h gap, threshold=2h → split
        clusters = _cluster_events(photos, gap_hours=2)
        assert len(clusters) == 2
        # 3h gap, threshold=4h → same cluster
        clusters = _cluster_events(photos, gap_hours=4)
        assert len(clusters) == 1


class TestTitleForCluster:
    """Test title generation for memory clusters."""

    def test_single_day(self):
        photos = [
            {"date": "2024-06-15 10:00:00"},
            {"date": "2024-06-15 14:00:00"},
        ]
        title = _title_for_cluster(photos)
        assert "June" in title
        assert "15" in title
        assert "2024" in title

    def test_multi_day(self):
        photos = [
            {"date": "2024-06-15 10:00:00"},
            {"date": "2024-06-17 14:00:00"},
        ]
        title = _title_for_cluster(photos)
        assert "June" in title
        assert "2024" in title

    def test_cross_month(self):
        photos = [
            {"date": "2024-06-28 10:00:00"},
            {"date": "2024-07-02 14:00:00"},
        ]
        title = _title_for_cluster(photos)
        assert "Jun" in title or "June" in title or "Jul" in title

    def test_empty_cluster(self):
        title = _title_for_cluster([])
        assert title  # should return something


class TestScoreMemory:
    """Test memory scoring/ranking."""

    def test_more_photos_score_higher(self):
        small = [{"aggregate_score": 0.5} for _ in range(3)]
        large = [{"aggregate_score": 0.5} for _ in range(10)]
        assert _score_memory(large) > _score_memory(small)

    def test_higher_quality_scores_higher(self):
        low = [{"aggregate_score": 0.3} for _ in range(5)]
        high = [{"aggregate_score": 0.8} for _ in range(5)]
        assert _score_memory(high) > _score_memory(low)

    def test_faces_boost(self):
        no_faces = [{"aggregate_score": 0.5} for _ in range(5)]
        with_faces = [{"aggregate_score": 0.5, "face_count": 2} for _ in range(5)]
        assert _score_memory(with_faces) > _score_memory(no_faces)


class TestGenerateMemories:
    """Integration tests for memory generation."""

    def test_no_photos(self, conn):
        memories = generate_memories(conn)
        assert memories == []

    def test_single_event(self, conn):
        for i in range(5):
            _insert_photo(conn, f"/p/img{i}.jpg", f"2024-06-15 {10 + i}:00:00", 0.5 + i * 0.05)
        memories = generate_memories(conn)
        assert len(memories) >= 1
        m = memories[0]
        assert m["photo_count"] == 5
        assert m["title"]
        assert m["date_start"]
        assert m["date_end"]
        assert m["hero_photo_id"]

    def test_two_events(self, conn):
        # Event 1: June 15
        for i in range(5):
            _insert_photo(conn, f"/p/jun{i}.jpg", f"2024-06-15 {10 + i}:00:00", 0.6)
        # Event 2: July 20 (big gap)
        for i in range(5):
            _insert_photo(conn, f"/p/jul{i}.jpg", f"2024-07-20 {10 + i}:00:00", 0.7)
        memories = generate_memories(conn)
        assert len(memories) >= 2

    def test_small_events_filtered_out(self, conn):
        """Events with < 3 photos should be excluded."""
        # Only 2 photos — too small
        _insert_photo(conn, "/p/a.jpg", "2024-06-15 10:00:00")
        _insert_photo(conn, "/p/b.jpg", "2024-06-15 11:00:00")
        memories = generate_memories(conn)
        assert len(memories) == 0

    def test_deleted_photos_excluded(self, conn):
        for i in range(5):
            _insert_photo(conn, f"/p/img{i}.jpg", f"2024-06-15 {10 + i}:00:00", 0.5)
        # Delete all photos
        conn.execute("UPDATE photos SET deleted_at = datetime('now')")
        conn.commit()
        memories = generate_memories(conn)
        assert len(memories) == 0

    def test_hidden_photos_excluded(self, conn):
        for i in range(5):
            _insert_photo(conn, f"/p/img{i}.jpg", f"2024-06-15 {10 + i}:00:00", 0.5)
        conn.execute("UPDATE photos SET hidden_at = datetime('now')")
        conn.commit()
        memories = generate_memories(conn)
        assert len(memories) == 0

    def test_hero_is_highest_scored(self, conn):
        _insert_photo(conn, "/p/a.jpg", "2024-06-15 10:00:00", 0.3)
        _insert_photo(conn, "/p/b.jpg", "2024-06-15 11:00:00", 0.9)
        _insert_photo(conn, "/p/c.jpg", "2024-06-15 12:00:00", 0.5)
        _insert_photo(conn, "/p/d.jpg", "2024-06-15 13:00:00", 0.4)
        memories = generate_memories(conn)
        assert len(memories) >= 1
        hero_id = memories[0]["hero_photo_id"]
        hero_path = conn.execute("SELECT filepath FROM photos WHERE id=?", (hero_id,)).fetchone()[0]
        assert hero_path == "/p/b.jpg"

    def test_memories_sorted_by_date_desc(self, conn):
        for i in range(4):
            _insert_photo(conn, f"/p/jan{i}.jpg", f"2024-01-15 {10 + i}:00:00", 0.5)
        for i in range(4):
            _insert_photo(conn, f"/p/jun{i}.jpg", f"2024-06-15 {10 + i}:00:00", 0.6)
        memories = generate_memories(conn)
        assert len(memories) >= 2
        # Most recent first
        assert memories[0]["date_start"] > memories[1]["date_start"]


class TestMemoryPersistence:
    """Test DB storage and retrieval."""

    def test_list_memories(self, conn):
        for i in range(5):
            _insert_photo(conn, f"/p/img{i}.jpg", f"2024-06-15 {10 + i}:00:00", 0.5)
        generate_memories(conn)
        memories = list_memories(conn)
        assert len(memories) >= 1
        m = memories[0]
        assert "id" in m
        assert "title" in m
        assert "hero_photo_id" in m
        assert "photo_count" in m

    def test_get_memory_with_photos(self, conn):
        ids = []
        for i in range(5):
            pid = _insert_photo(conn, f"/p/img{i}.jpg", f"2024-06-15 {10 + i}:00:00", 0.5)
            ids.append(pid)
        generate_memories(conn)
        memories = list_memories(conn)
        assert len(memories) >= 1
        m = get_memory(conn, memories[0]["id"])
        assert m is not None
        assert m["photo_count"] == 5
        assert len(m["photo_ids"]) == 5

    def test_get_nonexistent_memory(self, conn):
        assert get_memory(conn, 999) is None

    def test_regenerate_replaces_old(self, conn):
        for i in range(5):
            _insert_photo(conn, f"/p/img{i}.jpg", f"2024-06-15 {10 + i}:00:00", 0.5)
        generate_memories(conn)
        first = list_memories(conn)
        generate_memories(conn)
        second = list_memories(conn)
        assert len(first) == len(second)

    def test_hero_hash_included(self, conn):
        """Memories should include hero photo hash for thumbnail."""
        for i in range(5):
            _insert_photo(conn, f"/p/img{i}.jpg", f"2024-06-15 {10 + i}:00:00", 0.5)
        # Add sha256 to one photo
        conn.execute("UPDATE photos SET sha256='abc123' WHERE filepath='/p/img2.jpg'")
        conn.commit()
        generate_memories(conn)
        memories = list_memories(conn)
        assert len(memories) >= 1
        # hero_hash may or may not be the one with sha256, but the field should exist
        assert "hero_hash" in memories[0]

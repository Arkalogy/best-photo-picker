"""Direct unit tests for smart_album_queries.py resolvers.

These functions answer "what photo IDs match this smart-album rule?"
given live DB state. They're pure read-only and easy to test in
isolation — the resolver test seeds a small set of photos with the
matching attributes, calls the resolver, and asserts the right IDs
come back.

This file backfills coverage on the resolvers extracted from
smart_albums.py in commit `458c750` (M11.c refactor). The original
tests stayed on the integration-level refresh path; the resolvers
themselves were under-tested.
"""

from __future__ import annotations

import numpy as np
import pytest

from bpp.db.connection import get_db, init_db
from bpp.db.photos import upsert_photo
from bpp.db.smart_album_queries import (
    _get_all_ids,
    _get_deleted_ids,
    _get_duplicates_ids,
    _get_edited_ids,
    _get_group_ids,
    _get_hidden_ids,
    _get_no_faces_ids,
    _get_person_ids,
    _get_pet_ids,
    _get_recent_ids,
    _get_score_ids,
    _get_screenshot_ids,
    _get_tag_ids,
    _get_time_ids,
    _get_unsorted_ids,
    _get_video_ids,
)


@pytest.fixture
def conn(tmp_path):
    db_path = str(tmp_path / "queries.db")
    init_db(db_path)
    return get_db(db_path)


def _add_photo(conn, tmp_path, name, **extra):
    f = tmp_path / name
    f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 32)
    photo = {"filepath": str(f)}
    photo.update(extra)
    return upsert_photo(conn, photo)


# ── _get_all_ids ─────────────────────────────────────────────────────────


class TestGetAllIds:
    def test_returns_all_active_photos(self, conn, tmp_path):
        a = _add_photo(conn, tmp_path, "a.jpg")
        b = _add_photo(conn, tmp_path, "b.jpg")
        c = _add_photo(conn, tmp_path, "c.jpg")
        assert sorted(_get_all_ids(conn, {})) == sorted([a, b, c])

    def test_excludes_deleted(self, conn, tmp_path):
        a = _add_photo(conn, tmp_path, "a.jpg")
        b = _add_photo(conn, tmp_path, "b.jpg")
        conn.execute("UPDATE photos SET deleted_at=datetime('now') WHERE id=?", (b,))
        conn.commit()
        result = _get_all_ids(conn, {})
        assert a in result and b not in result


# ── _get_time_ids ────────────────────────────────────────────────────────


class TestGetTimeIds:
    def test_filters_by_year(self, conn, tmp_path):
        a = _add_photo(conn, tmp_path, "a.jpg", date="2024-06-15T10:00:00")
        b = _add_photo(conn, tmp_path, "b.jpg", date="2023-06-15T10:00:00")
        result = _get_time_ids(conn, {"year": 2024})
        assert a in result and b not in result

    def test_invalid_year_returns_empty(self, conn):
        assert _get_time_ids(conn, {"year": "not-a-number"}) == []

    def test_empty_rule_returns_empty(self, conn):
        assert _get_time_ids(conn, {}) == []

    def test_invalid_days_returns_empty(self, conn):
        assert _get_time_ids(conn, {"days": "bad"}) == []


# ── _get_score_ids ───────────────────────────────────────────────────────


class TestGetScoreIds:
    def test_top_percent_returns_highest_scored(self, conn, tmp_path):
        ids = [_add_photo(conn, tmp_path, f"p{i}.jpg", aggregate_score=i * 0.1) for i in range(10)]
        result = _get_score_ids(conn, {"top_percent": 30})
        # 10 photos * 30% = 3 photos, the three highest
        assert len(result) == 3
        assert ids[9] in result and ids[8] in result and ids[7] in result

    def test_invalid_percent_defaults_to_10(self, conn, tmp_path):
        _add_photo(conn, tmp_path, "p.jpg", aggregate_score=0.5)
        # Shouldn't raise on garbage input
        result = _get_score_ids(conn, {"top_percent": "bad"})
        assert isinstance(result, list)

    def test_excludes_null_scores(self, conn, tmp_path):
        _add_photo(conn, tmp_path, "scored.jpg", aggregate_score=0.8)
        _add_photo(conn, tmp_path, "unscored.jpg")
        result = _get_score_ids(conn, {"top_percent": 100})
        assert len(result) == 1


# ── _get_unsorted_ids ────────────────────────────────────────────────────


class TestGetUnsortedIds:
    def test_returns_photos_not_in_manual_album(self, conn, tmp_path):
        from bpp.db.albums import add_photos_to_album, create_album

        a = _add_photo(conn, tmp_path, "in_album.jpg")
        b = _add_photo(conn, tmp_path, "no_album.jpg")
        album_id = create_album(conn, "Manual Album", album_type="manual")
        add_photos_to_album(conn, album_id, [a])
        result = _get_unsorted_ids(conn, {})
        assert b in result and a not in result


# ── _get_pet_ids ─────────────────────────────────────────────────────────


class TestGetPetIds:
    def test_filters_by_cluster_id(self, conn, tmp_path):
        a = _add_photo(conn, tmp_path, "pet1.jpg")
        b = _add_photo(conn, tmp_path, "pet2.jpg")
        c = _add_photo(conn, tmp_path, "no_pet.jpg")
        conn.execute(
            "INSERT INTO pet_detections (photo_id, detection_index, class, confidence, cluster_id) "
            "VALUES (?, 0, 'cat', 0.9, 5)",
            (a,),
        )
        conn.execute(
            "INSERT INTO pet_detections (photo_id, detection_index, class, confidence, cluster_id) "
            "VALUES (?, 0, 'dog', 0.8, 7)",
            (b,),
        )
        conn.commit()
        result = _get_pet_ids(conn, {"cluster_id": 5})
        assert a in result and b not in result and c not in result

    def test_legacy_class_fallback(self, conn, tmp_path):
        a = _add_photo(conn, tmp_path, "cat.jpg", has_cat=1)
        b = _add_photo(conn, tmp_path, "dog.jpg", has_dog=1)
        cats = _get_pet_ids(conn, {"pet_class": "cat"})
        dogs = _get_pet_ids(conn, {"pet_class": "dog"})
        assert a in cats and b not in cats
        assert b in dogs and a not in dogs

    def test_unknown_class_returns_empty(self, conn):
        assert _get_pet_ids(conn, {"pet_class": "ferret"}) == []

    def test_empty_rule_returns_empty(self, conn):
        assert _get_pet_ids(conn, {}) == []


# ── _get_group_ids ───────────────────────────────────────────────────────


class TestGetGroupIds:
    def test_empty_members_returns_empty(self, conn):
        assert _get_group_ids(conn, {}) == []
        assert _get_group_ids(conn, {"group_members": []}) == []


# ── _get_recent_ids ──────────────────────────────────────────────────────


class TestGetRecentIds:
    def test_invalid_days_defaults_to_seven(self, conn, tmp_path):
        # Just verify no exception on garbage input
        result = _get_recent_ids(conn, {"days": "bad"})
        assert isinstance(result, list)

    def test_returns_only_recent_photos(self, conn, tmp_path):
        # Photos default to current created_at, so all should be recent
        a = _add_photo(conn, tmp_path, "today.jpg")
        result = _get_recent_ids(conn, {"days": 7})
        assert a in result


# ── _get_hidden_ids ──────────────────────────────────────────────────────


class TestGetHiddenIds:
    def test_returns_hidden_not_deleted(self, conn, tmp_path):
        a = _add_photo(conn, tmp_path, "hidden.jpg")
        b = _add_photo(conn, tmp_path, "visible.jpg")
        c = _add_photo(conn, tmp_path, "hidden_and_deleted.jpg")
        conn.execute("UPDATE photos SET hidden_at=datetime('now') WHERE id=?", (a,))
        conn.execute(
            "UPDATE photos SET hidden_at=datetime('now'), deleted_at=datetime('now') WHERE id=?",
            (c,),
        )
        conn.commit()
        result = _get_hidden_ids(conn, {})
        assert a in result and b not in result and c not in result


# ── _get_person_ids ──────────────────────────────────────────────────────


class TestGetPersonIds:
    def test_returns_photos_with_cluster_face(self, conn, tmp_path):
        a = _add_photo(conn, tmp_path, "alice.jpg")
        b = _add_photo(conn, tmp_path, "bob.jpg")
        emb = np.zeros(128, dtype=np.float32).tobytes()
        conn.execute(
            "INSERT INTO face_embeddings (photo_id, face_index, embedding, cluster_id) "
            "VALUES (?, 0, ?, 1)",
            (a, emb),
        )
        conn.execute(
            "INSERT INTO face_embeddings (photo_id, face_index, embedding, cluster_id) "
            "VALUES (?, 0, ?, 2)",
            (b, emb),
        )
        conn.commit()
        result = _get_person_ids(conn, {"cluster_id": 1})
        assert a in result and b not in result

    def test_no_cluster_id_returns_empty(self, conn):
        assert _get_person_ids(conn, {}) == []


# ── _get_deleted_ids ─────────────────────────────────────────────────────


class TestGetDeletedIds:
    def test_returns_only_deleted(self, conn, tmp_path):
        a = _add_photo(conn, tmp_path, "live.jpg")
        b = _add_photo(conn, tmp_path, "trash.jpg")
        conn.execute("UPDATE photos SET deleted_at=datetime('now') WHERE id=?", (b,))
        conn.commit()
        result = _get_deleted_ids(conn, {})
        assert b in result and a not in result


# ── _get_video_ids ───────────────────────────────────────────────────────


class TestGetVideoIds:
    def test_returns_only_videos(self, conn, tmp_path):
        a = _add_photo(conn, tmp_path, "video.mp4", is_video=1)
        b = _add_photo(conn, tmp_path, "photo.jpg", is_video=0)
        result = _get_video_ids(conn, {})
        assert a in result and b not in result


# ── _get_screenshot_ids ──────────────────────────────────────────────────


class TestGetScreenshotIds:
    def test_matches_screenshot_naming_patterns(self, conn, tmp_path):
        a = _add_photo(conn, tmp_path, "shot1.jpg", original_filename="Screenshot 2024-01-01.png")
        b = _add_photo(conn, tmp_path, "shot2.jpg", original_filename="Screen Shot 2024-01-02.png")
        c = _add_photo(conn, tmp_path, "cap.jpg", original_filename="Capture123.jpg")
        d = _add_photo(conn, tmp_path, "regular.jpg", original_filename="IMG_1234.jpg")
        result = _get_screenshot_ids(conn, {})
        assert a in result and b in result and c in result and d not in result


# ── _get_duplicates_ids ──────────────────────────────────────────────────


class TestGetDuplicatesIds:
    def test_uses_cluster_size_when_available(self, conn, tmp_path):
        a = _add_photo(conn, tmp_path, "dup1.jpg", cluster_size=3)
        b = _add_photo(conn, tmp_path, "dup2.jpg", cluster_size=3)
        c = _add_photo(conn, tmp_path, "unique.jpg", cluster_size=1)
        result = _get_duplicates_ids(conn, {})
        assert a in result and b in result and c not in result

    def test_falls_back_to_phash_equality(self, conn, tmp_path):
        # All cluster_size=1 (pre-clustering state), but two share phash
        a = _add_photo(conn, tmp_path, "p1.jpg", phash="abc123")
        b = _add_photo(conn, tmp_path, "p2.jpg", phash="abc123")
        c = _add_photo(conn, tmp_path, "p3.jpg", phash="zzz999")
        result = _get_duplicates_ids(conn, {})
        assert a in result and b in result and c not in result


# ── _get_no_faces_ids ────────────────────────────────────────────────────


class TestGetNoFacesIds:
    def test_returns_face_count_zero(self, conn, tmp_path):
        a = _add_photo(conn, tmp_path, "empty.jpg", face_count=0)
        b = _add_photo(conn, tmp_path, "portrait.jpg", face_count=2)
        result = _get_no_faces_ids(conn, {})
        assert a in result and b not in result


# ── _get_edited_ids ──────────────────────────────────────────────────────


class TestGetEditedIds:
    def test_returns_edited_photos(self, conn, tmp_path):
        a = _add_photo(conn, tmp_path, "edited.jpg")
        b = _add_photo(conn, tmp_path, "untouched.jpg")
        conn.execute(
            "INSERT INTO photo_edits (photo_id, brightness, contrast) VALUES (?, 1.2, 1.1)",
            (a,),
        )
        conn.commit()
        result = _get_edited_ids(conn, {})
        assert a in result and b not in result


# ── _get_tag_ids ─────────────────────────────────────────────────────────


class TestGetTagIds:
    def test_returns_photos_with_tag(self, conn, tmp_path):
        a = _add_photo(conn, tmp_path, "tagged.jpg")
        b = _add_photo(conn, tmp_path, "untagged.jpg")
        # Create tag and tag the photo
        cur = conn.execute("INSERT INTO tags (name) VALUES ('vacation')")
        tag_id = cur.lastrowid
        conn.execute("INSERT INTO photo_tags (photo_id, tag_id) VALUES (?, ?)", (a, tag_id))
        conn.commit()
        result = _get_tag_ids(conn, {"tag_id": tag_id})
        assert a in result and b not in result

    def test_no_tag_id_returns_empty(self, conn):
        assert _get_tag_ids(conn, {}) == []

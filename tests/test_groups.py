"""Tests for face co-occurrence groups (db/groups.py)."""

from __future__ import annotations

import numpy as np

from bpp.db.connection import init_db
from bpp.db.groups import (
    compute_cooccurrence,
    detect_groups,
    get_group_photo_ids,
    has_group_data,
)
from bpp.db.photos import upsert_photo


def _make_photo(conn, tmp_path, name):
    f = tmp_path / name
    f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
    return upsert_photo(conn, {"filepath": str(f)})


def _add_face(conn, photo_id, face_index, cluster_id):
    """Insert a minimal face embedding for testing."""
    embedding = np.zeros(128, dtype=np.float32).tobytes()
    conn.execute(
        "INSERT OR REPLACE INTO face_embeddings "
        "(photo_id, face_index, embedding, cluster_id) "
        "VALUES (?, ?, ?, ?)",
        (photo_id, face_index, embedding, cluster_id),
    )
    conn.commit()


class TestComputeCooccurrence:
    def test_empty_db(self, tmp_path):
        conn = init_db(str(tmp_path / "test.db"))
        result = compute_cooccurrence(conn, min_photos=1)
        assert result == []
        conn.close()

    def test_single_face_per_photo(self, tmp_path):
        conn = init_db(str(tmp_path / "test.db"))
        p1 = _make_photo(conn, tmp_path, "a.jpg")
        p2 = _make_photo(conn, tmp_path, "b.jpg")
        _add_face(conn, p1, 0, 0)
        _add_face(conn, p2, 0, 1)
        # No co-occurrence: each photo has only one person
        result = compute_cooccurrence(conn, min_photos=1)
        assert result == []
        conn.close()

    def test_two_people_in_same_photos(self, tmp_path):
        conn = init_db(str(tmp_path / "test.db"))
        p1 = _make_photo(conn, tmp_path, "a.jpg")
        p2 = _make_photo(conn, tmp_path, "b.jpg")
        p3 = _make_photo(conn, tmp_path, "c.jpg")
        # Person 0 and Person 1 in photos 1 and 2
        _add_face(conn, p1, 0, 0)
        _add_face(conn, p1, 1, 1)
        _add_face(conn, p2, 0, 0)
        _add_face(conn, p2, 1, 1)
        # Photo 3 has only Person 0
        _add_face(conn, p3, 0, 0)

        result = compute_cooccurrence(conn, min_photos=2)
        assert len(result) == 1
        assert result[0] == (0, 1, 2)
        conn.close()

    def test_min_photos_filter(self, tmp_path):
        conn = init_db(str(tmp_path / "test.db"))
        p1 = _make_photo(conn, tmp_path, "a.jpg")
        _add_face(conn, p1, 0, 0)
        _add_face(conn, p1, 1, 1)
        # Only 1 shared photo — should be filtered by min_photos=2
        result = compute_cooccurrence(conn, min_photos=2)
        assert result == []
        # But visible with min_photos=1
        result = compute_cooccurrence(conn, min_photos=1)
        assert len(result) == 1
        conn.close()


class TestDetectGroups:
    def test_no_groups(self, tmp_path):
        conn = init_db(str(tmp_path / "test.db"))
        groups = detect_groups(conn, min_photos=3)
        assert groups == []
        conn.close()

    def test_pair_group(self, tmp_path):
        # 4 shared photos: both clusters clear the significance gate
        # (FACE_MIN_PHOTOS) and the pair co-occurs enough.
        conn = init_db(str(tmp_path / "test.db"))
        photos = [_make_photo(conn, tmp_path, f"{i}.jpg") for i in range(4)]
        for pid in photos:
            _add_face(conn, pid, 0, 0)
            _add_face(conn, pid, 1, 1)

        groups = detect_groups(conn, min_photos=3)
        assert len(groups) == 1
        assert sorted(groups[0]["members"]) == [0, 1]
        assert groups[0]["photo_count"] == 4
        conn.close()

    def test_trio_group(self, tmp_path):
        conn = init_db(str(tmp_path / "test.db"))
        photos = [_make_photo(conn, tmp_path, f"{i}.jpg") for i in range(4)]
        # Persons 0, 1, 2 all appear together in 4 photos
        for pid in photos:
            _add_face(conn, pid, 0, 0)
            _add_face(conn, pid, 1, 1)
            _add_face(conn, pid, 2, 2)

        groups = detect_groups(conn, min_photos=3)
        assert len(groups) == 1
        assert sorted(groups[0]["members"]) == [0, 1, 2]
        assert groups[0]["photo_count"] == 4
        conn.close()

    def test_separate_groups(self, tmp_path):
        conn = init_db(str(tmp_path / "test.db"))
        # Group 1: Person 0 and 1 in 4 photos
        p_group1 = [_make_photo(conn, tmp_path, f"g1_{i}.jpg") for i in range(4)]
        for pid in p_group1:
            _add_face(conn, pid, 0, 0)
            _add_face(conn, pid, 1, 1)

        # Group 2: Person 2 and 3 in 4 photos
        p_group2 = [_make_photo(conn, tmp_path, f"g2_{i}.jpg") for i in range(4)]
        for pid in p_group2:
            _add_face(conn, pid, 0, 2)
            _add_face(conn, pid, 1, 3)

        groups = detect_groups(conn, min_photos=3)
        assert len(groups) == 2
        member_sets = [frozenset(g["members"]) for g in groups]
        assert frozenset({0, 1}) in member_sets
        assert frozenset({2, 3}) in member_sets
        conn.close()

    def test_pair_resurfaces_when_clique_has_fewer_photos(self, tmp_path):
        """A pair swallowed by a larger clique comes back as its own group
        when it co-occurs in MORE photos than the clique (Leo & AZ share
        592 photos; the 5-person clique only 9)."""
        conn = init_db(str(tmp_path / "test.db"))
        # A+B in 5 photos alone…
        for i in range(5):
            pid = _make_photo(conn, tmp_path, f"ab_{i}.jpg")
            _add_face(conn, pid, 0, 0)
            _add_face(conn, pid, 1, 1)
        # …plus 3 photos with C too (every pair clears min_photos=3)…
        for i in range(3):
            pid = _make_photo(conn, tmp_path, f"abc_{i}.jpg")
            _add_face(conn, pid, 0, 0)
            _add_face(conn, pid, 1, 1)
            _add_face(conn, pid, 2, 2)
        # …and one solo C photo so C clears the significance gate (4 photos).
        pid = _make_photo(conn, tmp_path, "c_solo.jpg")
        _add_face(conn, pid, 0, 2)

        groups = detect_groups(conn, min_photos=3)
        as_sets = {frozenset(g["members"]): g["photo_count"] for g in groups}
        assert as_sets.get(frozenset({0, 1, 2})) == 3, f"clique missing: {as_sets}"
        assert as_sets.get(frozenset({0, 1})) == 8, f"swallowed pair not resurfaced: {as_sets}"
        # A+C / B+C share exactly the clique's 3 photos — stay suppressed.
        assert frozenset({0, 2}) not in as_sets
        assert frozenset({1, 2}) not in as_sets
        conn.close()

    def test_insignificant_fragments_form_no_groups(self, tmp_path):
        """Unnamed clusters below FACE_MIN_PHOTOS don't get group cards —
        they're unmerged fragments, not people (the 'Person 2 & Person 75'
        junk-card regression)."""
        conn = init_db(str(tmp_path / "test.db"))
        photos = [_make_photo(conn, tmp_path, f"{i}.jpg") for i in range(3)]
        for pid in photos:
            _add_face(conn, pid, 0, 0)  # 3 photos — below the gate
            _add_face(conn, pid, 1, 1)  # 3 photos — below the gate

        assert detect_groups(conn, min_photos=3) == []
        conn.close()

    def test_named_small_cluster_is_significant(self, tmp_path):
        """A user-named cluster counts even under FACE_MIN_PHOTOS; the
        auto 'Person N' album name does NOT count as named."""
        conn = init_db(str(tmp_path / "test.db"))
        photos = [_make_photo(conn, tmp_path, f"{i}.jpg") for i in range(3)]
        for pid in photos:
            _add_face(conn, pid, 0, 0)
            _add_face(conn, pid, 1, 1)
        # Cluster 0 user-named; cluster 1 carries only the auto album name.
        conn.execute(
            "INSERT INTO albums (name, album_type, rule_json)"
            " VALUES ('Leo', 'smart_person', '{\"cluster_id\": 0}')"
        )
        conn.execute(
            "INSERT INTO albums (name, album_type, rule_json)"
            " VALUES ('Person 2', 'smart_person', '{\"cluster_id\": 1}')"
        )
        conn.commit()

        # Cluster 1 is still insignificant (auto name, 3 photos) → no group.
        assert detect_groups(conn, min_photos=3) == []

        # Name cluster 1 for real → both significant → group forms.
        conn.execute(
            "UPDATE albums SET name='Rita' WHERE album_type='smart_person'"
            " AND smart_person_cluster_id=1"
        )
        conn.commit()
        groups = detect_groups(conn, min_photos=3)
        assert len(groups) == 1
        assert sorted(groups[0]["members"]) == [0, 1]
        conn.close()

    def test_dismissed_faces_excluded(self, tmp_path):
        conn = init_db(str(tmp_path / "test.db"))
        photos = [_make_photo(conn, tmp_path, f"{i}.jpg") for i in range(3)]
        for pid in photos:
            _add_face(conn, pid, 0, 0)
            _add_face(conn, pid, 1, -2)  # dismissed

        groups = detect_groups(conn, min_photos=1)
        assert groups == []
        conn.close()


class TestGroupMinPhotosSetting:
    """Settings → Group detection: user-set minimum shared photos."""

    def test_resolver_default_floor_and_junk(self, tmp_path):
        from bpp.db.groups import group_min_photos
        from bpp.db.settings import set_setting

        conn = init_db(str(tmp_path / "test.db"))
        assert group_min_photos(conn) == 3  # DEFAULTS
        set_setting(conn, "group_min_photos", "2")
        assert group_min_photos(conn) == 2
        set_setting(conn, "group_min_photos", "0")
        assert group_min_photos(conn) == 1, "must floor at 1"
        set_setting(conn, "group_min_photos", "junk")
        assert group_min_photos(conn) == 3, "junk falls back to default"
        conn.close()

    def test_refresh_honors_user_setting(self, tmp_path):
        from bpp.db.settings import set_setting
        from bpp.db.smart_album_groups import _refresh_group_albums

        conn = init_db(str(tmp_path / "test.db"))
        # Two significant clusters (4 photos each) sharing only 2 photos.
        for i in range(2):
            pid = _make_photo(conn, tmp_path, f"shared_{i}.jpg")
            _add_face(conn, pid, 0, 0)
            _add_face(conn, pid, 1, 1)
        for cid in (0, 1):
            for i in range(2):
                pid = _make_photo(conn, tmp_path, f"solo_{cid}_{i}.jpg")
                _add_face(conn, pid, 0, cid)

        _refresh_group_albums(conn)
        assert (
            conn.execute("SELECT COUNT(*) FROM albums WHERE album_type='smart_group'").fetchone()[0]
            == 0
        ), "2 shared photos must not form a group at the default of 3"

        set_setting(conn, "group_min_photos", "2")
        _refresh_group_albums(conn)
        assert (
            conn.execute("SELECT COUNT(*) FROM albums WHERE album_type='smart_group'").fetchone()[0]
            == 1
        ), "lowering the setting to 2 must create the group album"
        conn.close()


class TestGroupNameSelfHeal:
    """Stale auto names ('Person 2 & Person 5' baked before renames)
    regenerate on refresh; user-given names survive."""

    def test_is_stale_auto_name_heuristic(self):
        from bpp.db.smart_album_groups import is_stale_auto_group_name

        members = ["Leo", "Rita"]
        assert is_stale_auto_group_name("Person 2 & Person 5", members)
        assert is_stale_auto_group_name("Leo & Person 5", members)
        assert is_stale_auto_group_name("Leo & Rita", members)
        assert not is_stale_auto_group_name("Family", members)
        assert not is_stale_auto_group_name("Beach crew & co", members)

    def _seed_pair(self, conn, tmp_path):
        photos = [_make_photo(conn, tmp_path, f"{i}.jpg") for i in range(4)]
        for pid in photos:
            _add_face(conn, pid, 0, 0)
            _add_face(conn, pid, 1, 1)

    def test_refresh_heals_stale_auto_name(self, tmp_path):
        from bpp.db.smart_album_groups import _refresh_group_albums

        conn = init_db(str(tmp_path / "test.db"))
        self._seed_pair(conn, tmp_path)
        # Cluster 0 was renamed to Leo since the group album was created.
        conn.execute(
            "INSERT INTO albums (name, album_type, rule_json)"
            " VALUES ('Leo', 'smart_person', '{\"cluster_id\": 0}')"
        )
        conn.execute(
            "INSERT INTO albums (name, album_type, rule_json)"
            " VALUES ('Person 1 & Person 2', 'smart_group',"
            " '{\"group_members\": [0, 1]}')"
        )
        conn.commit()

        _refresh_group_albums(conn)
        row = conn.execute("SELECT name FROM albums WHERE album_type='smart_group'").fetchone()
        assert row[0] == "Leo & Person 2", f"stale auto name not healed: {row[0]}"
        conn.close()

    def test_refresh_preserves_user_name(self, tmp_path):
        from bpp.db.smart_album_groups import _refresh_group_albums

        conn = init_db(str(tmp_path / "test.db"))
        self._seed_pair(conn, tmp_path)
        conn.execute(
            "INSERT INTO albums (name, album_type, rule_json)"
            " VALUES ('Family', 'smart_group', '{\"group_members\": [0, 1]}')"
        )
        conn.commit()

        _refresh_group_albums(conn)
        row = conn.execute("SELECT name FROM albums WHERE album_type='smart_group'").fetchone()
        assert row[0] == "Family", f"user name clobbered: {row[0]}"
        conn.close()


class TestGetGroupPhotoIds:
    def test_single_member(self, tmp_path):
        conn = init_db(str(tmp_path / "test.db"))
        p1 = _make_photo(conn, tmp_path, "a.jpg")
        p2 = _make_photo(conn, tmp_path, "b.jpg")
        _add_face(conn, p1, 0, 0)
        _add_face(conn, p2, 0, 0)

        ids = get_group_photo_ids(conn, [0])
        assert sorted(ids) == sorted([p1, p2])
        conn.close()

    def test_intersection(self, tmp_path):
        conn = init_db(str(tmp_path / "test.db"))
        p1 = _make_photo(conn, tmp_path, "a.jpg")
        p2 = _make_photo(conn, tmp_path, "b.jpg")
        p3 = _make_photo(conn, tmp_path, "c.jpg")
        # p1: persons 0, 1
        _add_face(conn, p1, 0, 0)
        _add_face(conn, p1, 1, 1)
        # p2: person 0 only
        _add_face(conn, p2, 0, 0)
        # p3: persons 0, 1
        _add_face(conn, p3, 0, 0)
        _add_face(conn, p3, 1, 1)

        ids = get_group_photo_ids(conn, [0, 1])
        assert sorted(ids) == sorted([p1, p3])
        conn.close()

    def test_empty_cluster_list(self, tmp_path):
        conn = init_db(str(tmp_path / "test.db"))
        assert get_group_photo_ids(conn, []) == []
        conn.close()


class TestHasGroupData:
    def test_no_faces(self, tmp_path):
        conn = init_db(str(tmp_path / "test.db"))
        assert has_group_data(conn) is False
        conn.close()

    def test_with_cooccurrence(self, tmp_path):
        conn = init_db(str(tmp_path / "test.db"))
        p1 = _make_photo(conn, tmp_path, "a.jpg")
        _add_face(conn, p1, 0, 0)
        _add_face(conn, p1, 1, 1)
        assert has_group_data(conn) is True
        conn.close()

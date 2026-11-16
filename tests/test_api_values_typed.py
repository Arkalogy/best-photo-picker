"""Fields the page treats as numbers, dates or hashes are sent as such.

A library database can hold any text in a column, whatever its declared
type, and a library folder can come from someone else. The page writes
these fields into HTML, so the API must not pass other text along under
their names.
"""

from __future__ import annotations

import json

import pytest

from bpp.utils.typed_values import as_date_text, as_float, as_hash, as_int

MARKUP = '"><img src=x data-onerror=toast>'


@pytest.fixture
def conn(tmp_path):
    from bpp.db.connection import close_all_connections, get_db, init_db

    db = str(tmp_path / "photopicker.db")
    init_db(db)
    yield get_db(db)
    close_all_connections(new_epoch=True)


def test_helpers_accept_what_the_app_stores():
    assert as_int(7) == 7 and as_int(7.0) == 7 and as_int(True) == 1
    assert as_float(1) == 1.0 and as_float(2.5) == 2.5
    assert as_date_text("2023-01-29T11:36:32") == "2023-01-29T11:36:32"
    assert as_date_text("2023:01:29 11:36:32.123456+02:00") != ""
    assert as_date_text("2023-01") == "2023-01"
    assert as_hash("0a1B" * 16) == "0a1B" * 16


@pytest.mark.parametrize(
    "value", [MARKUP, "7", "", None, b"7", float("nan"), float("inf"), [1], 1.5]
)
def test_as_int_refuses_everything_else(value):
    assert as_int(value) is None


@pytest.mark.parametrize("value", [MARKUP, "1.5", None, b"1", float("nan"), float("inf"), True])
def test_as_float_refuses_everything_else(value):
    assert as_float(value) is None


@pytest.mark.parametrize(
    "value", [MARKUP, "<b>", "x2023", 2023, None, "2023-01-01" + "0" * 40, "2023\n"]
)
def test_as_date_text_refuses_everything_else(value):
    assert as_date_text(value) == ""


@pytest.mark.parametrize("value", [MARKUP, "xyz", "", None, 12, "ab" * 65, "ab\n"])
def test_as_hash_refuses_everything_else(value):
    assert as_hash(value) is None


def test_photo_dict_sends_typed_fields_only():
    from bpp.web.photo_dict import build_photo_dict

    photo = build_photo_dict(
        {
            "filepath": "/lib/photos/a.jpg",
            "id": MARKUP,
            "date": MARKUP,
            "date_day": MARKUP,
            "date_month": MARKUP,
            "file_size": MARKUP,
            "cluster_size": MARKUP,
            "dup_cluster_id": MARKUP,
            "moment_cluster_id": MARKUP,
            "moment_size": MARKUP,
            "video_duration": MARKUP,
            "video_width": MARKUP,
            "video_height": MARKUP,
            "video_fps": MARKUP,
            "live_photo_sidecar_count": MARKUP,
        },
        None,
    )
    assert MARKUP not in json.dumps(photo)
    assert photo["date"] == "" and photo["date_month"] == ""
    assert photo["video_duration"] is None
    assert photo["cluster_size"] == 1 and photo["file_size"] == 0


def test_scores_are_numbers_or_their_default():
    from bpp.web.photo_dict import build_photo_dict, build_similar_entry

    item = {
        "filepath": "/lib/photos/a.jpg",
        "aggregate_score": MARKUP,
        "blur_score": MARKUP,
        "face_count": MARKUP,
        "nudity_score": MARKUP,
        "date_day": MARKUP,
    }
    photo = build_photo_dict(item, None)
    assert MARKUP not in json.dumps(photo)
    assert photo["aggregate_score"] == 0 and photo["face_count"] == 0
    assert photo["nudity_score"] is None
    assert photo["is_sensitive"] is True  # as the SQL filter sees it: text sorts above numbers
    assert MARKUP not in json.dumps(build_similar_entry(item, None, similarity=None))

    kept = build_photo_dict({"filepath": "/a.jpg", "aggregate_score": 0.75, "face_count": 3}, None)
    assert kept["aggregate_score"] == 0.75
    assert kept["face_count"] == 3 and isinstance(kept["face_count"], int)


def test_photo_dict_keeps_well_formed_values():
    from bpp.web.photo_dict import build_photo_dict

    photo = build_photo_dict(
        {
            "filepath": "/lib/photos/a.mov",
            "id": 5,
            "date": "2023-01-29T11:36:32",
            "date_day": "2023-01-29",
            "date_month": "2023-01",
            "file_size": 1234,
            "video_duration": 12.5,
            "video_width": 1920,
            "video_fps": 29.97,
        },
        None,
    )
    assert photo["id"] == 5
    assert photo["date"] == "2023-01-29T11:36:32"
    assert photo["date_month"] == "2023-01"
    assert photo["video_duration"] == 12.5 and photo["video_width"] == 1920
    assert photo["video_fps"] == 29.97


def _add_album(conn, name, album_type, rule):
    conn.execute(
        "INSERT INTO albums (name, album_type, rule_json) VALUES (?, ?, ?)",
        (name, album_type, json.dumps(rule)),
    )
    conn.commit()


def test_album_rule_numbers_are_numbers_or_absent(conn):
    from bpp.db.albums import list_albums

    _add_album(conn, "2031", "smart_time", {"year": MARKUP})
    _add_album(conn, "P", "smart_person", {"cluster_id": MARKUP})
    _add_album(conn, "T", "smart_tag", {"tag_id": MARKUP, "note": "kept"})
    rules = {a["name"]: a["rule"] for a in list_albums(conn)}
    assert rules["2031"] == {}
    assert rules["P"] == {}
    assert rules["T"] == {"note": "kept"}


def test_one_album_is_read_the_same_way_as_the_list(conn):
    from bpp.db.albums import get_album, list_albums

    _add_album(conn, "P", "smart_person", {"cluster_id": MARKUP})
    (listed,) = list_albums(conn)
    assert get_album(conn, listed["id"])["rule"] == {}


def test_album_rule_keeps_what_the_app_writes(conn):
    from bpp.db.albums import list_albums

    _add_album(conn, "2024", "smart_time", {"year": "2024"})  # the app stores the year as text
    _add_album(conn, "Recent", "smart_recent", {"days": 7})
    _add_album(conn, "Cat", "smart_pet", {"cluster_id": 0, "pet_class": "cat"})
    rules = {a["name"]: a["rule"] for a in list_albums(conn)}
    assert rules["2024"] == {"year": "2024"}
    assert rules["Recent"] == {"days": 7}
    assert rules["Cat"] == {"cluster_id": 0, "pet_class": "cat"}


def _add_photo(conn, tmp_path, name, **cols):
    from bpp.db.photos import upsert_photo

    photo_id = upsert_photo(conn, {"filepath": str(tmp_path / name)})
    for col, value in {"missing": 0, **cols}.items():  # no file on disk: keep it listed
        conn.execute(f"UPDATE photos SET {col} = ? WHERE id = ?", (value, photo_id))
    conn.commit()
    return photo_id


def test_memories_send_a_count_and_a_hash(conn, tmp_path):
    from bpp.db.memories import get_memory, list_memories

    hero = _add_photo(conn, tmp_path, "a.jpg", sha256=MARKUP)
    conn.execute(
        "INSERT INTO memories (title, date_start, date_end, hero_photo_id, photo_count, score)"
        " VALUES ('t', ?, ?, ?, ?, 1.0)",
        (MARKUP, "2024-06-01", hero, MARKUP),
    )
    conn.commit()
    (memory,) = list_memories(conn)
    assert MARKUP not in json.dumps(memory)
    assert MARKUP not in json.dumps(get_memory(conn, memory["id"]))
    assert memory["photo_count"] == 0 and memory["hero_hash"] is None
    assert memory["date_start"] is None and memory["date_end"] == "2024-06-01"


def test_calendar_sends_a_hash_or_nothing(conn, tmp_path):
    from bpp.db.calendar import get_daily_counts

    _add_photo(conn, tmp_path, "a.jpg", date="2024-06-05T10:00:00", sha256=MARKUP)
    days = get_daily_counts(conn, 2024, 6)
    assert [d["day"] for d in days] == [5]
    assert days[0]["top_hash"] is None


def test_face_groups_with_a_text_id_are_not_people(conn, tmp_path):
    from bpp.db.face_embedding_safety import active_embedding_dim
    from bpp.db.face_queries import load_face_clusters

    photo_id = _add_photo(conn, tmp_path, "a.jpg")
    blob = b"\x00" * (active_embedding_dim(conn) * 4)
    for index, cluster_id in enumerate([MARKUP, 3]):
        conn.execute(
            "INSERT INTO face_embeddings"
            " (photo_id, face_index, embedding, bbox_x, bbox_y, bbox_w, bbox_h, cluster_id)"
            " VALUES (?, ?, ?, 0, 0, 10, 10, ?)",
            (photo_id, index, blob, cluster_id),
        )
    conn.commit()
    assert [c["cluster_id"] for c in load_face_clusters(conn)] == [3]

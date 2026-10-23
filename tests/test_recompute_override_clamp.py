"""A recompute override is clamped to ITS key's range, not a blanket 0-10."""

from __future__ import annotations

import pytest

from bpp.web.state_helpers import clamp_override


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("hash_distance_threshold", 20, 20),  # the default itself used to become 10
        ("hash_distance_threshold", 100, 64),
        ("time_window_seconds", 30, 30),
        ("time_window_seconds", -5, 0),
        ("max_per_day", 12, 12),
        ("selection_similarity_threshold", 2, 1.0),
        ("face_selection_boost", 0.15, 0.15),
        ("blur_weight", 50, 1.0),  # scoring weights: 0-1, the range DEFAULTS documents
        ("blur_weight", 0.5, 0.5),
    ],
)
def test_clamped_to_the_keys_own_range(key, value, expected) -> None:
    assert clamp_override(key, value) == expected


def test_int_keys_come_back_as_ints_truncated_like_settings() -> None:
    assert clamp_override("hash_distance_threshold", "20") == 20
    assert clamp_override("hash_distance_threshold", 20.9) == 20


@pytest.mark.parametrize("bad", [None, "abc", [], {}])
def test_a_value_that_is_not_a_number_is_a_400_not_a_500(bad) -> None:
    from bpp.errors import ValidationError

    with pytest.raises(ValidationError, match="hash_distance_threshold must be a number"):
        clamp_override("hash_distance_threshold", bad)


def test_a_key_with_no_range_is_a_400_not_a_guess() -> None:
    from bpp.errors import ValidationError

    with pytest.raises(ValidationError):
        clamp_override("dedupe_strategy", 3)


def test_an_album_config_write_clamps_weights_and_refuses_junk(tmp_path, monkeypatch):
    """A stored album weight is used unchecked at recompute time, so the
    write is where its range (and type) is enforced."""
    from bpp.web.app import create_app

    workdir = tmp_path / "wd"
    workdir.mkdir()
    (workdir / "analysis.json").write_text("[]", encoding="utf-8")
    app = create_app(workdir=str(workdir))
    app.config["TESTING"] = True
    client = app.test_client()
    with app.app_context():
        from bpp.db.albums import create_album, get_album
        from bpp.web.state import get_ctx

        conn = get_ctx().get_conn()
        album_id = create_album(conn, "Test")
        conn.commit()
    r = client.put(f"/api/v1/albums/{album_id}", json={"config": {"blur_weight": 5.0}})
    assert r.status_code == 200, r.get_data(as_text=True)
    with app.app_context():
        conn = get_ctx().get_conn()
        assert get_album(conn, album_id)["config"]["blur_weight"] == 1.0
    r = client.put(f"/api/v1/albums/{album_id}", json={"config": {"blur_weight": "abc"}})
    assert r.status_code == 400

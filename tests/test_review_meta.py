"""Unit tests for the face-review photo-metadata helper."""

from __future__ import annotations

import sqlite3

import pytest

from bpp.web.review_meta import attach_photo_meta, photo_meta_by_filepaths


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        "CREATE TABLE photos (filepath TEXT, original_filename TEXT, "
        "date TEXT, aggregate_score REAL)"
    )
    c.executemany(
        "INSERT INTO photos VALUES (?,?,?,?)",
        [
            ("/lib/a.heic", "IMG_6346.HEIC", "2025-12-06T11:50:29", 0.8),
            ("/lib/b.jpg", None, "2024-01-02T08:00:00", 0.5),  # no original_filename
        ],
    )
    return c


def test_maps_filepath_to_meta(conn: sqlite3.Connection) -> None:
    meta = photo_meta_by_filepaths(conn, ["/lib/a.heic"])
    assert meta["/lib/a.heic"] == {
        "filename": "IMG_6346.HEIC",
        "date": "2025-12-06T11:50:29",
        "score": 0.8,
    }


def test_falls_back_to_basename_when_no_original_filename(conn: sqlite3.Connection) -> None:
    meta = photo_meta_by_filepaths(conn, ["/lib/b.jpg"])
    assert meta["/lib/b.jpg"]["filename"] == "b.jpg", meta


def test_dedups_and_drops_empty_paths(conn: sqlite3.Connection) -> None:
    # One batched query; duplicates and falsy paths must not break it.
    meta = photo_meta_by_filepaths(conn, ["/lib/a.heic", "/lib/a.heic", "", None])
    assert set(meta) == {"/lib/a.heic"}


def test_empty_input_returns_empty(conn: sqlite3.Connection) -> None:
    assert photo_meta_by_filepaths(conn, []) == {}
    assert photo_meta_by_filepaths(conn, ["", None]) == {}


def test_missing_row_simply_absent(conn: sqlite3.Connection) -> None:
    meta = photo_meta_by_filepaths(conn, ["/lib/nope.png"])
    assert meta == {}


def test_attach_copies_fields_in_place(conn: sqlite3.Connection) -> None:
    meta = photo_meta_by_filepaths(conn, ["/lib/a.heic"])
    rep = {"filepath": "/lib/a.heic", "face_index": 0}
    attach_photo_meta(rep, meta)
    assert rep["filename"] == "IMG_6346.HEIC"
    assert rep["date"] == "2025-12-06T11:50:29"
    assert rep["score"] == 0.8


def test_attach_noop_when_filepath_unknown_or_missing(conn: sqlite3.Connection) -> None:
    meta = photo_meta_by_filepaths(conn, ["/lib/a.heic"])
    rep = {"filepath": "/lib/unknown.png"}
    attach_photo_meta(rep, meta)
    assert "filename" not in rep
    no_fp: dict = {}
    attach_photo_meta(no_fp, meta)
    assert no_fp == {}

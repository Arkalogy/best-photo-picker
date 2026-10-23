"""CLIP search data is held once: the stacked matrix, with the per-photo
entries as views into it — not a dict of copies plus a second matrix —
and it's loaded row by row straight into that matrix."""

from __future__ import annotations

import os
import sqlite3

import numpy as np
import pytest

from bpp.db.clip import get_all_clip_embeddings, load_clip_matrix, upsert_clip_embedding


def _db(tmp_path, n=6, malformed=()):
    from bpp.db.connection import init_db

    workdir = str(tmp_path / "w")
    os.makedirs(workdir, exist_ok=True)
    db_path = os.path.join(workdir, "photopicker.db")
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys=ON")
    for i in range(n):
        conn.execute(
            "INSERT INTO photos (filepath, original_filename, file_size, file_mtime) "
            "VALUES (?, ?, ?, ?)",
            (f"/tmp/p{i}.jpg", f"p{i}.jpg", 100, 1000.0),
        )
    conn.commit()
    ids = [r[0] for r in conn.execute("SELECT id FROM photos ORDER BY id")]
    expected = {}
    for pid in ids:
        emb = np.full(512, pid, np.float32)
        upsert_clip_embedding(conn, pid, emb)
        expected[pid] = emb
    for pid in malformed:
        conn.execute(
            "UPDATE clip_embeddings SET embedding = ? WHERE photo_id = ?", (b"\x00" * 10, pid)
        )
        expected.pop(pid)
    conn.commit()
    return workdir, conn, expected


def test_the_matrix_is_loaded_row_by_row_with_bad_rows_skipped(tmp_path):
    _, conn, expected = _db(tmp_path, n=6, malformed=(3,))
    ids, matrix = load_clip_matrix(conn)
    assert matrix.dtype == np.float32 and matrix.shape == (5, 512)
    assert sorted(ids) == sorted(expected)
    for pid, row in zip(ids, matrix, strict=True):
        np.testing.assert_array_equal(row, expected[pid])


def test_no_fetchall_copy_of_every_row(tmp_path):
    _, conn, _ = _db(tmp_path, n=4)

    class _NoFetchall:
        def __init__(self, inner):
            self._inner = inner

        def execute(self, *a, **kw):
            cur = self._inner.execute(*a, **kw)

            class _Cur:
                def __iter__(self):
                    return iter(cur)

                def fetchone(self):
                    return cur.fetchone()

                def fetchall(self):
                    raise AssertionError("fetchall would hold every row at once")

            return _Cur()

    _, matrix = load_clip_matrix(_NoFetchall(conn))
    assert matrix.shape == (4, 512)


def test_per_photo_entries_are_views_into_one_matrix(tmp_path):
    _, conn, expected = _db(tmp_path, n=5)
    embs = get_all_clip_embeddings(conn)
    assert isinstance(embs, dict) and set(embs) == set(expected)
    assert all(v.base is not None for v in embs.values())
    assert len({id(v.base) for v in embs.values()}) == 1  # rows of one matrix
    for pid, emb in embs.items():
        np.testing.assert_array_equal(emb, expected[pid])


def test_rows_added_while_loading_are_still_loaded(tmp_path, monkeypatch):
    from bpp.db import clip

    _, conn, expected = _db(tmp_path, n=5)
    monkeypatch.setattr(clip, "get_clip_embedding_count", lambda conn, model_name=None: 2)
    ids, matrix = load_clip_matrix(conn)
    assert sorted(ids) == sorted(expected) and matrix.shape == (5, 512)
    for pid, row in zip(ids, matrix, strict=True):
        np.testing.assert_array_equal(row, expected[pid])


@pytest.mark.parametrize("n", [0, 1])
def test_tiny_libraries(tmp_path, n):
    _, conn, _ = _db(tmp_path, n=n)
    ids, matrix = load_clip_matrix(conn)
    assert len(ids) == n and matrix.shape == (n, 512)


def test_the_server_cache_holds_the_data_once(tmp_path):
    from bpp.web.app import create_app

    workdir, conn, expected = _db(tmp_path, n=5)
    conn.close()
    app = create_app(workdir=workdir)
    app.config["TESTING"] = True
    ctx = app.extensions["bpp"]
    with app.app_context():
        embs = ctx.load_clip_embeddings()
        cache = ctx.caches.clip_cache
        matrix, ids = cache["matrix"], cache["matrix_ids"]
        assert matrix.shape == (5, 512)
        assert all(np.shares_memory(matrix, v) for v in embs.values())
        for pid, row in zip(ids, matrix, strict=True):
            np.testing.assert_array_equal(row, expected[pid])


def test_the_shared_data_is_read_only(tmp_path):
    _, conn, _ = _db(tmp_path, n=3)
    _, matrix = load_clip_matrix(conn)
    embs = get_all_clip_embeddings(conn)
    with pytest.raises(ValueError):
        matrix[0, 0] = 1.0
    with pytest.raises(ValueError):
        next(iter(embs.values()))[0] = 1.0  # would corrupt every search


@pytest.mark.parametrize(
    ("module", "func"),
    [
        ("bpp.db.smart_album_queries", "_get_document_ids"),
        ("bpp.db.smart_album_document", "_refresh_document_album"),
    ],
)
def test_document_albums_use_the_matrix_without_a_second_copy(tmp_path, monkeypatch, module, func):
    import importlib

    _, conn, expected = _db(tmp_path, n=4)
    pids = sorted(expected)
    match = np.zeros(512, np.float32)
    match[0] = 1.0
    fake_matrix = np.array([match if p == pids[0] else -match for p in pids])
    monkeypatch.setattr("bpp.db.clip.load_clip_matrix", lambda conn: (pids, fake_matrix))
    monkeypatch.setattr("bpp.scoring.clip_embed.compute_text_embedding", lambda prompt: match)
    monkeypatch.setattr(
        "bpp.db.clip.get_all_clip_embeddings",
        lambda conn: pytest.fail("loads per-photo copies again"),
    )
    real_stack = np.stack
    monkeypatch.setattr(np, "stack", lambda *a, **k: pytest.fail("second full copy"))
    try:
        result = (
            getattr(importlib.import_module(module), func)(conn, {})
            if func.startswith("_get")
            else getattr(importlib.import_module(module), func)(conn)
        )
    finally:
        monkeypatch.setattr(np, "stack", real_stack)
    if func.startswith("_get"):
        assert result == [pids[0]]

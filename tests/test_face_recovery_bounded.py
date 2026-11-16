"""Stored recovery rows can't make an open repeat face extraction without limit.

The face-extraction journal lives in the library database. Every pending
row used to trigger its own full run on open, and the three-attempt limit
compared a stored counter that could start below zero.
"""

from __future__ import annotations

import pytest

from tests.test_face_recovery_thread import _pending_run_with_one_face


def _resume_counting_runs(app, ctx, monkeypatch) -> list:
    from bpp.web import face_extraction_journal as journal
    from bpp.web.face_recovery import recover_pending_face_extractions

    runs: list = []

    def fake_extract(conn, *a, resume_run_id=None, **k):
        runs.append(resume_run_id)
        journal.complete_run(conn, resume_run_id)
        return (1, 1)

    monkeypatch.setattr("bpp.web.face_worker.extract_and_cluster_faces", fake_extract)
    with app.app_context():
        recover_pending_face_extractions(ctx.get_conn(), ctx=ctx, cancel_event=None)
    return runs


def _pending(ctx) -> set:
    from bpp.web import face_extraction_journal as journal

    return {p["run_id"] for p in journal.pending_runs(ctx.get_conn())}


def test_one_ordinary_pending_run_still_resumes(tmp_path, monkeypatch):
    app, ctx, run_id = _pending_run_with_one_face(tmp_path)

    assert _resume_counting_runs(app, ctx, monkeypatch) == [run_id]


def test_many_pending_rows_resume_only_the_newest_two(tmp_path, monkeypatch):
    app, ctx, first = _pending_run_with_one_face(tmp_path)
    with app.app_context():
        conn = ctx.get_conn()
        start = conn.execute(
            "SELECT started_at FROM face_extraction_journal WHERE run_id = ?", (first,)
        ).fetchone()[0]
        for n in range(1, 5):
            conn.execute(
                "INSERT INTO face_extraction_journal (run_id, phases_complete, started_at)"
                " VALUES (?, 0, ?)",
                (f"planted-{n}", start + n),
            )
        conn.commit()

    runs = _resume_counting_runs(app, ctx, monkeypatch)

    assert runs == ["planted-3", "planted-4"]
    with app.app_context():
        assert _pending(ctx) == set()


@pytest.mark.parametrize("stored", [-5, 3, 400, "abc"])
def test_a_stored_retry_count_outside_the_limit_is_used_up(tmp_path, monkeypatch, stored):
    app, ctx, run_id = _pending_run_with_one_face(tmp_path)
    with app.app_context():
        conn = ctx.get_conn()
        conn.execute(
            "UPDATE face_extraction_journal SET retry_count = ? WHERE run_id = ?", (stored, run_id)
        )
        conn.commit()

    assert _resume_counting_runs(app, ctx, monkeypatch) == []
    with app.app_context():
        assert _pending(ctx) == set()


def test_older_rows_are_closed_in_one_statement(tmp_path, monkeypatch):
    app, ctx, _first = _pending_run_with_one_face(tmp_path)
    with app.app_context():
        conn = ctx.get_conn()
        conn.executemany(
            "INSERT INTO face_extraction_journal (run_id, phases_complete, started_at)"
            " VALUES (?, 0, ?)",
            [(f"planted-{n}", 10**9 + n) for n in range(500)],
        )
        conn.commit()
    closing: list = []
    from bpp.web import face_extraction_journal as journal

    monkeypatch.setattr(journal, "force_complete_after_retries", lambda *a, **k: closing.append(a))

    runs = _resume_counting_runs(app, ctx, monkeypatch)

    assert len(runs) == 2
    assert closing == [], "no per-row close"
    with app.app_context():
        assert _pending(ctx) == set()

"""The analysis file after a delete: rewritten in the background from a
durable note, and removed before the rows when the library is cleared."""

from __future__ import annotations

import os

# ── the analysis file is rewritten in the background, never forgotten ────


def _results(folder, *paths):
    import json

    with open(os.path.join(folder, "analysis.json"), "w") as f:
        json.dump([{"filepath": p} for p in paths], f)


def _listed(folder):
    import json

    with open(os.path.join(folder, "analysis.json")) as f:
        return [d["filepath"] for d in json.load(f)]


def test_a_delete_returns_before_the_analysis_file_is_rewritten(tmp_path, monkeypatch):
    import threading

    from bpp.db import analysis_leftovers as leftovers

    work = str(tmp_path)
    _results(work, "/p/a.jpg", "/p/b.jpg", "/p/c.jpg")
    hold, real = threading.Event(), leftovers._forget_in_json
    monkeypatch.setattr(leftovers, "_forget_in_json", lambda *a: (hold.wait(10), real(*a))[1])

    leftovers.forget_analysis(work, ["/p/a.jpg"])  # returns while the rewrite waits
    leftovers.forget_analysis(work, ["/p/b.jpg"])

    assert os.path.exists(os.path.join(work, leftovers.PENDING))
    assert _listed(work) == ["/p/a.jpg", "/p/b.jpg", "/p/c.jpg"]
    hold.set()
    leftovers.wait_for_forgets()
    assert _listed(work) == ["/p/c.jpg"]
    assert not os.path.exists(os.path.join(work, leftovers.PENDING))


def test_a_rewrite_the_process_did_not_live_to_do_is_done_at_the_next_start(tmp_path):
    from bpp.db import analysis_leftovers as leftovers
    from bpp.db.connection import init_db

    work = str(tmp_path)
    _results(work, "/p/a.jpg", "/p/b.jpg")
    assert leftovers._note_pending(work, ["/p/a.jpg"])  # noted, then the process ended
    conn = init_db(os.path.join(work, "photopicker.db"))

    leftovers.forget_interrupted_deletes(conn, work, None)

    assert _listed(work) == ["/p/b.jpg"]
    assert not os.path.exists(os.path.join(work, leftovers.PENDING))


def test_a_rewrite_that_fails_keeps_its_note(tmp_path, monkeypatch):
    from bpp.db import analysis_leftovers as leftovers

    work = str(tmp_path)
    _results(work, "/p/a.jpg", "/p/b.jpg")
    leftovers._note_pending(work, ["/p/a.jpg"])
    monkeypatch.setattr(leftovers, "_forget_in_json", lambda *a: False)

    leftovers.apply_pending_forgets(work)

    assert leftovers._read_pending(os.path.join(work, leftovers.PENDING))[0] == {"/p/a.jpg"}


def test_clear_library_stops_before_the_rows_go_when_the_analysis_file_stays(tmp_path, monkeypatch):
    from bpp.db import analysis_leftovers as leftovers
    from bpp.db.photos import upsert_photo
    from bpp.web.app import create_app

    app = create_app(library_path=str(tmp_path / "library"))
    app.config["TESTING"] = True
    ctx = app.extensions["bpp"]
    with app.app_context():
        conn = ctx.get_conn()
        upsert_photo(conn, {"filepath": "/p/a.jpg"})

    def refuse(*a):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(leftovers, "forget_analysis_files", refuse)
    with app.test_client() as client:
        r = client.delete("/api/v1/library", json={"confirmation": "delete"})

    assert r.status_code >= 400
    assert "nothing was cleared" in r.get_json()["error"]
    with app.app_context():
        assert ctx.get_conn().execute("SELECT COUNT(*) FROM photos").fetchone()[0] == 1


def test_a_file_that_could_not_be_read_keeps_its_note(tmp_path, monkeypatch):
    import builtins

    from bpp.db import analysis_leftovers as leftovers

    work = str(tmp_path)
    _results(work, "/p/a.jpg", "/p/b.jpg")
    leftovers._note_pending(work, ["/p/a.jpg"])
    results, real_open = os.path.join(work, "analysis.json"), builtins.open

    def busy(path, mode="r", *a, **k):
        if path == results and "b" in mode:
            raise OSError(5, "Input/output error")
        return real_open(path, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", busy)
    leftovers.apply_pending_forgets(work)
    monkeypatch.setattr(builtins, "open", real_open)

    assert os.path.exists(os.path.join(work, leftovers.PENDING)), "tried again later"
    leftovers.apply_pending_forgets(work)
    assert _listed(work) == ["/p/b.jpg"]


def test_a_rewrite_cut_short_leaves_nothing_that_clear_library_misses(tmp_path):
    from bpp.db import analysis_leftovers as leftovers

    work = str(tmp_path)
    _results(work, "/p/a.jpg")
    part = os.path.join(work, "analysis.json" + leftovers.PART)
    with open(part, "w") as f:
        f.write('[{"filepath": "/p/a.jpg"}]')  # what a killed rewrite leaves

    leftovers.forget_analysis_files(work, None)

    assert not os.path.exists(part)
    assert not os.path.exists(os.path.join(work, "analysis.json"))

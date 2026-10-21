"""The 'scan refused' health flag clears when the owner's forced recheck
runs, when a periodic sample finds nothing missing, and never crosses a
library switch."""

from __future__ import annotations

from PIL import Image


def _app(tmp_path):
    from bpp.web.app import create_app

    app = create_app(library_path=str(tmp_path / "lib"))
    app.config["TESTING"] = True
    return app, app.extensions["bpp"]


def test_the_forced_recheck_clears_the_flag(tmp_path):
    app, ctx = _app(tmp_path)
    ctx.analysis_store.missing_scan_refused = (60, 100)
    with app.test_client() as c:
        assert c.post("/api/v1/photos/recheck-missing").status_code == 200
    assert ctx.analysis_store.missing_scan_refused is None


def test_a_clean_periodic_sample_clears_the_flag(tmp_path):
    from bpp.db.connection import init_db
    from bpp.db.photos import upsert_photo
    from bpp.web.health import periodic_sample_tick

    photos = tmp_path / "photos"
    photos.mkdir()
    conn = init_db(str(tmp_path / "t.db"))
    for i in range(5):
        p = photos / f"p{i}.jpg"
        Image.new("RGB", (4, 4)).save(p, "JPEG")
        upsert_photo(conn, {"filepath": str(p)})
    conn.commit()
    seen: list = []
    periodic_sample_tick(conn, {"photos": str(photos)}, lambda: None, seen.append)
    assert seen == [None]


def test_a_refusal_from_the_previous_library_is_ignored(tmp_path):
    from bpp.web.state_lifecycle import make_scan_refused_callback

    _, ctx = _app(tmp_path)
    cb = make_scan_refused_callback(ctx)
    cb((3, 4))
    assert ctx.analysis_store.missing_scan_refused == (3, 4)
    ctx.analysis_store.bump_generation_and_reset_phash()
    cb((5, 6))
    assert ctx.analysis_store.missing_scan_refused is None


def test_a_sample_storage_did_not_answer_for_does_not_clear_the_flag(tmp_path, monkeypatch):
    from bpp.db.connection import init_db
    from bpp.db.photos import upsert_photo
    from bpp.web.health import periodic_sample_tick

    conn = init_db(str(tmp_path / "t.db"))
    for i in range(3):
        p = tmp_path / f"p{i}.jpg"
        Image.new("RGB", (4, 4)).save(p, "JPEG")
        upsert_photo(conn, {"filepath": str(p)})
    conn.commit()
    monkeypatch.setattr("bpp.db.photos_missing.file_present", lambda fp: None)
    seen: list = []
    periodic_sample_tick(conn, {"photos": str(tmp_path)}, lambda: None, seen.append)
    assert seen == []


def test_a_skipped_scan_reports_nothing(tmp_path):
    from bpp.db.connection import init_db
    from bpp.db.photos import upsert_photo
    from bpp.db.photos_missing import scan_missing
    from bpp.web.health import periodic_sample_tick

    conn = init_db(str(tmp_path / "t.db"))
    gone = tmp_path / "gone.jpg"
    Image.new("RGB", (4, 4)).save(gone, "JPEG")
    upsert_photo(conn, {"filepath": str(gone)})
    conn.commit()
    gone.unlink()
    unreachable = str(tmp_path / "unplugged")
    assert scan_missing(conn, photos_dir=unreachable).completed is False
    seen: list = []
    periodic_sample_tick(conn, {"photos": unreachable}, lambda: None, seen.append)
    assert seen == []


def test_the_forced_recheck_keeps_the_flag_when_storage_stops_answering(tmp_path, monkeypatch):
    from bpp.db.photos_missing import MissingScan

    app, ctx = _app(tmp_path)
    ctx.analysis_store.missing_scan_refused = (60, 100)
    monkeypatch.setattr(
        "bpp.db.photos_missing.scan_missing", lambda *a, **k: MissingScan([], completed=False)
    )
    with app.test_client() as c:
        assert c.post("/api/v1/photos/recheck-missing").status_code == 200
    assert ctx.analysis_store.missing_scan_refused == (60, 100)

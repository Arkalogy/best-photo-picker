"""When the missing-file scan refuses to mark more than half the library
missing (an outage, not a cleanup), the refusal is reported: the scan
result says so and the health endpoint shows it as degraded."""

from __future__ import annotations

from PIL import Image

from bpp.db.photos_missing import MASS_FLIP_MIN_ROWS, scan_missing


def _library(tmp_path, n):
    from bpp.db.connection import init_db
    from bpp.db.photos import upsert_photo

    photos = tmp_path / "photos"
    photos.mkdir()
    conn = init_db(str(tmp_path / "t.db"))
    paths = []
    for i in range(n):
        p = photos / f"p{i}.jpg"
        Image.new("RGB", (4, 4)).save(p, "JPEG")
        upsert_photo(conn, {"filepath": str(p)})
        paths.append(p)
    conn.commit()
    return conn, photos, paths


def test_scan_result_reports_the_refusal(tmp_path):
    n = MASS_FLIP_MIN_ROWS + 5
    conn, photos, paths = _library(tmp_path, n)
    for p in paths[: n - 3]:
        p.unlink()
    result = scan_missing(conn, photos_dir=str(photos))
    assert result.newly_missing == []
    assert result.refused == (n - 3, n)
    assert conn.execute("SELECT COUNT(*) FROM photos WHERE missing=1").fetchone()[0] == 0


def test_scan_result_is_clean_when_a_few_files_are_gone(tmp_path):
    conn, photos, paths = _library(tmp_path, MASS_FLIP_MIN_ROWS + 5)
    paths[0].unlink()
    result = scan_missing(conn, photos_dir=str(photos))
    assert result.newly_missing == [str(paths[0])]
    assert result.refused is None


def test_health_shows_a_refused_scan_as_degraded(tmp_path):
    from bpp.web.app import create_app

    app = create_app(library_path=str(tmp_path / "lib"))
    app.config["TESTING"] = True
    ctx = app.extensions["bpp"]
    with app.test_client() as c:
        assert c.get("/api/v1/health").get_json()["checks"]["missing_scan"] == {"refused": False}
    ctx.analysis_store.missing_scan_refused = (60, 100)
    with app.test_client() as c:
        health = c.get("/api/v1/health").get_json()
    assert health["checks"]["missing_scan"] == {"refused": True, "missing": 60, "total": 100}
    assert health["status"] == "degraded"

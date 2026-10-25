"""A camera RAW with saved edits is served edited, like any other format.

Before 0.2.2 the RAW branch of ``/photo/<hash>`` returned the plain
conversion: the edits step came after it and never ran.
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image

from bpp.utils import raw
from bpp.web.app import create_app


def _fake_convert(_raw_path, jpeg_path, **_kw):
    Image.new("RGB", (40, 20), "red").save(jpeg_path, "JPEG")
    return jpeg_path


def test_a_raw_photo_with_saved_edits_is_served_edited(tmp_path, monkeypatch):
    monkeypatch.setattr(raw, "RAWPY_AVAILABLE", True)
    monkeypatch.setattr(raw, "convert_raw_to_jpeg", _fake_convert)
    raw_path = tmp_path / "shot.ARW"
    raw_path.write_bytes(b"not really a raw file")

    app = create_app(workdir=str(tmp_path), input_dir=str(tmp_path), library_path=str(tmp_path))
    app.config["TESTING"] = True
    with app.app_context():
        from bpp.db.edits import save_photo_edits
        from bpp.db.photos import get_photo_by_path, upsert_photo
        from bpp.web.state import get_ctx

        ctx = get_ctx()
        conn = ctx.get_conn()
        upsert_photo(conn, {"filepath": str(raw_path), "aggregate_score": 0.5, "is_raw": 1})
        conn.commit()
        ctx.invalidate_analysis()
        ctx.load_analysis_if_needed()
        photo = get_photo_by_path(conn, str(raw_path))
        save_photo_edits(conn, photo["id"], {"rotation": 90})
        conn.commit()
        path_hash = ctx.thumbs.get_hash(str(raw_path))

    with app.test_client() as c:
        resp = c.get(f"/photo/{path_hash}")
        assert resp.status_code == 200, resp.data[:200]
        edited = Image.open(BytesIO(resp.data))
        assert edited.size == (20, 40), "the 90° rotation must be applied"

        resp = c.get(f"/photo/{path_hash}?raw=1")
        assert resp.status_code == 200
        assert Image.open(BytesIO(resp.data)).size == (40, 20), "the editor still gets the original"


def _raw_app(tmp_path, monkeypatch):
    monkeypatch.setattr(raw, "RAWPY_AVAILABLE", True)
    monkeypatch.setattr(raw, "convert_raw_to_jpeg", _fake_convert)
    raw_path = tmp_path / "shot.ARW"
    raw_path.write_bytes(b"not really a raw file")
    app = create_app(workdir=str(tmp_path), input_dir=str(tmp_path), library_path=str(tmp_path))
    app.config["TESTING"] = True
    with app.app_context():
        from bpp.db.edits import save_photo_edits
        from bpp.db.photos import get_photo_by_path, upsert_photo
        from bpp.web.state import get_ctx

        ctx = get_ctx()
        conn = ctx.get_conn()
        upsert_photo(conn, {"filepath": str(raw_path), "aggregate_score": 0.5, "is_raw": 1})
        conn.commit()
        ctx.invalidate_analysis()
        ctx.load_analysis_if_needed()
        photo = get_photo_by_path(conn, str(raw_path))
        save_photo_edits(conn, photo["id"], {"rotation": 90})
        conn.commit()
        return (
            app,
            str(raw_path),
            photo["id"],
            ctx.thumbs.get_hash(str(raw_path)),
            ctx.thumbs.cache_dir,
        )


def _size(resp):
    assert resp.status_code == 200, resp.data[:200]
    return Image.open(BytesIO(resp.data)).size


def test_the_thumbnail_of_an_edited_raw_is_edited_too(tmp_path, monkeypatch):
    app, _path, _pid, path_hash, _cache = _raw_app(tmp_path, monkeypatch)
    with app.test_client() as c:
        w, h = _size(c.get(f"/thumb/{path_hash}"))
        assert h > w, "the rotation shows in the grid as well"


def test_a_new_edit_shows_and_keeps_the_conversion(tmp_path, monkeypatch):
    import os

    from bpp.constants import PHOTO_CACHE_SUFFIX_FULL
    from bpp.web.photo_cache import cached_variant_path, invalidate_photo_cache

    app, _path, photo_id, path_hash, cache_dir = _raw_app(tmp_path, monkeypatch)
    full = cached_variant_path(cache_dir, path_hash, PHOTO_CACHE_SUFFIX_FULL)
    with app.test_client() as c:
        assert _size(c.get(f"/photo/{path_hash}")) == (20, 40)
        full_mtime = os.path.getmtime(full)

        with app.app_context():
            from bpp.db.edits import save_photo_edits
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            save_photo_edits(ctx.get_conn(), photo_id, {"rotation": 180})
            ctx.get_conn().commit()
            invalidate_photo_cache(ctx, path_hash, edits_only=True)

        assert os.path.exists(full), "an edit save keeps the slow RAW conversion"
        assert _size(c.get(f"/photo/{path_hash}")) == (40, 20), "the new edit shows"
        assert os.path.getmtime(full) == full_mtime, "the conversion was not redone"


def test_a_redone_conversion_rebuilds_the_edited_view(tmp_path, monkeypatch):
    import os
    import time

    from bpp.constants import PHOTO_CACHE_SUFFIX_EDITED, PHOTO_CACHE_SUFFIX_FULL
    from bpp.web.photo_cache import cached_variant_path

    app, _path, _pid, path_hash, cache_dir = _raw_app(tmp_path, monkeypatch)
    full = cached_variant_path(cache_dir, path_hash, PHOTO_CACHE_SUFFIX_FULL)
    edited = cached_variant_path(cache_dir, path_hash, PHOTO_CACHE_SUFFIX_EDITED)
    with app.test_client() as c:
        c.get(f"/photo/{path_hash}")
        first = os.path.getmtime(edited)
        time.sleep(0.05)
        Image.new("RGB", (60, 30), "blue").save(full, "JPEG")  # a newer conversion
        later = os.path.getmtime(full) + 1
        os.utime(full, (later, later))
        assert _size(c.get(f"/photo/{path_hash}")) == (30, 60)
        assert os.path.getmtime(edited) > first

"""A paired phone never learns the owner's filesystem layout.

Every JSON response to a LAN principal passes through one rewrite
(``bpp/web/lan_redaction.py``): strings under the library root become
library-relative, strings under the owner's home become ``~/...``, and
dict keys get the same treatment so maps keyed by path (overrides,
favorites) still match the photo rows. The owner's own app keeps
absolute paths (Show in Finder needs them). The device's read-only
lookups by path accept the rewritten form, and the export stream is
owner-only like the export.
"""

from __future__ import annotations

import json
import os

import pytest
from PIL import Image

from bpp.web.lan_redaction import path_rewriter, redact_paths, resolve_device_path

LAN = {"REMOTE_ADDR": "192.168.1.5"}


class TestRewrite:
    def test_library_and_home_paths_lose_their_prefix(self, tmp_path):
        lib = str(tmp_path / "lib")
        home = str(tmp_path / "home" / "alice")
        rw = path_rewriter(lib, home=home)
        assert rw(os.path.join(lib, "photos", "b", "a.jpg")) == os.path.join("photos", "b", "a.jpg")
        assert rw(lib) == "."
        assert rw(os.path.join(home, "Pictures", "x.jpg")) == os.path.join("~", "Pictures", "x.jpg")
        # Outside both roots: the volume and folder names stay on the Mac.
        ext = rw("/Volumes/nas/trip 2024/y.jpg")
        assert ext.startswith("@ext/") and ext.endswith("/y.jpg") and "nas" not in ext
        assert rw("plain text") == "plain text"

    def test_keys_and_nested_values_are_rewritten(self, tmp_path):
        lib = str(tmp_path / "lib")
        rw = path_rewriter(lib, home=str(tmp_path / "home"))
        a = os.path.join(lib, "photos", "a.jpg")
        data = {"overrides": {a: "include"}, "photos": [{"filepath": a, "n": 1}], "ok": True}
        out = redact_paths(data, rw)
        assert out == {
            "overrides": {os.path.join("photos", "a.jpg"): "include"},
            "photos": [{"filepath": os.path.join("photos", "a.jpg"), "n": 1}],
            "ok": True,
        }

    def test_resolve_is_the_inverse(self, tmp_path):
        lib = str(tmp_path / "lib")
        assert resolve_device_path(os.path.join("photos", "a.jpg"), lib) == os.path.join(
            lib, "photos", "a.jpg"
        )
        assert resolve_device_path("~/Pictures/x.jpg", lib) == os.path.expanduser(
            "~/Pictures/x.jpg"
        )
        assert resolve_device_path("/abs/y.jpg", lib) == "/abs/y.jpg"
        ext = path_rewriter(lib, home=str(tmp_path / "home"))("/Volumes/nas/y.jpg")
        table = {ext.split("/")[1]: "/Volumes/nas/y.jpg"}
        assert resolve_device_path(ext, lib, table.get) == "/Volumes/nas/y.jpg"
        assert resolve_device_path(ext, lib) == ext
        assert resolve_device_path("", lib) == ""


def _analysis_entry(filepath: str, i: int) -> dict:
    return {
        "filepath": filepath,
        "date": f"2024-01-{i + 1:02d}T12:00:00",
        "date_day": f"2024-01-{i + 1:02d}",
        "date_month": "2024-01",
        "file_size": 1024,
        "file_mtime": 1700000000.0 + i,
        "blur_raw": 100.0,
        "blur_score": 0.5,
        "exposure_score": 0.5,
        "face_score": 0.5,
        "face_count": 0,
        "composition_score": 0.5,
        "aggregate_score": 0.5,
    }


@pytest.fixture
def shared_app(tmp_path):
    """A library with two photos, sharing on, one approved phone."""
    from bpp.db.photos import upsert_photo
    from bpp.web.app import create_app
    from bpp.web.share import get_share_token, set_lan_sharing_enabled
    from bpp.web.share_devices import approve_device, find_or_create_pending_device

    lib = str(tmp_path / "library")
    os.makedirs(os.path.join(lib, "photos", "batch"))
    app = create_app(library_path=lib)
    app.config["TESTING"] = False
    ctx = app.extensions["bpp"]
    paths = [os.path.join(lib, "photos", "batch", f"img_{i}.jpg") for i in range(2)]
    for p in paths:  # real files: a photo without one counts as missing
        Image.new("RGB", (8, 8), "gray").save(p, "JPEG")
    with open(os.path.join(ctx.workdir, "analysis.json"), "w") as f:
        json.dump([_analysis_entry(p, i) for i, p in enumerate(paths)], f)
    with app.app_context():
        conn = ctx.get_conn()
        for i, p in enumerate(paths):
            upsert_photo(conn, {"filepath": p, "date": f"2024-01-{i + 1:02d}T12:00:00"})
        conn.commit()
        set_lan_sharing_enabled(conn, True)
        token = get_share_token(conn)
        row = find_or_create_pending_device(
            conn, fingerprint="fp-1", name="Phone", ip="192.168.1.5"
        )
        assert approve_device(conn, int(row["id"]))
    return app, lib, paths, token


def _as_phone(app, token, path):
    with app.test_client() as c:
        c.set_cookie("bpp_share_fp", "fp-1")
        return c.get(path, headers={"X-Auth-Token": token}, environ_overrides=LAN)


def _as_owner(app, path, method="GET", **kw):
    ctx = app.extensions["bpp"]
    with app.test_client() as c:
        return c.open(path, method=method, headers={"X-Auth-Token": ctx.auth_token}, **kw)


class TestPathsOnTheWire:
    def test_the_phone_sees_library_relative_paths_and_the_owner_absolute(self, shared_app):
        app, lib, paths, token = shared_app
        owner = _as_owner(app, "/api/v1/photos").get_json()
        phone = _as_phone(app, token, "/api/v1/photos").get_json()
        assert {p["filepath"] for p in owner["photos"]} == set(paths)
        assert {p["filepath"] for p in phone["photos"]} == {
            os.path.join("photos", "batch", f"img_{i}.jpg") for i in range(2)
        }
        assert lib not in json.dumps(phone)

    def test_override_keys_match_the_phones_photo_paths(self, shared_app):
        from bpp.db.albums import add_photos_to_album, ensure_all_photos_album
        from bpp.db.photos import get_photo_ids_by_paths

        app, _lib, paths, token = shared_app
        ctx = app.extensions["bpp"]
        with app.app_context():  # overrides live on All Photos membership rows
            conn = ctx.get_conn()
            add_photos_to_album(
                conn, ensure_all_photos_album(conn), get_photo_ids_by_paths(conn, paths)
            )
            conn.commit()
        r = _as_owner(
            app, "/api/v1/override", method="POST", json={"filepath": paths[0], "mode": "include"}
        )
        assert r.status_code == 200, r.get_json()
        overrides = _as_phone(app, token, "/api/v1/overrides").get_json()
        keys = set(overrides.get("overrides", overrides))
        assert os.path.join("photos", "batch", "img_0.jpg") in keys
        assert not any(os.path.isabs(k) for k in keys)

    def test_the_phone_can_look_a_photo_up_by_its_relative_path(self, shared_app):
        from bpp.db.photos import upsert_photo

        app, _lib, paths, token = shared_app
        ctx = app.extensions["bpp"]
        with app.app_context():
            upsert_photo(ctx.get_conn(), {"filepath": paths[0], "date": "2024-01-01T00:00:00"})
            ctx.get_conn().commit()
        rel = os.path.join("photos", "batch", "img_0.jpg")
        r = _as_phone(app, token, f"/api/v1/photos/edits?filepath={rel}")
        assert r.status_code == 200
        assert (
            r.get_json() == _as_owner(app, f"/api/v1/photos/edits?filepath={paths[0]}").get_json()
        )


class TestExportStreamIsOwnerOnly:
    def test_a_paired_device_cannot_follow_the_export_stream(self, shared_app):
        app, _lib, _paths, token = shared_app
        assert _as_phone(app, token, "/api/v1/export/progress").status_code == 403

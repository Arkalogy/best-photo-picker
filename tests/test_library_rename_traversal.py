"""TDD tests for H-10: library rename path traversal prevention."""

from __future__ import annotations

import pytest

from bpp.web.app import create_app


@pytest.fixture()
def app_and_lib(tmp_path):
    lib = tmp_path / "my_library"
    lib.mkdir()
    (lib / "photos").mkdir()
    (lib / "data").mkdir()
    (lib / "cache").mkdir()
    (lib / "logs").mkdir()
    app = create_app(
        workdir=str(lib / "data"),
        input_dir=str(lib),
        library_path=str(lib),
    )
    app.config["TESTING"] = True
    return app, str(lib)


class TestLibraryRenameTraversal:
    def test_normal_rename_allowed(self, app_and_lib):
        app, lib = app_and_lib
        with app.test_client() as c:
            resp = c.put(
                "/api/v1/libraries/rename",
                json={"path": lib, "name": "new_name", "rename_folder": True},
            )
            assert resp.status_code == 200

    def test_path_separator_rejected(self, app_and_lib):
        app, lib = app_and_lib
        with app.test_client() as c:
            resp = c.put(
                "/api/v1/libraries/rename",
                json={"path": lib, "name": "foo/bar", "rename_folder": True},
            )
            assert resp.status_code == 400
            assert "invalid" in resp.get_json()["error"].lower()

    def test_dotdot_rejected(self, app_and_lib):
        app, lib = app_and_lib
        with app.test_client() as c:
            resp = c.put(
                "/api/v1/libraries/rename",
                json={"path": lib, "name": "../evil", "rename_folder": True},
            )
            assert resp.status_code == 400

    def test_backslash_rejected(self, app_and_lib):
        app, lib = app_and_lib
        with app.test_client() as c:
            resp = c.put(
                "/api/v1/libraries/rename",
                json={
                    "path": lib,
                    "name": "foo\\bar",
                    "rename_folder": True,
                },
            )
            assert resp.status_code == 400


class TestActiveRenameUpdatesWorkdirCorrectly:
    """R4-M3: when renaming the ACTIVE library folder, the server's
    in-memory state must point at <new_root>/data, not <new_root>.
    The previous code set workdir = new_path (the library root),
    which made every subsequent DB path computation wrong — DB
    writes would attempt to land in the library root instead of
    data/, and the next backup_db / restore_backup would point at
    a non-existent file.
    """

    def test_workdir_lands_under_data_after_active_rename(self, app_and_lib):
        import os

        app, lib = app_and_lib
        ctx = app.extensions["bpp"]
        # Sanity: starting state has workdir under data/
        assert ctx.state["workdir"].endswith(os.path.join("my_library", "data"))

        with app.test_client() as c:
            resp = c.put(
                "/api/v1/libraries/rename",
                json={"path": lib, "name": "renamed_library", "rename_folder": True},
            )
            assert resp.status_code == 200, resp.get_json()
            new_path = resp.get_json()["new_path"]

        # library_path is the new ROOT
        assert ctx.state["library_path"] == new_path
        # workdir is the new data subdir, not the new root
        expected_data = os.path.join(new_path, "data")
        assert ctx.state["workdir"] == expected_data, (
            f"R4-M3: workdir should be {expected_data!r} (library/data) "
            f"but is {ctx.state['workdir']!r} (library root). DB writes "
            "will land in the wrong place."
        )
        # ctx.dirs should also be refreshed
        assert ctx.dirs["data"] == expected_data
        assert ctx.dirs["photos"] == os.path.join(new_path, "photos")

"""Clear logs empties only the files this app's log rotation writes
(``server.log`` and ``server.log.1`` … ``.N``), and never through a link.
"""

from __future__ import annotations

import os

import pytest

from bpp.constants import LOG_BACKUP_COUNT


@pytest.fixture
def app(tmp_path):
    from bpp.web.app import create_app

    lib = tmp_path / "library"
    (lib / "logs").mkdir(parents=True)
    app = create_app(workdir=str(lib / "data"), library_path=str(lib))
    app.config["TESTING"] = True
    assert app.extensions["bpp"].dirs["logs"] == str(lib / "logs")
    return app


def _clear(app):
    r = app.test_client().post("/api/v1/logs/clear")
    assert r.status_code == 200
    return r.get_json()["files"]


def test_links_and_strangers_in_the_logs_folder_are_left_alone(app, tmp_path):
    logs = tmp_path / "library" / "logs"
    owner = tmp_path / "owner_notes.txt"
    owner.write_text("owner file")
    (logs / "server.log").write_text("live log")
    (logs / "server.log.1").symlink_to(owner)
    (logs / "server.log.old").symlink_to(owner)
    (logs / "server.log.2").symlink_to(tmp_path / "not_there")
    (logs / "server.log.keep").write_text("someone's notes")

    cleared = _clear(app)

    assert owner.read_text() == "owner file"
    assert not (tmp_path / "not_there").exists(), "a dangling link was written through"
    assert (logs / "server.log.keep").read_text() == "someone's notes"
    assert (logs / "server.log").read_text() == ""
    assert cleared == 1


def test_the_live_log_and_every_rotated_file_are_emptied(app, tmp_path):
    logs = tmp_path / "library" / "logs"
    names = ["server.log"] + [f"server.log.{i}" for i in range(1, LOG_BACKUP_COUNT + 1)]
    for name in names:
        (logs / name).write_text("lines")
    beyond = logs / f"server.log.{LOG_BACKUP_COUNT + 1}"
    beyond.write_text("not ours")

    cleared = _clear(app)

    assert [os.path.getsize(logs / n) for n in names] == [0] * len(names)
    assert beyond.read_text() == "not ours"
    assert cleared == len(names)

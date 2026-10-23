"""No test reaches the owner's real default library. An app built with
only a workdir resolves its library to the default path and runs the
start-up recoveries there (rename journal, data dir), so the suite
points the default at a folder of its own."""

from __future__ import annotations

import os


def test_the_suite_default_library_is_not_the_owners():
    from bpp.db import library

    real_default = os.path.expanduser("~/Pictures/BestPhotoPicker")
    assert real_default != library.DEFAULT_LIBRARY_PATH
    assert not library.DEFAULT_LIBRARY_PATH.startswith(real_default + os.sep)
    assert library.get_library_path(None) == library.DEFAULT_LIBRARY_PATH


def test_a_workdir_only_app_creates_nothing_under_home(tmp_path):
    from bpp.db import library
    from bpp.web.app import create_app

    real_default = os.path.expanduser("~/Pictures/BestPhotoPicker")
    before = os.path.exists(real_default)
    create_app(workdir=str(tmp_path / "work"))
    assert os.path.exists(real_default) == before
    assert str(tmp_path) in library.DEFAULT_LIBRARY_PATH

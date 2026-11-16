"""A permanent-delete journal row travels with the library folder, so
its replay takes nothing about *where it may act* from the row: the
allowed folders come from the library that is open, and the claim token
must be the plain 16-hex token this app generates.
"""

from __future__ import annotations

import os

import pytest

from bpp.web.permanent_delete import claim_name
from tests.test_permanent_delete_recycle_bin import TOKEN, _pending, _recover, _rows


@pytest.fixture(autouse=True)
def _reset_journal_handlers():
    from bpp.db.journal import _reset_handlers_for_tests

    _reset_handlers_for_tests()
    yield
    _reset_handlers_for_tests()


@pytest.fixture
def app(tmp_path):
    from bpp.web.app import create_app

    lib = tmp_path / "library"
    (lib / "photos").mkdir(parents=True)
    app = create_app(workdir=str(lib / "data"), library_path=str(lib))
    app.config["TESTING"] = True
    return app


def _journal(app, payload):
    from bpp.db.journal import journal_start

    ctx = app.extensions["bpp"]
    with app.app_context():
        journal_start(ctx.get_conn(), "permanent_delete", payload)


def _record(path, row_id=999):
    st = os.stat(path)
    return {
        "id": row_id,
        "ino": int(st.st_ino),
        "size": int(st.st_size),
        "mtime_ns": int(st.st_mtime_ns),
        "claimed": True,
    }


def test_folders_named_by_the_journal_row_are_not_trusted(app, tmp_path):
    """The row lists an outside folder as allowed and names a claimed
    file there whose photo row is gone: it must not be unlinked."""
    outside = tmp_path / "owner_docs"
    outside.mkdir()
    victim = outside / "taxes.jpg"
    claimed = claim_name(str(victim), TOKEN)
    with open(claimed, "wb") as f:
        f.write(b"owner file")
    _journal(
        app,
        {
            "filepaths": [str(victim)],
            "sha256_map": {},
            "allowed_dirs": [str(outside)],
            "token": TOKEN,
            "files": {str(victim): _record(claimed)},
        },
    )

    _recover(app)

    assert os.path.exists(claimed), "a file outside the open library was removed"
    assert not victim.exists()


@pytest.mark.parametrize("token", ["TRAVERSAL", "0123456789ABCDEF", "0123456789abcde", 5, ["a"]])
def test_a_claim_token_that_is_not_ours_touches_nothing(app, tmp_path, token):
    """With a live row at the journaled path, the put-back branch moves
    whatever sits under the claim name to that path. A token carrying
    path parts points the claim name at any file on the disk."""
    lib = app.extensions["bpp"].state["library_path"]
    ((fp, row_id),) = _rows(app, ["a.jpg"]).items()
    os.remove(fp)
    planted = os.path.join(os.path.dirname(fp), ".a.jpg.bpp-trash-x")
    os.mkdir(planted)
    secret = tmp_path / "secret.txt"
    secret.write_text("owner file")
    if token == "TRAVERSAL":
        token = "x/" + os.path.relpath(secret, planted)
        assert os.path.samefile(claim_name(fp, token), secret)
    _journal(
        app,
        {
            "filepaths": [fp],
            "sha256_map": {},
            "allowed_dirs": [lib],
            "token": token,
            "files": {fp: {"id": row_id, "ino": 1, "size": 1, "mtime_ns": 1, "claimed": True}},
        },
    )

    _recover(app)

    assert secret.read_text() == "owner file"
    assert not os.path.exists(fp), f"token {token!r}: a file was moved into the library"
    assert _pending(app) == [], "the refused entry must not be replayed at every start"


@pytest.mark.parametrize(
    "bad",
    [
        {"files": {"/lib/photos/a.jpg": "x"}},
        {"files": {"/lib/photos/a.jpg": {"id": 1, "ino": 1}}, "attempts": "abc"},
        {"files": {5: {"id": 1}}},
    ],
)
def test_a_malformed_entry_is_consumed_without_raising(app, bad):
    _journal(app, {"filepaths": [], "sha256_map": {}, "token": TOKEN, **bad})

    _recover(app)

    assert _pending(app) == [], "a malformed entry would fail again at every start"

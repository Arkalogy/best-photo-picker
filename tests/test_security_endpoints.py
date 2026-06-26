"""Security regression tests for endpoints that accept filepath input.

Covers:
- `/api/v1/photos/enhance-preview` — must reject arbitrary filesystem paths,
  only accept filepaths that exist in the DB.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def client(tmp_path):
    from bpp.web.app import create_app

    d = str(tmp_path.resolve())
    app = create_app(workdir=d, library_path=d)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_enhance_preview_rejects_unknown_filepath(client):
    """Endpoint must refuse filepaths not tracked in the DB (path traversal guard)."""
    resp = client.get("/api/v1/photos/enhance-preview?filepath=/etc/passwd")
    assert resp.status_code == 404
    data = resp.get_json()
    assert "not found" in data.get("error", "").lower()


def test_enhance_preview_requires_filepath(client):
    """Endpoint must reject empty filepath."""
    resp = client.get("/api/v1/photos/enhance-preview")
    assert resp.status_code == 400

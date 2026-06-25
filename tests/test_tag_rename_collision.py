"""TDD tests for M-4: tag rename collision must return 409, not 500."""

from __future__ import annotations

import os

from bpp.web.app import create_app


def test_rename_to_existing_name_returns_409(tmp_path):
    workdir = str(tmp_path / "w")
    os.makedirs(workdir)
    app = create_app(workdir=workdir)
    app.config["TESTING"] = True

    with app.test_client() as c:
        # Create two tags
        r1 = c.post("/api/v1/tags", json={"name": "cats"})
        assert r1.status_code == 200

        r2 = c.post("/api/v1/tags", json={"name": "dogs"})
        assert r2.status_code == 200
        id2 = r2.get_json()["id"]

        # Rename "dogs" to "cats" — should conflict
        resp = c.put(f"/api/v1/tags/{id2}", json={"name": "cats"})
        assert resp.status_code == 409
        assert "exists" in resp.get_json()["error"].lower()


def test_rename_to_new_name_works(tmp_path):
    workdir = str(tmp_path / "w")
    os.makedirs(workdir)
    app = create_app(workdir=workdir)
    app.config["TESTING"] = True

    with app.test_client() as c:
        r1 = c.post("/api/v1/tags", json={"name": "old_name"})
        tag_id = r1.get_json()["id"]

        resp = c.put(f"/api/v1/tags/{tag_id}", json={"name": "new_name"})
        assert resp.status_code == 200
        assert resp.get_json()["name"] == "new_name"

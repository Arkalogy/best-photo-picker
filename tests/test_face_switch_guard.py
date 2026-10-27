"""While a face-model switch is placing names, the operations that would
move or rename clusters are refused with a 409 (``blocker="face_switch"``).

Shared fixtures are imported by name and re-bound as test parameters.
"""

# ruff: noqa: F811

from __future__ import annotations

import json
import os

import pytest

from bpp.db.face_name_placement import begin_switch, finish_switch
from tests._web_shared import face_web_client  # noqa: F401


def _conn(tmp_path):
    from bpp.db.connection import get_db

    return get_db(os.path.join(str(tmp_path / "workdir"), "photopicker.db"))


def _person_album(conn, name: str, cid: int) -> int:
    """The person album of a cluster that has faces (an empty cluster's
    default album is legitimately removed by any album refresh); reused
    when a refresh already made it."""
    rule = json.dumps({"cluster_id": cid}, sort_keys=True)
    conn.execute(
        "INSERT OR IGNORE INTO albums (name, album_type, rule_json) VALUES (?, 'smart_person', ?)",
        (name, rule),
    )
    conn.commit()
    return conn.execute(
        "SELECT id FROM albums WHERE album_type='smart_person' AND rule_json=?", (rule,)
    ).fetchone()[0]


def _wait_for_startup_refresh(client) -> None:
    """The app refreshes the smart albums on a startup thread; a test that
    edits person albums while it runs races it (an album removed or created
    under the test). Wait for it, as a user's first click effectively does."""
    from bpp.web.state import get_ctx

    with client.application.app_context():
        thread = get_ctx().analysis_store.phase5_thread
    if thread is not None:
        thread.join(timeout=30)


@pytest.fixture
def pending_switch(face_web_client, tmp_path):
    _wait_for_startup_refresh(face_web_client)
    conn = _conn(tmp_path)
    begin_switch(conn, "sface", {0: {1, 2, 3}})
    conn.commit()
    return face_web_client, conn


def _assert_face_switch_409(resp) -> None:
    assert resp.status_code == 409, resp.get_data(as_text=True)
    body = resp.get_json()
    text = json.dumps(body)
    assert "face_switch" in text, body


class TestRefusedWhileSwitching:
    def test_recluster_is_refused(self, pending_switch):
        client, _ = pending_switch
        _assert_face_switch_409(client.post("/api/v1/faces/recluster", json={"threshold": 0.6}))

    def test_merge_is_refused(self, pending_switch):
        client, _ = pending_switch
        _assert_face_switch_409(
            client.post(
                "/api/v1/faces/merge",
                json={"primary_cluster_id": 0, "merge_cluster_ids": [1]},
            )
        )

    def test_person_rename_is_refused(self, pending_switch):
        client, conn = pending_switch
        album_id = _person_album(conn, "Person 1", 1)
        _assert_face_switch_409(client.put(f"/api/v1/albums/{album_id}", json={"name": "Leo"}))

    def test_a_manual_album_rename_is_unaffected(self, pending_switch):
        client, _ = pending_switch
        resp = client.post("/api/v1/albums", json={"name": "Trip", "album_type": "manual"})
        assert resp.status_code in (200, 201), resp.get_data(as_text=True)
        album_id = resp.get_json()["id"]
        resp = client.put(f"/api/v1/albums/{album_id}", json={"name": "Trip 2024"})
        assert resp.status_code == 200, resp.get_data(as_text=True)

    def test_allowed_again_after_the_switch_finishes(self, pending_switch):
        client, conn = pending_switch
        finish_switch(conn)  # (its album refresh drops placeholder albums)
        album_id = _person_album(conn, "Person 1", 1)
        resp = client.put(f"/api/v1/albums/{album_id}", json={"name": "Leo"})
        assert resp.status_code == 200, resp.get_data(as_text=True)


class TestExtractionStaysAllowed:
    def test_starting_face_extraction_is_not_refused(self, pending_switch):
        """Extraction is what finishes the switch; refusing it would lock the
        library for good after a cancelled run."""
        from unittest.mock import patch

        client, _ = pending_switch
        # Only the route's guard is under test: no real worker, and no
        # model selection (which could download) behind it.
        with (
            patch("bpp.web.face_worker.FaceWorker.start", return_value=True),
            patch("bpp.scoring.face_embed.embedding_method", return_value="sface"),
            patch("bpp.web.bp_faces_extract.face_recognition_available", return_value=True),
        ):
            for route in ("/api/v1/faces/extract", "/api/v1/faces/retry"):
                resp = client.post(route, json={})
                assert resp.status_code == 202, (route, resp.get_data(as_text=True))

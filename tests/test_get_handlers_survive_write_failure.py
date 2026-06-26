"""Protection D — GET handlers must survive side-effect write failures.

The Jun-2 demo lib incident: ``GET /api/v1/faces/clusters`` did a lazy
``delete_setting`` cleanup of stale avatar overrides inside the read
path. A transient ``sqlite3.OperationalError: disk I/O error`` from
that write propagated up and turned the read into a 500. The People
panel — and the sidebar — disappeared.

Protection D wraps the lazy cleanup in a try/except so the GET
returns successfully (with the stale data) instead of crashing. This
test pins that contract: ``delete_setting`` patched to raise, the
endpoint must still return 200.
"""

from __future__ import annotations

import json
import os
import sqlite3
import struct
from collections.abc import Iterator
from unittest.mock import patch

import pytest
from PIL import Image


@pytest.fixture()
def _suppress_config(monkeypatch):
    """Mirror the helper from tests/test_bp_analysis_photos.py."""
    monkeypatch.setenv("BPP_TEST_NO_CONFIG", "1")


@pytest.fixture()
def app_with_stale_avatar(tmp_path, _suppress_config) -> Iterator[tuple]:
    """App seeded with one face_embedding in cluster 0 and a stale
    person_avatar_0 settings row whose filepath doesn't exist on disk
    (so the GET handler's cleanup branch fires)."""
    d = str(tmp_path)
    # A real photo on disk (the cluster's representative).
    real_fp = os.path.join(d, "real_photo.jpg")
    Image.new("RGB", (100, 100), "red").save(real_fp, "JPEG")

    from bpp.web.app import create_app

    app = create_app(workdir=d, input_dir=d, library_path=d)
    app.config["TESTING"] = True

    with app.app_context():
        from bpp.db.photos import upsert_photo
        from bpp.web.state import get_ctx

        ctx = get_ctx()
        conn = ctx.get_conn()
        photo_id = upsert_photo(
            conn,
            {
                "filepath": real_fp,
                "file_size": 100,
                "file_mtime": 1700000000.0,
            },
        )

        # Valid 128-d float32 embedding in cluster 0.
        valid_blob = struct.pack("128f", *([0.1] * 128))
        conn.execute(
            "INSERT INTO face_embeddings "
            "(photo_id, face_index, bbox_x, bbox_y, bbox_w, bbox_h, embedding,"
            " cluster_id, quality, extraction_max_long_side)"
            " VALUES (?, 0, 10, 10, 50, 50, ?, 0, 0.9, 1024)",
            (photo_id, valid_blob),
        )

        # Stale avatar override: file EXISTS on disk so the handler's
        # os.path.exists() check passes, but there's no matching
        # (filepath, face_index) in face_embeddings, so the cleanup
        # else-branch fires and calls delete_setting(). That's the
        # write we want to crash on.
        stale_fp = os.path.join(d, "stale_avatar.jpg")
        Image.new("RGB", (50, 50), "blue").save(stale_fp, "JPEG")
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (
                "person_avatar_0",
                json.dumps({"filepath": stale_fp, "face_index": 99}),
            ),
        )
        conn.commit()

        yield app.test_client(), conn


def test_clusters_endpoint_succeeds_when_lazy_cleanup_write_fails(
    app_with_stale_avatar,
) -> None:
    """The Jun-2 regression: lazy ``delete_setting`` inside the GET
    raised on disk I/O error, propagated, and 500'd the endpoint.

    Patch ``delete_setting`` to always raise; the endpoint must still
    return 200 with the clusters payload. The stale override remains
    in the DB (acceptable — next read or explicit retry clears it)
    but the user's sidebar / People panel survives."""
    client, _conn = app_with_stale_avatar
    from bpp.web import bp_faces

    def raise_disk_io(*_args, **_kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    with patch.object(bp_faces, "delete_setting", side_effect=raise_disk_io):
        resp = client.get("/api/v1/faces/clusters")

    assert resp.status_code == 200, (
        f"GET /api/v1/faces/clusters must survive lazy-cleanup write "
        f"failures (Protection D). Got {resp.status_code}: "
        f"{resp.data[:300]!r}"
    )
    payload = resp.get_json()
    assert "clusters" in payload
    assert isinstance(payload["clusters"], list)


def test_lazy_cleanup_does_not_swallow_programmer_errors(
    app_with_stale_avatar,
) -> None:
    """P-09: Protection D's catch used to be a blanket
    ``except Exception``. That would also swallow a NameError
    introduced by a future refactor and leave the bug hiding in the
    log while the endpoint kept working. The narrowed
    ``except (sqlite3.Error, OSError)`` only catches the disk / DB
    errors the write actually emits; programmer mistakes propagate.

    Patch ``delete_setting`` to raise NameError (a stand-in for
    "refactor broke an import"); the endpoint should 500 instead of
    silently degrading."""
    client, _conn = app_with_stale_avatar
    from bpp.web import bp_faces

    def raise_name_error(*_args, **_kwargs):
        raise NameError("name 'misnamed_helper' is not defined")

    # In TESTING mode Flask propagates unhandled exceptions through
    # the test client — exactly what we want to assert. Under
    # production config the same NameError would become a 500; the
    # point is that Protection D does NOT silently swallow it.
    with (
        patch.object(bp_faces, "delete_setting", side_effect=raise_name_error),
        pytest.raises(NameError),
    ):
        client.get("/api/v1/faces/clusters")


def test_clusters_endpoint_succeeds_without_write_failure(
    app_with_stale_avatar,
) -> None:
    """Sanity: the endpoint also returns 200 in the happy path (no
    patched write). Locks the regression test above isn't accidentally
    passing because the handler short-circuits before the cleanup
    branch."""
    client, _conn = app_with_stale_avatar
    resp = client.get("/api/v1/faces/clusters")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert "clusters" in payload

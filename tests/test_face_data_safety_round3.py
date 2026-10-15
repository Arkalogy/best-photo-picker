"""Third-review follow-ups: debris never wipes a library, resumed runs
use the recorded face model, and the endpoint wiring really is there."""

from __future__ import annotations

import json
import os

import numpy as np
import pytest

from tests.test_embedding_size_contract import _add_row, _phase_conn


@pytest.mark.parametrize(
    ("method", "good_tag"), [("sface", "sface_yunet"), ("dlib", None)], ids=["sface", "dlib"]
)
def test_untagged_debris_in_an_upgraded_library_is_not_a_model_switch(method, good_tag):
    from bpp.web import face_extraction_phases as phases

    conn = _phase_conn()
    conn.execute("INSERT INTO settings VALUES ('face_embedding_method', ?)", (method,))
    for pid in range(5):
        _add_row(conn, pid, 128, good_tag)
    _add_row(conn, 99, 256, None)  # SIGKILL debris, untagged
    _, changed, _ = phases.reconcile_method(conn, method)
    assert changed is False
    assert conn.execute("SELECT COUNT(*) FROM face_embeddings").fetchone()[0] == 5


def test_untagged_legacy_library_switching_to_buffalo_is_a_switch():
    from bpp.web import face_extraction_phases as phases

    conn = _phase_conn()
    conn.execute("INSERT INTO settings VALUES ('face_embedding_method', 'buffalo_s')")
    for pid in range(3):
        _add_row(conn, pid, 128, None)
    _, changed, _ = phases.reconcile_method(conn, "buffalo_s")
    assert changed is True


def _ctx(conn):
    class _Ctx:
        run_id = 1

    c = _Ctx()
    c.conn = conn
    c.current_method = None
    return c


def test_resumed_run_extracts_with_the_recorded_method():
    from unittest.mock import patch

    from bpp.web import face_phase_classes

    conn = _phase_conn()
    conn.execute("INSERT INTO settings VALUES ('face_embedding_method_extracted', 'dlib')")
    conn.execute("INSERT INTO settings VALUES ('face_embedding_method', 'dlib')")
    snap = type(
        "S", (), {"stored_method": "dlib", "method_changed": False, "old_cluster_photos": None}
    )
    ctx = _ctx(conn)
    with (
        patch.object(face_phase_classes.journal, "load_snapshot", return_value=snap),
        patch.object(face_phase_classes, "embedding_method", return_value="dlib"),
    ):
        face_phase_classes.MethodReconcilerPhase().rehydrate(ctx)
    assert ctx.current_method == "dlib"


def test_resumed_run_refuses_when_the_recorded_model_is_unavailable():
    from unittest.mock import patch

    from bpp.errors import BppError
    from bpp.web import face_phase_classes

    conn = _phase_conn()
    conn.execute("INSERT INTO settings VALUES ('face_embedding_method_extracted', 'sface')")
    conn.execute("INSERT INTO settings VALUES ('face_embedding_method', 'sface')")
    snap = type(
        "S", (), {"stored_method": "sface", "method_changed": False, "old_cluster_photos": None}
    )
    with (
        patch.object(face_phase_classes.journal, "load_snapshot", return_value=snap),
        patch.object(face_phase_classes, "embedding_method", return_value="dlib"),
        pytest.raises(BppError),
    ):
        face_phase_classes.MethodReconcilerPhase().rehydrate(_ctx(conn))


def test_plugin_registering_its_entry_late_is_picked_up():
    from bpp.registry import get_entry, register_entry
    from bpp.registry.model_registry import _REGISTRY
    from bpp.scoring.model_load_gate import license_allows

    assert license_allows("late_plugin_model", feature="late") is False
    entry = get_entry("openai_clip_vit_b32_onnx")
    import dataclasses

    register_entry(dataclasses.replace(entry, id="late_plugin_model"))
    try:
        assert license_allows("late_plugin_model", feature="late") is True
    finally:
        _REGISTRY._store.pop("late_plugin_model", None)


# ── endpoint wiring ──────────────────────────────────────────────────


@pytest.fixture
def web(tmp_path):
    from bpp.db.connection import get_db
    from bpp.web.app import create_app
    from tests.test_web import _make_analysis

    workdir = str(tmp_path / "workdir")
    os.makedirs(workdir)
    analysis = _make_analysis(5)
    with open(os.path.join(workdir, "analysis.json"), "w") as f:
        json.dump(analysis, f)
    app = create_app(workdir=workdir)
    app.config["TESTING"] = True
    client = app.test_client()
    client.get("/api/v1/photos")
    conn = get_db(os.path.join(workdir, "photopicker.db"))
    pids = [r[0] for r in conn.execute("SELECT id FROM photos ORDER BY id").fetchall()]
    return client, conn, pids


def test_hand_drawn_face_endpoints_are_refused_in_a_buffalo_library(web):
    client, conn, pids = web
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) "
        "VALUES ('face_embedding_method_extracted', 'buffalo_s')"
    )
    conn.execute(
        "INSERT INTO face_embeddings (photo_id, face_index, embedding, cluster_id)"
        " VALUES (?, 0, ?, 0)",
        (pids[0], np.ones(512, np.float32).tobytes()),
    )
    conn.commit()
    with __import__("unittest.mock").mock.patch(
        "bpp.web.bp_faces_bbox.get_photo_id_by_path", return_value=pids[0]
    ):
        face_id = conn.execute("SELECT id FROM face_embeddings").fetchone()[0]
        upd = client.post(
            "/api/v1/faces/update-bbox",
            json={"face_id": face_id, "bbox_pct": {"x": 10, "y": 10, "w": 20, "h": 20}},
        )
    assert upd.status_code == 400
    assert "SFace" in upd.get_json()["error"]


def test_recluster_unassigns_rows_it_cannot_read(web):
    from bpp.constants import CLUSTER_UNASSIGNED

    client, conn, pids = web
    rng = np.random.RandomState(1)
    for i in range(3):
        conn.execute(
            "INSERT INTO face_embeddings (photo_id, face_index, embedding, cluster_id)"
            " VALUES (?, 0, ?, 7)",
            (pids[i], (rng.randn(128) * 0.01 + 1).astype(np.float32).tobytes()),
        )
    conn.execute(
        "INSERT INTO face_embeddings (photo_id, face_index, embedding, cluster_id)"
        " VALUES (?, 0, ?, 0)",
        (pids[3], np.ones(512, np.float32).tobytes()),
    )
    conn.commit()
    resp = client.post("/api/v1/faces/recluster", json={"threshold": 0.6})
    assert resp.status_code == 200
    stray = conn.execute(
        "SELECT cluster_id FROM face_embeddings WHERE length(embedding) = 2048"
    ).fetchone()[0]
    assert stray == CLUSTER_UNASSIGNED


def test_resume_after_a_mid_run_model_switch_stops_and_keeps_everything():
    """The user picked dlib while an SFace run was in flight. Resuming
    must neither extract with dlib into SFace clusters nor reconcile
    mid-pipeline (later phases may already be marked done, and the
    original run's name map would be lost). It stops; faces, clusters
    and the saved name map stay; the next fresh analysis does the switch."""
    from unittest.mock import patch

    from bpp.errors import BppError
    from bpp.web import face_phase_classes

    conn = _phase_conn()
    conn.execute("INSERT INTO settings VALUES ('face_embedding_method_extracted', 'sface')")
    conn.execute("INSERT INTO settings VALUES ('face_embedding_method', 'dlib')")
    _add_row(conn, 1, 128, "sface_yunet", cluster=7)
    original_map = {7: {1, 2}}
    snap = type(
        "S",
        (),
        {"stored_method": "sface", "method_changed": True, "old_cluster_photos": original_map},
    )
    ctx = _ctx(conn)
    with (
        patch.object(face_phase_classes.journal, "load_snapshot", return_value=snap),
        patch.object(face_phase_classes, "embedding_method", return_value="dlib"),
        patch.object(face_phase_classes.journal, "force_complete_after_retries") as close,
        pytest.raises(BppError) as exc,
    ):
        face_phase_classes.MethodReconcilerPhase().rehydrate(ctx)
    close.assert_called_once()  # closed: not retried at every startup
    assert "new analysis" in exc.value.user_message
    assert ctx.old_cluster_photos == original_map
    row = conn.execute("SELECT cluster_id FROM face_embeddings").fetchone()
    assert row[0] == 7

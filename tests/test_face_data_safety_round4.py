"""Final-review follow-ups."""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pytest

from bpp.scoring.face_embedder_registry import _REGISTRY, FaceEmbedder, register_embedder
from tests._models import skip_unless_buffalo_s
from tests.test_embedding_size_contract import _add_row, _phase_conn


@pytest.fixture
def unrunnable_plugin():
    register_embedder(
        FaceEmbedder(
            name="__unrunnable__",
            embed=lambda i, b: np.ones(256),
            embedding_dim=256,
            registry_id="no_such_entry",
        )
    )
    yield "__unrunnable__"
    _REGISTRY._store.pop("__unrunnable__", None)


def test_switch_to_a_model_that_cannot_run_keeps_the_faces(unrunnable_plugin):
    from bpp.errors import BppError
    from bpp.web.face_phase_classes import MethodReconcilerPhase

    conn = _phase_conn()
    conn.execute("INSERT INTO settings VALUES ('face_embedding_method_extracted', 'sface')")
    conn.execute("INSERT INTO settings VALUES ('face_embedding_method', ?)", (unrunnable_plugin,))
    _add_row(conn, 1, 128, "sface_yunet")

    class _Ctx:
        run_id = 1

    ctx = _Ctx()
    ctx.conn = conn
    with pytest.raises(BppError) as exc:
        MethodReconcilerPhase().run(ctx)
    assert unrunnable_plugin in exc.value.user_message
    assert conn.execute("SELECT COUNT(*) FROM face_embeddings").fetchone()[0] == 1


def test_plugins_load_where_photos_are_scored():
    import contextlib

    from bpp.scoring import aggregate

    with patch("bpp.plugins.ensure_loaded_once") as load, contextlib.suppress(Exception):
        aggregate.process_one(("/nope.jpg", 64, ":memory:"))
    load.assert_called()


def test_library_of_an_unloaded_plugin_is_read_at_its_real_size():
    from bpp.db.face_embedding_safety import active_embedding_dim

    conn = _phase_conn()
    conn.execute("INSERT INTO settings VALUES ('face_embedding_method_extracted', 'plugin_gone')")
    for pid in range(5):
        _add_row(conn, pid, 64, "plugin_gone_model")
    assert active_embedding_dim(conn) == 64


def test_hand_drawn_faces_refused_while_the_librarys_plugin_is_not_loaded():
    from bpp.errors import ValidationError
    from bpp.web.face_create_helpers import require_hand_drawn_faces_supported

    conn = _phase_conn()
    conn.execute("INSERT INTO settings VALUES ('face_embedding_method_extracted', 'plugin_gone')")
    _add_row(conn, 1, 128, "plugin_gone_model")
    with pytest.raises(ValidationError):
        require_hand_drawn_faces_supported(conn)


def _blocked_buffalo():
    from bpp.registry.policy import ModelLoadBlockedError, ModelLoadDecision, PolicyResult

    real = __import__("bpp.registry", fromlist=["enforce_load_policy_for"]).enforce_load_policy_for

    def fake(model_id):
        if model_id == "insightface_buffalo_s":
            raise ModelLoadBlockedError(
                PolicyResult(
                    decision=ModelLoadDecision.BLOCKED_NEEDS_ACK, reason="x", entry_id=model_id
                )
            )
        return real(model_id)

    return patch("bpp.registry.enforce_load_policy_for", side_effect=fake)


def _run_phase(conn, resolved):
    from bpp.web import face_phase_classes

    class _Ctx:
        run_id = 1

    ctx = _Ctx()
    ctx.conn = conn
    with patch.object(face_phase_classes, "embedding_method", return_value=resolved):
        face_phase_classes.MethodReconcilerPhase().run(ctx)


@pytest.mark.parametrize("tag", ["sface_yunet", None], ids=["tagged", "untagged"])
def test_older_library_switch_to_unlicensed_buffalo_keeps_faces(tag):
    """No extracted method recorded (analysed before that key existed)."""
    from bpp.errors import BppError
    from bpp.scoring import model_load_gate

    conn = _phase_conn()
    conn.execute("INSERT INTO settings VALUES ('face_embedding_method', 'buffalo_s')")
    _add_row(conn, 1, 128, tag)
    for g in model_load_gate._ALL_GATES:
        g.reset()
    with _blocked_buffalo(), pytest.raises(BppError):
        _run_phase(conn, "buffalo_s")
    assert conn.execute("SELECT COUNT(*) FROM face_embeddings").fetchone()[0] == 1


def test_licensed_switch_still_runs():
    skip_unless_buffalo_s()  # runs the real embedder
    conn = _phase_conn()
    conn.execute("INSERT INTO settings VALUES ('face_embedding_method_extracted', 'sface')")
    conn.execute("INSERT INTO settings VALUES ('face_embedding_method', 'buffalo_s')")
    _add_row(conn, 1, 128, "sface_yunet")
    with patch("bpp.scoring.model_load_gate.license_allows", return_value=True):
        _run_phase(conn, "buffalo_s")
    assert conn.execute("SELECT COUNT(*) FROM face_embeddings").fetchone()[0] == 0


def test_same_model_rerun_is_never_refused():
    conn = _phase_conn()
    conn.execute("INSERT INTO settings VALUES ('face_embedding_method_extracted', 'sface')")
    conn.execute("INSERT INTO settings VALUES ('face_embedding_method', 'sface')")
    _add_row(conn, 1, 128, "sface_yunet")
    with patch("bpp.scoring.model_load_gate.license_allows", return_value=False):
        _run_phase(conn, "sface")
    assert conn.execute("SELECT COUNT(*) FROM face_embeddings").fetchone()[0] == 1


@pytest.mark.parametrize(
    ("recorded", "rows", "allowed"),
    [
        ("sface", [(128, "sface_yunet")], True),
        ("dlib", [(128, "dlib_face_recognition_resnet_v1")], False),
        (None, [(128, None)], True),
        (None, [(128, "dlib_face_recognition_resnet_v1")], False),
    ],
    ids=["sface", "dlib", "legacy-untagged", "legacy-dlib"],
)
def test_hand_drawn_faces_only_in_sface_libraries(recorded, rows, allowed):
    from bpp.errors import ValidationError
    from bpp.web.face_create_helpers import require_hand_drawn_faces_supported

    conn = _phase_conn()
    if recorded:
        conn.execute(
            "INSERT INTO settings VALUES ('face_embedding_method_extracted', ?)", (recorded,)
        )
    for i, (dim, tag) in enumerate(rows):
        _add_row(conn, i, dim, tag)
    if allowed:
        require_hand_drawn_faces_supported(conn)
    else:
        with pytest.raises(ValidationError, match="SFace"):
            require_hand_drawn_faces_supported(conn)


@pytest.mark.parametrize("method", ["buffalo_s", "dlib"])
def test_switch_to_a_model_whose_files_cannot_load_keeps_faces(method):
    from bpp.errors import BppError

    conn = _phase_conn()
    conn.execute("INSERT INTO settings VALUES ('face_embedding_method_extracted', 'sface')")
    conn.execute("INSERT INTO settings VALUES ('face_embedding_method', ?)", (method,))
    _add_row(conn, 1, 128, "sface_yunet")
    with (
        patch("bpp.scoring.model_load_gate.license_allows", return_value=True),
        patch("bpp.scoring.face_embed_buffalo_s.is_on_disk", return_value=False),
        patch(
            "bpp.scoring.face_embed_buffalo_s.ensure_buffalo_s_model",
            side_effect=AssertionError("must not download during analysis"),
        ),
        patch("bpp.scoring.face_recognition_loader.load_face_recognition", return_value=None),
        pytest.raises(BppError),
    ):
        _run_phase(conn, method)
    assert conn.execute("SELECT COUNT(*) FROM face_embeddings").fetchone()[0] == 1


def test_older_untagged_dlib_library_refuses_hand_drawn_faces():
    from bpp.errors import ValidationError
    from bpp.web.face_create_helpers import require_hand_drawn_faces_supported

    conn = _phase_conn()
    conn.execute("INSERT INTO settings VALUES ('face_embedding_method', 'dlib')")
    _add_row(conn, 1, 128, None)
    with pytest.raises(ValidationError):
        require_hand_drawn_faces_supported(conn)


def test_plugin_setup_calling_back_does_not_deadlock(monkeypatch):
    import threading

    import bpp.plugins as plugins

    monkeypatch.setattr(plugins, "_loaded_once", False)
    done = threading.Event()

    def reenter():
        plugins.ensure_loaded_once()
        return 0

    def run():
        with patch.object(plugins, "load_plugin_entry_points", side_effect=reenter):
            plugins.ensure_loaded_once()
        done.set()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    assert done.wait(5), "ensure_loaded_once deadlocked on re-entry"

"""Second-review follow-ups: no path wipes or hides a user's faces."""

from __future__ import annotations

import sqlite3
import traceback
from unittest.mock import patch

import numpy as np
import pytest

from tests.test_embedding_size_contract import _add_row, _phase_conn


def test_corrupt_row_of_the_current_method_is_deleted_alone():
    """A wrong-size row tagged with the current method is debris, not a
    model switch: delete that row, keep the rest."""
    from bpp.web import face_extraction_phases as phases

    conn = _phase_conn()
    conn.execute("INSERT INTO settings VALUES ('face_embedding_method', 'sface')")
    for pid in range(5):
        _add_row(conn, pid, 128, "sface_yunet")
    _add_row(conn, 9, 256, "sface")  # float64 debris with a bare-name tag
    _, changed, _ = phases.reconcile_method(conn, "sface")
    assert changed is False
    assert conn.execute("SELECT COUNT(*) FROM face_embeddings").fetchone()[0] == 5


def test_hand_drawn_face_in_an_untagged_slot_does_not_wipe_buffalo_faces():
    from bpp.web import face_extraction_phases as phases

    conn = _phase_conn()
    conn.execute("INSERT INTO settings VALUES ('face_embedding_method_extracted', 'buffalo_s')")
    for pid in range(3):
        _add_row(conn, pid, 512, "insightface_buffalo_s")
    _add_row(conn, 7, 128, None)
    _, changed, _ = phases.reconcile_method(conn, "buffalo_s")
    assert changed is False
    assert conn.execute("SELECT COUNT(*) FROM face_embeddings").fetchone()[0] == 3


def test_startup_cleanup_checks_bare_name_tags(tmp_path):
    from bpp.db.integrity import prune_corrupt_face_embeddings

    db = tmp_path / "lib.db"
    c = sqlite3.connect(db)
    c.execute(
        "CREATE TABLE face_embeddings (id INTEGER PRIMARY KEY, photo_id INTEGER,"
        " face_index INTEGER, embedding BLOB, producing_model_id TEXT)"
    )
    c.executemany(
        "INSERT INTO face_embeddings (photo_id, face_index, embedding, producing_model_id)"
        " VALUES (1, 0, ?, ?)",
        [
            (np.ones(128, np.float32).tobytes(), "sface"),  # keep
            (np.ones(256, np.float32).tobytes(), "sface"),  # wrong size: drop
            (np.ones(512, np.float32).tobytes(), "buffalo_s"),  # keep
        ],
    )
    c.commit()
    c.close()
    assert prune_corrupt_face_embeddings(str(db)) == 1


def test_readers_infer_the_size_from_rows_when_never_recorded():
    """Upgraded library, user picks buffalo_s before re-analysing: the
    128-float faces must stay visible."""
    from bpp.db.face_embedding_safety import active_embedding_dim

    conn = _phase_conn()
    conn.execute("INSERT INTO settings VALUES ('face_embedding_method', 'buffalo_s')")
    _add_row(conn, 1, 128, None)
    _add_row(conn, 2, 128, "sface_yunet")
    assert active_embedding_dim(conn) == 128


def test_model_unavailable_fallback_refuses_instead_of_wiping():
    from bpp.errors import BppError
    from bpp.web import face_phase_classes

    conn = _phase_conn()
    conn.execute("INSERT INTO settings VALUES ('face_embedding_method', 'sface')")
    _add_row(conn, 1, 128, "sface_yunet")

    class _Ctx:
        pass

    ctx = _Ctx()
    ctx.conn = conn
    with (
        patch.object(face_phase_classes, "embedding_method", return_value="dlib"),
        pytest.raises(BppError) as exc,
    ):
        face_phase_classes.MethodReconcilerPhase().run(ctx)
    assert "sface" in exc.value.user_message
    assert conn.execute("SELECT COUNT(*) FROM face_embeddings").fetchone()[0] == 1


@pytest.mark.no_preaccept_permissive
def test_repeated_refusals_do_not_grow_a_traceback():
    from bpp.registry.policy import ModelLoadBlockedError
    from bpp.scoring.model_load_gate import MemoizedLoadGate

    gate = MemoizedLoadGate("insightface_scrfd_25g")
    last = None
    for _ in range(200):
        try:
            gate.enforce()
        except ModelLoadBlockedError as exc:
            last = exc
    assert last is not None
    assert len(traceback.extract_tb(last.__traceback__)) < 10


@pytest.mark.no_preaccept_permissive
def test_cached_refusal_clears_when_the_files_change():
    from bpp.registry.attribution_bundle import confirm_attribution_bundle
    from bpp.scoring.model_load_gate import license_allows

    assert license_allows("mediapipe_selfie_segmenter", feature="t") is False
    confirm_attribution_bundle(
        ["mediapipe_selfie_segmenter"],
        checkbox_responses={"permissive_attribution_acknowledged": True},
        accepted_at="2026-10-14T00:00:00+00:00",
    )
    assert license_allows("mediapipe_selfie_segmenter", feature="t") is True


def test_hand_drawn_faces_are_refused_for_non_128_libraries():
    from bpp.errors import ValidationError
    from bpp.web.face_create_helpers import require_hand_drawn_faces_supported

    conn = _phase_conn()
    conn.execute("INSERT INTO settings VALUES ('face_embedding_method_extracted', 'buffalo_s')")
    with pytest.raises(ValidationError, match="SFace"):
        require_hand_drawn_faces_supported(conn)
    conn.execute("UPDATE settings SET value='sface' WHERE key='face_embedding_method_extracted'")
    require_hand_drawn_faces_supported(conn)


def test_face_extraction_child_reports_the_user_message():
    import multiprocessing as mp

    from bpp.errors import BppError
    from bpp.web import analyze_face_extract

    q = mp.Queue()
    err = BppError("tech detail", user_message="Plain words for the user.")
    with (
        patch("bpp.db.connection.init_db", side_effect=err),
    ):
        analyze_face_extract._face_extraction_worker([], {}, "/nope.db", q)
    msg = q.get(timeout=5)
    assert msg["type"] == "fatal_error"
    assert msg["error"] == "Plain words for the user."

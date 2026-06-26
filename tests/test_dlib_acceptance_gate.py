"""The dlib face embedder is the one restricted model whose weights
ship *inside* a pip dependency (``face_recognition``), so they never
pass through the download-time policy gate, and dlib is not the default
embedder, so the orchestrator chokepoint doesn't cover it either.

`bpp/scoring/face_embed_extractors.py::_enforce_dlib_policy` is the
ONLY enforcement point that makes dlib fail-closed on a missing
acceptance. These tests pin that gate at every dlib entry point so a
future refactor can't silently reopen the hole.
"""

from __future__ import annotations

import sys
import types

import numpy as np
import pytest


@pytest.fixture(autouse=True)
def _reset_dlib_policy_memo():
    """The gate memoizes a passing check per process. Clear it before
    each test so a prior allowed-path test can't mask a blocked-path
    assertion (or vice versa) via test ordering."""
    from bpp.scoring import face_embed_extractors as fee

    fee._dlib_gate.reset()
    yield
    fee._dlib_gate.reset()


@pytest.mark.no_preaccept_permissive
def test_dlib_policy_gate_blocks_when_not_accepted(monkeypatch, tmp_path):
    """With no acceptance row on file, the dlib gate must raise —
    proving the registry id resolves and the gate is real (not a typo
    that would silently allow inference)."""
    monkeypatch.setenv("BPP_ACCEPTANCE_LOG_PATH", str(tmp_path / "empty-acceptance.jsonl"))
    from bpp.registry import ModelLoadBlockedError
    from bpp.scoring.face_embed_extractors import _enforce_dlib_policy

    with pytest.raises(ModelLoadBlockedError):
        _enforce_dlib_policy()


def test_dlib_policy_gate_passes_when_accepted():
    """The autouse conftest fixture pre-accepts dlib; the gate must then
    pass silently (no raise), so accepting users are unaffected."""
    from bpp.scoring.face_embed_extractors import _enforce_dlib_policy

    _enforce_dlib_policy()  # must not raise


def test_dlib_policy_gate_memoizes_passing_check(monkeypatch):
    """The gate is called per-photo; the underlying policy check reads the
    acceptance log from disk every call. Once it passes, subsequent calls
    must short-circuit (no re-check) so face extraction doesn't do a
    per-photo disk read on a large library."""
    import bpp.registry as reg
    from bpp.scoring import face_embed_extractors as fee

    calls = {"n": 0}

    def _counting_enforce(_model_id):
        calls["n"] += 1

    monkeypatch.setattr(reg, "enforce_load_policy_for", _counting_enforce)
    fee._dlib_gate.reset()
    fee._enforce_dlib_policy()
    fee._enforce_dlib_policy()
    fee._enforce_dlib_policy()
    assert calls["n"] == 1, f"expected the passing check to be memoized (1 call), got {calls['n']}"


@pytest.mark.no_preaccept_permissive
def test_extract_dlib_fails_closed_before_inference(monkeypatch, tmp_path):
    """_extract_dlib must hit the gate as its first action — i.e. it
    raises ModelLoadBlockedError before ever touching face_recognition.
    The face worker's broad except converts that into a skipped photo."""
    monkeypatch.setenv("BPP_ACCEPTANCE_LOG_PATH", str(tmp_path / "empty-acceptance.jsonl"))
    from bpp.registry import ModelLoadBlockedError
    from bpp.scoring.face_embed_extractors import _extract_dlib

    img = np.zeros((64, 64, 3), dtype=np.uint8)
    with pytest.raises(ModelLoadBlockedError):
        _extract_dlib(img, min_confidence=0.2)


def test_supplement_with_scrfd_keeps_sface_results_when_dlib_blocked(monkeypatch):
    """The SCRFD-supplementary path runs dlib *inside the SFace flow*.
    A dlib block here must NOT discard the SFace embeddings already
    found — it skips only the supplemented faces and returns the SFace
    results intact."""
    from bpp.registry import ModelLoadBlockedError, PolicyResult
    from bpp.registry.policy import ModelLoadDecision
    from bpp.scoring import face_embed_extractors as fee

    # A non-overlapping SCRFD detection so the function reaches the gate.
    monkeypatch.setattr(
        "bpp.scoring.face.detect_faces_scrfd",
        lambda image, min_confidence=0.0: [(200, 200, 40, 40, 0.99)],
    )
    # face_recognition may not be installed in every CI lane; inject a
    # stub so the import inside the function succeeds and we exercise the
    # gate rather than the import-guard early return.
    monkeypatch.setitem(sys.modules, "face_recognition", types.ModuleType("face_recognition"))
    # Force the gate to report "not accepted".
    monkeypatch.setattr(
        fee,
        "_enforce_dlib_policy",
        lambda: (_ for _ in ()).throw(
            ModelLoadBlockedError(
                PolicyResult(
                    decision=ModelLoadDecision.BLOCKED_NEEDS_ACK,
                    reason="test",
                    entry_id="dlib_face_recognition_resnet_v1",
                )
            )
        ),
    )

    sface_results = [{"bbox": (10, 10, 30, 30), "embedding": np.zeros(128), "quality": 0.9}]
    out = fee._supplement_with_scrfd(
        image=np.zeros((480, 480, 3), dtype=np.uint8),
        sface_results=sface_results,
        min_confidence=0.2,
        embedding_confidence=0.65,
        min_embedding_quality=0.25,
    )
    assert out == sface_results, (
        f"dlib block must preserve SFace results, not discard them; got {out!r}"
    )

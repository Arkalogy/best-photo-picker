"""Load-time license gate for the two DEFAULT face models — YuNet
(``opencv_yunet``) and SFace (``sface_yunet``).

Both are gated at *download* time via ``download_file``, but their
ensure-helpers return early on a cache hit BEFORE reaching the download
path, and neither has any other load-time check. That left a hole: once
the weights were on disk, a revoked or absent acceptance — or weights
that arrived by any path other than the gated download (backup restore,
copied machine, manual drop) — would load unchecked. The other five
gated models (buffalo_s, NudeNet, YOLO-pets, dlib, LaMa) re-check on
every load; these two didn't.

``_enforce_yunet_policy`` / ``_enforce_sface_policy`` close that hole by
running the gate at the top of the ensure-helper, before the cache-hit
early return. These tests pin both the gate and the
cache-doesn't-bypass-it property so a future refactor can't reopen it.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_policy_memos():
    """Both gates memoize a passing check per process. Clear before and
    after each test so ordering can't mask a blocked-path assertion."""
    # SFace's policy memo lives in face_embed_sface_runtime (split out of
    # face_embed for the 500-LOC cap); face_embed re-exports the functions
    # but a re-exported mutable global is a stale snapshot, so reset the
    # real one on the runtime module.
    from bpp.scoring import face_embed_sface_runtime as fsr
    from bpp.scoring import face_yunet as fy

    fy._yunet_gate.reset()
    fsr._sface_gate.reset()
    yield
    fy._yunet_gate.reset()
    fsr._sface_gate.reset()


# ── YuNet ────────────────────────────────────────────────────────────


@pytest.mark.no_preaccept_permissive
def test_yunet_gate_blocks_when_not_accepted(monkeypatch, tmp_path):
    monkeypatch.setenv("BPP_ACCEPTANCE_LOG_PATH", str(tmp_path / "empty.jsonl"))
    from bpp.registry import ModelLoadBlockedError
    from bpp.scoring.face_yunet import _enforce_yunet_policy

    with pytest.raises(ModelLoadBlockedError):
        _enforce_yunet_policy()


def test_yunet_gate_passes_when_accepted():
    """Autouse conftest pre-accepts opencv_yunet; the gate passes."""
    from bpp.scoring.face_yunet import _enforce_yunet_policy

    _enforce_yunet_policy()  # must not raise


@pytest.mark.no_preaccept_permissive
def test_cached_weights_do_not_bypass_yunet_gate(monkeypatch, tmp_path):
    """The fix: the gate runs BEFORE the cache-hit early return. Simulate
    weights already on disk and prove ``_ensure_yunet_model`` still
    returns False without acceptance — and never reaches ``verify_existing``
    (which would mean the gate was bypassed)."""
    monkeypatch.setenv("BPP_ACCEPTANCE_LOG_PATH", str(tmp_path / "empty.jsonl"))
    from bpp.scoring import face_yunet as fy

    # Pretend the cache is warm — pre-fix this short-circuited to True.
    monkeypatch.setattr(fy.os.path, "exists", lambda _p: True)

    def _boom(*_a, **_k):
        raise AssertionError("verify_existing reached — the gate was bypassed")

    monkeypatch.setattr("bpp.utils.download.verify_existing", _boom)
    assert fy._ensure_yunet_model() is False


# ── SFace ────────────────────────────────────────────────────────────


@pytest.mark.no_preaccept_permissive
def test_sface_gate_blocks_when_not_accepted(monkeypatch, tmp_path):
    monkeypatch.setenv("BPP_ACCEPTANCE_LOG_PATH", str(tmp_path / "empty.jsonl"))
    from bpp.registry import ModelLoadBlockedError
    from bpp.scoring.face_embed import _enforce_sface_policy

    with pytest.raises(ModelLoadBlockedError):
        _enforce_sface_policy()


def test_sface_gate_passes_when_accepted():
    """Autouse conftest pre-accepts sface_yunet; the gate passes."""
    from bpp.scoring.face_embed import _enforce_sface_policy

    _enforce_sface_policy()  # must not raise


@pytest.mark.no_preaccept_permissive
def test_cached_weights_do_not_bypass_sface_gate(monkeypatch, tmp_path):
    monkeypatch.setenv("BPP_ACCEPTANCE_LOG_PATH", str(tmp_path / "empty.jsonl"))
    # _ensure_sface_model + its globals live in the runtime module now.
    from bpp.scoring import face_embed_sface_runtime as fsr

    monkeypatch.setattr(fsr.os.path, "exists", lambda _p: True)

    def _boom(*_a, **_k):
        raise AssertionError("verify_existing reached — the gate was bypassed")

    monkeypatch.setattr("bpp.utils.download.verify_existing", _boom)
    assert fsr._ensure_sface_model() is False


# ── Both gates memoize the passing check (per-process, like dlib) ─────


def test_gates_memoize_passing_check(monkeypatch):
    """Once passed, the gate must not re-read the acceptance log — the
    detector/recognizer is rebuilt per image size, so the ensure-helper
    can run several times per worker."""
    import bpp.registry as reg
    from bpp.scoring import face_embed_sface_runtime as fsr
    from bpp.scoring import face_yunet as fy

    calls = {"n": 0}

    def _counting_enforce(_model_id):
        calls["n"] += 1

    monkeypatch.setattr(reg, "enforce_load_policy_for", _counting_enforce)

    fy._yunet_gate.reset()
    fy._enforce_yunet_policy()
    fy._enforce_yunet_policy()
    fy._enforce_yunet_policy()
    assert calls["n"] == 1, f"YuNet gate should memoize (1 call), got {calls['n']}"

    calls["n"] = 0
    fsr._sface_gate.reset()
    fsr._enforce_sface_policy()
    fsr._enforce_sface_policy()
    assert calls["n"] == 1, f"SFace gate should memoize (1 call), got {calls['n']}"


def test_reset_hooks_clear_the_policy_memo():
    """The model-registry reset hooks must clear the policy memo, not just
    the availability negative-cache — otherwise a revoke followed by a
    cache reset would let the next load skip the gate (the memo would
    still say "already passed")."""
    from bpp.scoring import face_embed_sface_runtime as fsr
    from bpp.scoring import face_yunet as fy

    fsr._sface_gate._passed = True
    fsr._reset_sface_cache()
    assert fsr._sface_gate.passed is False, "reset must re-arm the SFace gate"

    fy._yunet_gate._passed = True
    fy.reset_yunet_cache()
    assert fy._yunet_gate.passed is False, "reset must re-arm the YuNet gate"

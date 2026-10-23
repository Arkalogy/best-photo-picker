"""Per-photo / per-face model lookups check the license once, not on
every call: the result is cached until the acceptance files change (so
an accept or a revoke still takes effect on the next use). Cached gates
are reset after each test by tests/conftest.py."""

from __future__ import annotations

from unittest.mock import patch

import pytest


def _lookups():
    from bpp.ai import inpainting
    from bpp.scoring import face_embed_buffalo_s, nudity, pets

    return {
        "nudenet": (nudity._nudenet, nudity._get_detector),
        "nudenet status": (nudity._nudenet, nudity.is_active),
        "pets": (pets._yolo, pets._get_session),
        "buffalo_s": (face_embed_buffalo_s._buffalo_s_model, face_embed_buffalo_s._get_session),
        "lama": (inpainting._LAMA, inpainting._get_model),
    }


@pytest.mark.parametrize("which", ["nudenet", "nudenet status", "pets", "buffalo_s", "lama"])
def test_repeated_lookups_check_the_license_once(monkeypatch, which):
    from bpp.scoring import nudity

    singleton, lookup = _lookups()[which]
    monkeypatch.setattr(singleton, "_get_loaded", lambda: object())
    monkeypatch.setattr(nudity, "is_on_disk", lambda: True)
    with patch("bpp.registry.enforce_load_policy_for") as check:
        for _ in range(5):
            lookup()
    assert check.call_count == 1, which


@pytest.mark.parametrize("which", ["nudenet", "pets", "buffalo_s", "lama"])
def test_a_refused_license_still_refuses_every_time(monkeypatch, which):
    from bpp.registry import ModelLoadBlockedError
    from bpp.registry.policy import ModelLoadDecision, PolicyResult

    singleton, lookup = _lookups()[which]
    monkeypatch.setattr(singleton, "_get_loaded", lambda: object())
    blocked = ModelLoadBlockedError(
        PolicyResult(
            decision=ModelLoadDecision.BLOCKED_NEEDS_ACK,
            reason="not accepted",
            entry_id=singleton.registry_id,
        )
    )
    with patch("bpp.registry.enforce_load_policy_for", side_effect=blocked) as check:
        for _ in range(3):
            with pytest.raises(ModelLoadBlockedError):
                lookup()
    assert check.call_count == 1, which


@pytest.mark.parametrize("which", ["nudenet", "nudenet status", "pets", "buffalo_s", "lama"])
def test_a_license_change_takes_effect_on_the_next_lookup(monkeypatch, which):
    from bpp.registry import ModelLoadBlockedError
    from bpp.registry.policy import ModelLoadDecision, PolicyResult
    from bpp.scoring import model_load_gate, nudity

    singleton, lookup = _lookups()[which]
    monkeypatch.setattr(singleton, "_get_loaded", lambda: object())
    monkeypatch.setattr(nudity, "is_on_disk", lambda: True)
    files = {"version": 1}  # stands in for the acceptance files' identity
    monkeypatch.setattr(model_load_gate, "_inputs_signature", lambda: (files["version"],))
    with patch("bpp.registry.enforce_load_policy_for"):
        lookup()  # accepted
    files["version"] = 2  # the user revoked it
    revoked = ModelLoadBlockedError(
        PolicyResult(
            decision=ModelLoadDecision.BLOCKED_NEEDS_ACK,
            reason="revoked",
            entry_id=singleton.registry_id,
        )
    )
    with patch("bpp.registry.enforce_load_policy_for", side_effect=revoked):
        if which == "nudenet status":
            assert nudity.is_active() is False
        else:
            with pytest.raises(ModelLoadBlockedError):
                lookup()


def test_a_lookup_reads_the_license_files_once(monkeypatch):
    """One gate check per lookup — not one for the lookup and another
    inside the loader."""
    from bpp.scoring import model_load_gate, pets

    reads: list = []
    monkeypatch.setattr(model_load_gate, "_inputs_signature", lambda: reads.append(1) or (1,))
    monkeypatch.setattr(pets._yolo, "_get_loaded", lambda: object())
    with patch("bpp.registry.enforce_load_policy_for"):
        for _ in range(3):
            pets._get_session()
    assert len(reads) == 3


def test_a_cached_refusal_does_not_keep_the_failing_stack_alive():
    from bpp.registry import ModelLoadBlockedError
    from bpp.registry.policy import ModelLoadDecision, PolicyResult
    from bpp.scoring.model_load_gate import MemoizedLoadGate

    gate = MemoizedLoadGate("ultralytics_yolov11n_pets")
    result = PolicyResult(
        decision=ModelLoadDecision.BLOCKED_NEEDS_ACK, reason="no", entry_id=gate.registry_id
    )
    with (
        patch("bpp.registry.enforce_load_policy_for", side_effect=ModelLoadBlockedError(result)),
        pytest.raises(ModelLoadBlockedError),
    ):
        gate.enforce()
    assert gate._blocked is not None
    assert not isinstance(gate._blocked[1], BaseException)
    with pytest.raises(ModelLoadBlockedError) as again:
        gate.enforce()
    assert again.value.result is result


def test_replacing_a_registry_entry_rechecks_its_license():
    from bpp.registry.model_registry import get_entry, register_entry
    from bpp.scoring import pets

    with patch("bpp.registry.enforce_load_policy_for"):
        pets._yolo.enforce_license()
    assert pets._yolo._gate.passed
    register_entry(get_entry(pets._yolo.registry_id))
    assert not pets._yolo._gate.passed


def test_reset_rechecks_the_license():
    from bpp.scoring import pets

    with patch("bpp.registry.enforce_load_policy_for"):
        pets._yolo.enforce_license()
    pets._yolo.reset()
    assert not pets._yolo._gate.passed

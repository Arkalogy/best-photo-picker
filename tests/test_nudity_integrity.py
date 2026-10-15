"""NudeNet integrity failures are loud: they propagate instead of
degrading to "not sensitive". Split out of test_nudity.py for the
500-LOC cap."""

from __future__ import annotations

from unittest.mock import MagicMock, patch  # noqa: F401

import pytest

# ─── Integrity-failure propagation ───────────────────────────────


def _hide_nudenet_package(monkeypatch) -> None:
    """Make ``import nudenet`` fail for this test. BPP runs the NudeNet
    weights with its own ONNX runner, so these integrity tests must
    pass without the (AGPL) ``nudenet`` package — even on a dev venv
    that still has it installed."""
    import sys

    monkeypatch.setitem(sys.modules, "nudenet", None)
    with pytest.raises(ImportError):
        import nudenet  # noqa: F401


class TestIntegrityFailurePropagates:
    """A tampered NudeNet model must NOT silently degrade into a 0.0
    score. If ModelSingleton.get() catches the SHA-mismatch exception
    broadly and returns None, score_nudity catches the resulting
    None-detector errors and returns 0.0.

    Net effect: every NSFW photo would score "safe" with no diagnostic.
    Defeats the entire point of pinning the SHA. These tests lock the
    new contract — integrity failures propagate.
    """

    @pytest.fixture(autouse=True)
    def _accept_nudenet_first(self):
        """Record the acceptance row for NudeNet so the policy gate
        in ``_get_detector`` passes through. These tests are about
        integrity failures AFTER the user has accepted the
        click-through; without the row, the gate fires first and
        the integrity path is never reached."""
        from bpp.registry import (
            UseContext,
            confirm_acceptance,
            get_entry,
            prepare_acceptance,
            set_use_context,
            utc_now_iso,
        )

        set_use_context(UseContext.PERSONAL, set_via="test")
        entry = get_entry("nudenet_320n")
        if entry is None:
            return  # registry resetter ran; nothing to gate
        draft = prepare_acceptance(entry, use_context=UseContext.PERSONAL)
        confirm_acceptance(
            draft,
            checkbox_responses={cb_id: True for cb_id, _ in draft.required_checkboxes},
            accepted_at=utc_now_iso(),
            separate_rights_asserted=False,
            source_of_rights_note="",
        )

    def _patched_singleton(self, monkeypatch):
        """Reset the module-level _nudenet singleton so each test
        observes a fresh init attempt with the patched verifier.

        All attribute mutations go through ``monkeypatch.setattr`` so
        they auto-revert after the test. Earlier versions of this
        helper assigned directly, which leaked state across tests and
        caused random-order flakes (see Randomized workflow output).

        Also resets ``import_check`` to a no-op so a previous test's
        poisoned ``import_check`` doesn't short-circuit ``.get()``
        before the patched ``create_fn`` can raise.
        """
        from bpp.scoring import nudity as nud_mod
        from bpp.scoring.model_base import ModelIntegrityError

        # Stub create_fn to raise ModelIntegrityError on init
        def _create_fn_raises(_path):
            raise ModelIntegrityError("test: simulated integrity failure")

        # Replace the singleton's create_fn + reset state. Using
        # monkeypatch.setattr so all changes revert at test teardown.
        monkeypatch.setattr(nud_mod._nudenet, "create_fn", _create_fn_raises)
        # Never reach the real cache or the network from a unit test.
        monkeypatch.setattr(nud_mod._nudenet, "ensure_model", lambda: None)
        monkeypatch.setattr(nud_mod._nudenet, "model_path", None)
        monkeypatch.setattr(nud_mod._nudenet, "import_check", lambda: None)
        monkeypatch.setattr(nud_mod._nudenet, "_instance", None)
        monkeypatch.setattr(nud_mod._nudenet, "_available", None)
        return nud_mod

    def test_singleton_get_propagates_integrity_error(self, monkeypatch):
        """ModelSingleton.get() must NOT swallow ModelIntegrityError
        as 'unavailable'. The exception is loud by design — caller
        decides whether to abort the operation or surface to user."""
        _hide_nudenet_package(monkeypatch)

        from bpp.scoring.model_base import ModelIntegrityError

        nud_mod = self._patched_singleton(monkeypatch)

        with pytest.raises(ModelIntegrityError, match="simulated integrity"):
            nud_mod._nudenet.get()

    def test_score_nudity_does_not_swallow_integrity_error(self, monkeypatch):
        """The previous bug: score_nudity returned 0.0 because get()
        returned None. Now get() raises, and score_nudity must let
        the error propagate (NOT catch via the broad except)."""
        _hide_nudenet_package(monkeypatch)

        from bpp.scoring.model_base import ModelIntegrityError

        nud_mod = self._patched_singleton(monkeypatch)

        with pytest.raises(ModelIntegrityError):
            nud_mod.score_nudity("/tmp/nonexistent.jpg")

    def test_singleton_does_not_mark_unavailable_on_integrity(self, monkeypatch):
        """A retry shouldn't be possible (the bytes are wrong, no fix
        without operator action) but we also shouldn't mark the
        singleton as cached-unavailable — that would silently
        re-degrade a future call to 'feature off' instead of
        re-raising. Verify _available stays None after the raise."""
        _hide_nudenet_package(monkeypatch)

        from bpp.scoring.model_base import ModelIntegrityError

        nud_mod = self._patched_singleton(monkeypatch)

        with pytest.raises(ModelIntegrityError):
            nud_mod._nudenet.get()
        assert nud_mod._nudenet._available is None, (
            "Integrity failure must not poison the singleton's "
            "_available cache; a retry should re-attempt and re-raise."
        )

    def test_optional_dep_missing_still_returns_none(self, monkeypatch):
        """Inverse: a normal optional-dep missing failure (ImportError
        from import_check) must STILL behave as before — return None
        from get(), score_nudity returns 0.0 quietly. Don't make all
        failure modes loud, only integrity ones."""
        from bpp.scoring import nudity as nud_mod

        # Force import_check to fail with ImportError
        def _fail_import():
            raise ImportError("test: simulated missing dep")

        # monkeypatch.setattr so all mutations revert after the test —
        # leaving import_check=raises and _available=False here used to
        # poison the next integrity test in random order.
        monkeypatch.setattr(nud_mod._nudenet, "import_check", _fail_import)
        monkeypatch.setattr(nud_mod._nudenet, "_instance", None)
        monkeypatch.setattr(nud_mod._nudenet, "_available", None)

        # get() should swallow ImportError and return None
        assert nud_mod._nudenet.get() is None
        # And subsequent score_nudity returns 0.0 cleanly (None
        # detector → early return)
        assert nud_mod.score_nudity("/tmp/whatever.jpg") == 0.0

    def test_process_one_propagates_integrity_error(self, monkeypatch, tmp_path):
        """End-to-end: the analyze worker's per-image entry point
        (process_one) must propagate ModelIntegrityError instead of
        catching it via the broad `except Exception`. Otherwise the
        worker would log "failed to process X" and continue producing
        0.0 nudity scores for every subsequent photo."""
        import pytest

        from bpp.scoring import aggregate
        from bpp.scoring.model_base import ModelIntegrityError

        # Stub the cache lookup to skip DB I/O (we're testing the
        # exception path, not the cache plumbing). Returning None
        # forces the analyze_single_image branch.
        monkeypatch.setattr(aggregate, "_get_cached", lambda db_path, fp, size, mtime: None)

        def _analyze_raises(*args, **kwargs):
            raise ModelIntegrityError("test: simulated mid-pipeline integrity")

        monkeypatch.setattr(aggregate, "analyze_single_image", _analyze_raises)

        # Make a fake image so _cache_key (os.stat) doesn't trip
        from PIL import Image

        img_path = str(tmp_path / "x.jpg")
        Image.new("RGB", (10, 10)).save(img_path, "JPEG")

        with pytest.raises(ModelIntegrityError):
            aggregate.process_one((img_path, 1024, str(tmp_path / "test.db")))

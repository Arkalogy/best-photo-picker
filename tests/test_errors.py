"""P7 — :class:`BppError` hierarchy + structured response envelope."""

from __future__ import annotations

import pytest

from bpp.errors import (
    BppError,
    ConflictError,
    ForbiddenError,
    IntegrityError,
    NotFoundError,
    ResourceExhaustedError,
    ValidationError,
)


# Spawn-friendly module-level target for T1.5 cross-process pickle test.
# Nested-function targets fail with AttributeError under spawn because
# the start method re-imports this module by name and looks the target
# up there.
def _raise_face_cap_in_child(q) -> None:
    from bpp.db.face_queries import FaceEmbeddingsTooLarge

    q.put(FaceEmbeddingsTooLarge(count=12345, cap=10000))


class TestBaseClass:
    def test_default_http_status_and_code(self):
        e = BppError("oops")
        assert e.http_status == 500
        assert e.code == "internal_error"

    def test_str_returns_message(self):
        assert str(BppError("oops")) == "oops"

    def test_context_is_dict(self):
        e = BppError("oops", photo_id=42, count=3)
        assert e.context == {"photo_id": 42, "count": 3}

    def test_raise_and_catch_as_base(self):
        with pytest.raises(BppError):
            raise BppError("oops")


class TestSubclasses:
    @pytest.mark.parametrize(
        "cls,expected_status,expected_code",
        [
            (ValidationError, 400, "validation_error"),
            (NotFoundError, 404, "not_found"),
            (ForbiddenError, 403, "forbidden"),
            (ConflictError, 409, "conflict"),
            (ResourceExhaustedError, 503, "resource_exhausted"),
            (IntegrityError, 500, "integrity_error"),
        ],
    )
    def test_subclass_attributes(self, cls, expected_status, expected_code):
        e = cls("descriptive message")
        assert e.http_status == expected_status
        assert e.code == expected_code
        assert isinstance(e, BppError)

    def test_subclasses_inherit_to_dict(self):
        e = ValidationError("bad field", field="name", reason="empty")
        envelope = e.to_dict()
        assert envelope["error"] == "bad field"
        assert envelope["code"] == "validation_error"
        assert envelope["context"] == {"field": "name", "reason": "empty"}

    def test_empty_message_falls_back_to_code(self):
        e = NotFoundError()
        envelope = e.to_dict()
        # Empty `str(e)` falls back to the code so the response always
        # carries a non-empty error field.
        assert envelope["error"] == "not_found"

    def test_empty_context_omitted_from_envelope(self):
        e = NotFoundError("photo gone")
        envelope = e.to_dict()
        assert "context" not in envelope


class TestVirtualSubclasses:
    """The five pre-P7 exception classes are virtual subclasses —
    ``isinstance(e, BppError)`` returns True even though they don't
    actually subclass BppError. This makes the migration to
    ``except BppError`` safe without touching their existing raisers
    or catchers."""

    def test_config_validation_error_isinstance_bpp_error(self):
        from bpp.config_schema import ConfigValidationError

        # ConfigValidationError needs (key, reason) — match its signature.
        e = ConfigValidationError("max_long_side", "must be > 0")
        assert isinstance(e, BppError)

    def test_serving_lock_error_isinstance_bpp_error(self):
        from bpp.utils.serving_lock import ServingLockError

        assert isinstance(ServingLockError("locked"), BppError)

    def test_model_integrity_error_isinstance_bpp_error(self):
        from bpp.scoring.model_base import ModelIntegrityError

        assert isinstance(ModelIntegrityError("sha mismatch"), BppError)

    def test_face_embeddings_too_large_isinstance_bpp_error(self):
        from bpp.db.face_queries import FaceEmbeddingsTooLarge

        assert isinstance(FaceEmbeddingsTooLarge(100, 50), BppError)

    def test_clip_embeddings_too_large_isinstance_bpp_error(self):
        from bpp.db.clip import ClipEmbeddingsTooLarge

        assert isinstance(ClipEmbeddingsTooLarge(100, 50), BppError)

    def test_can_catch_legacy_exceptions_as_bpp_error(self):
        """The migration story: code that wants to handle "any
        recoverable bpp error" can now do ``except BppError`` and
        catch the legacy classes too."""
        from bpp.scoring.model_base import ModelIntegrityError

        caught = False
        try:
            raise ModelIntegrityError("sha mismatch")
        except BppError:
            caught = True
        assert caught


class TestEnvelopeShape:
    def test_envelope_keys(self):
        e = ValidationError("bad")
        envelope = e.to_dict()
        # Must always have these two top-level keys.
        assert set(envelope.keys()) == {"error", "code"}

    def test_envelope_with_context(self):
        e = ConflictError("can't merge", primary=1, target=2)
        envelope = e.to_dict()
        assert set(envelope.keys()) == {"error", "code", "context"}
        assert envelope["context"] == {"primary": 1, "target": 2}


# ── T1.5: pickle round-trip — BppError subclasses across subprocess boundary ──


class TestBppErrorPickling:
    """T1.5: BppError subclasses must survive pickle so a subprocess
    worker can raise one and the parent re-raises it identically.

    A BppError subclass with extra ``__init__`` parameters that doesn't
    define ``__reduce__`` will fail to unpickle — ``Exception.__reduce__``
    re-invokes ``__init__(*args)`` where ``args`` is ``self.args``, and
    if the subclass took different args at construction time, this
    breaks. Currently :class:`BppError` calls ``super().__init__(message)``
    so ``self.args == (message,)`` and the legacy subclasses work — but
    nothing pins this contract.

    Without the test, a future refactor that adds positional ``__init__``
    args (or removes the ``super().__init__(message)`` call) would
    silently break subprocess error propagation: the worker raises
    ``ConflictError(primary=1)`` (no positional arg), pickle dies
    inside ``multiprocessing.Queue.put`` with a cryptic
    ``TypeError: __init__() missing required positional argument``,
    and the parent sees the queue close instead of the real failure.
    """

    @pytest.mark.parametrize(
        "cls",
        [
            BppError,
            ValidationError,
            NotFoundError,
            ForbiddenError,
            ConflictError,
            ResourceExhaustedError,
            IntegrityError,
        ],
    )
    def test_round_trip_via_pickle(self, cls):
        import pickle

        original = cls("the message")
        restored = pickle.loads(pickle.dumps(original))
        assert isinstance(restored, cls)
        assert str(restored) == "the message"
        assert restored.code == cls.code
        assert restored.http_status == cls.http_status

    def test_round_trip_preserves_envelope(self):
        """User-facing envelope must survive the round-trip — the parent
        process serializes ``to_dict()`` into the response, so silent
        loss of ``context`` would corrupt the API surface."""
        import pickle

        original = ConflictError("can't merge", primary=1, target=2)
        restored = pickle.loads(pickle.dumps(original))
        assert restored.to_dict() == original.to_dict()

    def test_face_embeddings_too_large_round_trip(self):
        """The legacy subclass takes (count, cap) positional. Multi-base
        inheritance + custom ``__init__`` is the historical pickle
        booby-trap; this is the regression gate."""
        import pickle

        from bpp.db.face_queries import FaceEmbeddingsTooLarge

        original = FaceEmbeddingsTooLarge(count=12345, cap=10000)
        restored = pickle.loads(pickle.dumps(original))
        assert isinstance(restored, FaceEmbeddingsTooLarge)
        assert isinstance(restored, BppError)
        assert str(restored) == str(original)

    def test_clip_embeddings_too_large_round_trip(self):
        import pickle

        from bpp.db.clip import ClipEmbeddingsTooLarge

        original = ClipEmbeddingsTooLarge(count=250_000, cap=200_000)
        restored = pickle.loads(pickle.dumps(original))
        assert isinstance(restored, ClipEmbeddingsTooLarge)
        assert isinstance(restored, BppError)
        assert str(restored) == str(original)

    def test_face_embeddings_too_large_survives_spawn_subprocess(self):
        """End-to-end: a spawn child raises the legacy exception, the
        parent receives it via mp.Queue (which pickles internally), and
        unpickles back to the same class with intact ``count`` / ``cap``.

        This is the actual production path: scoring / analyze workers
        run in spawn children, and ``BoundedSubprocessRunner._drain``
        reads structured fatal_error messages off the queue. A pickle
        failure inside the queue ``put`` collapses the worker silently
        before the parent ever sees the error.
        """
        import multiprocessing as mp

        ctx = mp.get_context("spawn")
        q: mp.Queue = ctx.Queue()
        proc = ctx.Process(target=_raise_face_cap_in_child, args=(q,))
        proc.start()
        proc.join(timeout=10)
        assert proc.exitcode == 0, (
            f"spawn child crashed before queuing the exception (exitcode={proc.exitcode})"
        )
        from bpp.db.face_queries import FaceEmbeddingsTooLarge

        restored = q.get(timeout=1)
        assert isinstance(restored, FaceEmbeddingsTooLarge)
        assert isinstance(restored, BppError)
        assert restored.count == 12345
        assert restored.cap == 10000


# ── T1.6: MRO check for the multi-base legacy embedding-cap classes ──


class TestEmbeddingCapMRO:
    """T1.6: ``FaceEmbeddingsTooLarge(_ResourceExhaustedError, RuntimeError)``
    and the matching CLIP class use multiple inheritance to keep
    ``except RuntimeError`` callers working while also being a
    :class:`BppError` subclass. The MRO determines:

    * which ``__init__`` runs on construction
    * which ``http_status`` / ``code`` get inherited
    * whether ``isinstance(obj, RuntimeError)`` is True (pre-P7 callers)
    * whether ``isinstance(obj, BppError)`` is True (post-P7 callers)

    A future refactor that swaps the base order, or replaces
    ``_ResourceExhaustedError`` with a non-Exception mixin, would
    break one of these silently. This test pins the MRO contract.
    """

    def test_face_embeddings_too_large_mro(self):
        from bpp.db.face_queries import FaceEmbeddingsTooLarge

        mro = FaceEmbeddingsTooLarge.__mro__
        # Both ancestors must be reachable.
        assert ResourceExhaustedError in mro, (
            "FaceEmbeddingsTooLarge must inherit ResourceExhaustedError "
            "so it gets the 503 envelope automatically"
        )
        assert BppError in mro, "must be catchable as BppError"
        assert RuntimeError in mro, (
            "pre-P7 ``except RuntimeError`` callers must keep working — "
            "this is the back-compat contract"
        )
        # Cross-check: dual-isinstance contract.
        e = FaceEmbeddingsTooLarge(count=100, cap=50)
        assert isinstance(e, BppError)
        assert isinstance(e, RuntimeError)
        assert isinstance(e, ResourceExhaustedError)
        # http_status / code are inherited from ResourceExhaustedError
        # except `code` which the class overrides.
        assert e.http_status == 503
        assert e.code == "face_embeddings_too_large"

    def test_clip_embeddings_too_large_mro(self):
        from bpp.db.clip import ClipEmbeddingsTooLarge

        mro = ClipEmbeddingsTooLarge.__mro__
        assert ResourceExhaustedError in mro
        assert BppError in mro
        assert RuntimeError in mro

        e = ClipEmbeddingsTooLarge(count=300_000, cap=200_000)
        assert isinstance(e, BppError)
        assert isinstance(e, RuntimeError)
        assert isinstance(e, ResourceExhaustedError)
        assert e.http_status == 503
        assert e.code == "clip_embeddings_too_large"

    def test_face_and_clip_caps_are_distinct_branches(self):
        """The two caps must not be confusable — they live in different
        modules for a reason (one is the face load, one is the CLIP
        load) and an ``except FaceEmbeddingsTooLarge`` must NOT catch
        a ClipEmbeddingsTooLarge."""
        from bpp.db.clip import ClipEmbeddingsTooLarge
        from bpp.db.face_queries import FaceEmbeddingsTooLarge

        assert not issubclass(FaceEmbeddingsTooLarge, ClipEmbeddingsTooLarge)
        assert not issubclass(ClipEmbeddingsTooLarge, FaceEmbeddingsTooLarge)
        # But both share the ResourceExhaustedError branch so the
        # common-handler catch works.
        common_ancestor = ResourceExhaustedError
        assert issubclass(FaceEmbeddingsTooLarge, common_ancestor)
        assert issubclass(ClipEmbeddingsTooLarge, common_ancestor)

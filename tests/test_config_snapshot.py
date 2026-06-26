"""Picklability gate for ``ConfigSnapshot`` under ``multiprocessing.spawn``.

P0 of refactor-plan.md. The whole point of ``ConfigSnapshot`` is that the
parent flattens a live :class:`Config` (which holds a bound method that
drags a ``mappingproxy`` through pickle) into a plain-dict-backed
dataclass *before* the spawn child sees it. If a future contributor
adds a bound method or non-picklable attribute and forgets to flatten,
the spawn child crashes silently before its worker function runs and
the parent sees a queue timeout instead of a useful traceback.

These tests catch that class of regression at unit-test time — they
actually fire a ``multiprocessing.Process`` with ``spawn`` start method
(matching production) and round-trip the snapshot through the queue.

Sibling gate at the same layer: tests/test_face_thread_safety.py
"""

from __future__ import annotations

import multiprocessing
import pickle
import queue

from bpp.utils.config_snapshot import ConfigSnapshot


def _child_returns_snapshot(snap: ConfigSnapshot, q: multiprocessing.Queue) -> None:
    """Spawn target — verify the snapshot survives pickling and put its
    values back on the queue so the parent can compare."""
    q.put(("ok", dict(snap.values), type(snap).__name__))


class TestConfigSnapshotPickleable:
    """If pickle fails for ConfigSnapshot, the entire scoring/face-extract
    subprocess flow regresses to a queue-timeout failure mode."""

    def test_dataclass_pickle_roundtrip(self):
        snap = ConfigSnapshot(values={"k": 50, "seed": 42, "model_clip": True})
        rt = pickle.loads(pickle.dumps(snap))
        assert rt.values == snap.values

    def test_snapshot_survives_spawn_subprocess(self):
        """The end-to-end test — spawn an actual child with the spawn
        start method (matching production analyze_scoring /
        analyze_face_extract), pass the snapshot through, and verify
        the child can read the values."""
        ctx = multiprocessing.get_context("spawn")
        q: multiprocessing.Queue = ctx.Queue()

        snap = ConfigSnapshot(
            values={
                "k": 50,
                "seed": 42,
                "max_long_side": 1024,
                "face_detection_confidence": 0.3,
                "model_clip": True,
                "model_pets": False,
            }
        )

        proc = ctx.Process(target=_child_returns_snapshot, args=(snap, q))
        proc.start()
        try:
            tag, received_values, type_name = q.get(timeout=30)
        except queue.Empty:
            proc.kill()
            proc.join(timeout=5)
            raise AssertionError(
                "spawn child did not put result on queue within 30s — "
                "likely crashed before the worker function ran. This is "
                "the silent-pickle-failure regression the gate exists to catch."
            ) from None

        proc.join(timeout=10)
        assert proc.exitcode == 0, f"spawn child exited abnormally: {proc.exitcode}"
        assert tag == "ok"
        assert received_values == snap.values
        assert type_name == "ConfigSnapshot"

    def test_from_live_idempotent_on_plain_dict(self):
        """Passing a plain dict in returns a snapshot wrapping a defensive copy."""
        d = {"a": 1, "b": 2}
        snap = ConfigSnapshot.from_live(d)
        assert snap.values == d
        # Mutating the original must not affect the snapshot
        d["a"] = 999
        assert snap.values["a"] == 1

    def test_from_live_handles_dict_subclass_with_as_dict(self):
        """Live ``Config`` exposes ``as_dict()`` — confirm we use that
        path when available rather than the items() fallback."""

        class FakeConfig:
            def __init__(self) -> None:
                self.calls = 0

            def as_dict(self) -> dict[str, int]:
                self.calls += 1
                return {"x": 7}

        fc = FakeConfig()
        snap = ConfigSnapshot.from_live(fc)
        assert snap.values == {"x": 7}
        assert fc.calls == 1


class TestConfigSnapshotReadAPI:
    """Snapshot must support the same read shape consumers expect."""

    def test_get_with_default(self):
        snap = ConfigSnapshot(values={"a": 1})
        assert snap.get("a") == 1
        assert snap.get("missing", "default") == "default"

    def test_getitem(self):
        snap = ConfigSnapshot(values={"a": 1})
        assert snap["a"] == 1

    def test_contains(self):
        snap = ConfigSnapshot(values={"a": 1})
        assert "a" in snap
        assert "missing" not in snap

    def test_frozen_dataclass_blocks_field_reassignment(self):
        """A future contributor shouldn't be able to mutate ``snap.values =
        new_dict`` and accidentally desync the picklable snapshot. The
        underlying dict can still be mutated (frozen=True doesn't deep-
        freeze) but reassignment is blocked."""
        import dataclasses

        snap = ConfigSnapshot(values={"a": 1})
        try:
            snap.values = {"b": 2}  # type: ignore[misc]
        except dataclasses.FrozenInstanceError:
            return
        raise AssertionError("ConfigSnapshot must be frozen=True")

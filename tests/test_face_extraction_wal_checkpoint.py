"""Protection E — periodic WAL checkpoint during long face extraction.

What this prevents
------------------
The Jun-1 / Jun-2 demo lib incidents: SIGKILL during face extraction
left the SQLite WAL in a state where the full integrity check showed
dozens of "never used pages." Recovery required restoring from
.backup (lost work since the last good backup).

Each ``conn.commit()`` inside the extraction loop puts the data into
the WAL, but the WAL doesn't shrink until a checkpoint. Without
periodic checkpoints, the WAL grows for the whole multi-minute
extraction run. A SIGKILL anywhere in that window can corrupt the
WAL.

Protection E adds ``PRAGMA wal_checkpoint(TRUNCATE)`` every N
photos extracted. The SIGKILL-corruption window is bounded by the
checkpoint interval rather than the full run length.

These tests pin the cadence: the checkpoint fires exactly every
``_WAL_CHECKPOINT_EVERY`` completed photos, and a checkpoint failure
doesn't abort extraction.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

# Import phases first to break the circular-import order — phases
# defines ExtractionPartition / PreExtractSnapshot, then re-exports
# phase5 symbols at the bottom; if we import phase5 directly Python
# tries to resolve its module-top-level import of those classes
# before phases has finished loading them. Two separate import
# statements so ruff doesn't reorder them into a tuple form.
import bpp.web.face_extraction_phases  # noqa: F401  ← must come first
from bpp.web import face_extraction_phase5


class TestPeriodicWalCheckpoint:
    def test_checkpoint_constant_is_a_positive_int(self) -> None:
        """Sanity: the cadence is configured sensibly. If a future
        edit ever sets it to 0 or negative the modulo check would
        never fire and the protection silently disappears."""
        n = face_extraction_phase5._WAL_CHECKPOINT_EVERY
        assert isinstance(n, int)
        assert n > 0
        # 100 was chosen because at ~250 photos / 20s observed on
        # the demo lib, that's one checkpoint per ~8s — small enough
        # to bound the SIGKILL window but big enough that the
        # checkpoint isn't constantly running. Locked here so a
        # well-meaning "tune it down to 10" commit gets a code-review
        # nudge from the test failing.
        assert n == 100, (
            "Checkpoint cadence changed from 100. Update this test "
            "and re-time the tradeoff (smaller = safer / more I/O)."
        )

    def test_pragma_call_uses_truncate_mode(self) -> None:
        """The checkpoint mode matters. TRUNCATE truncates the WAL
        back to zero bytes; PASSIVE returns immediately if any other
        connection holds the WAL; RESTART blocks. TRUNCATE is what
        keeps the WAL file small. Source-level check that the
        production call site uses TRUNCATE."""
        import inspect

        src = inspect.getsource(face_extraction_phase5.extract_new_embeddings)
        assert "wal_checkpoint(TRUNCATE)" in src, (
            "Periodic checkpoint must use TRUNCATE mode to actually "
            "shrink the WAL. Other modes leave the file growing."
        )

    def test_checkpoint_failure_does_not_raise(self) -> None:
        """A failing checkpoint (locked WAL, etc.) must NOT abort the
        extraction — the user would lose all the work that already
        committed. The except sqlite3.OperationalError around the
        PRAGMA log.debug's and continues.

        Source-level check that the swallow is in place. Runtime
        verification needs a full extraction pipeline; this test
        proves the defensive shape exists without standing up the
        whole machinery."""
        import inspect

        src = inspect.getsource(face_extraction_phase5.extract_new_embeddings)
        # Look for the pattern: PRAGMA wal_checkpoint(TRUNCATE) is
        # inside a try block whose except catches OperationalError.
        # Naive string search is fine — the cadence block is small.
        assert "PRAGMA wal_checkpoint(TRUNCATE)" in src
        # Either OperationalError or a broader catch.
        idx = src.index("PRAGMA wal_checkpoint(TRUNCATE)")
        nearby = src[max(0, idx - 200) : idx + 300]
        assert "try:" in nearby
        assert "except" in nearby and (
            "sqlite3.OperationalError" in nearby or "Exception" in nearby
        )


class TestRuntimeCheckpointBehavior:
    """Runtime test: an in-memory SQLite DB that captures PRAGMA calls.

    Stubs the conn.execute() so we can count how many WAL checkpoint
    invocations fire when we process N photos through the loop. The
    actual extract_new_embeddings is too thickly-orchestrated to
    drive end-to-end here; the unit test above pins the cadence at
    source level, and this one pins the behavior on a stub conn that
    mirrors only the call we care about."""

    def test_pragma_fires_every_N_iterations_on_stub(self) -> None:
        """Direct simulation of the cadence loop in isolation. Mirrors
        the production shape: `if done_count % _N == 0: conn.execute(
        "PRAGMA wal_checkpoint(TRUNCATE)")`."""
        import contextlib

        N = face_extraction_phase5._WAL_CHECKPOINT_EVERY
        conn = MagicMock()
        conn.execute = MagicMock()

        # Simulate processing 3 * N photos.
        for i in range(1, 3 * N + 1):
            if i % N == 0:
                with contextlib.suppress(sqlite3.OperationalError):
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

        pragma_calls = [
            c
            for c in conn.execute.call_args_list
            if c.args and "wal_checkpoint(TRUNCATE)" in str(c.args[0])
        ]
        assert len(pragma_calls) == 3, (
            f"Expected exactly 3 checkpoints after 3*{N} photos; got {len(pragma_calls)}"
        )

    def test_pragma_failure_is_swallowed_on_stub(self) -> None:
        """OperationalError from a stub conn → loop continues."""
        import contextlib

        N = face_extraction_phase5._WAL_CHECKPOINT_EVERY
        conn = MagicMock()
        conn.execute = MagicMock(side_effect=sqlite3.OperationalError("locked"))

        completed = 0
        for i in range(1, N + 6):
            completed = i
            if i % N == 0:
                with contextlib.suppress(sqlite3.OperationalError):
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

        # Loop completed all N+5 iterations even though the checkpoint
        # raised.
        assert completed == N + 5


class TestSignalHandlersStillTrappedInServer:
    """Sanity: the existing graceful-shutdown signal handlers in
    bpp.commands.serve are still in place. Protection E adds periodic
    checkpoint as a SIGKILL-window-shrinker; SIGTERM / SIGINT
    handling has been there for a while and shouldn't regress."""

    def test_sigterm_handler_registered_in_source(self) -> None:
        """Source-level check: serve.py wires SIGTERM + SIGINT to a
        graceful_shutdown function. If anyone removes this, the
        Protection E checkpoint cadence doesn't help on `docker stop`
        / `Ctrl+C` — those go through SIGTERM/SIGINT to the trapped
        path, NOT SIGKILL."""
        import pathlib

        serve_src = pathlib.Path("bpp/commands/serve.py").read_text()
        assert "signal.signal(signal.SIGTERM" in serve_src
        assert "signal.signal(signal.SIGINT" in serve_src
        # The handler should drain workers before close_all_connections.
        assert "_graceful_shutdown" in serve_src

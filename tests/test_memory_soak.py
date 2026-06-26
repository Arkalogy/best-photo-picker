"""Memory soak regression — assert no unbounded growth across request cycles.

This test is the regression guardrail for the 2026-05-20 incident where the
desktop app left running 2+ days consumed ~15GB RAM. It does NOT reproduce
multi-day usage (we can't, in a test). Instead it sustains a request load
across multiple cycles and asserts cache sizes do not creep cycle-over-cycle.

If a future change introduces an unbounded cache, dict-keyed accumulator, or
reference cycle that the periodic ``gc.collect()`` can't break, this test
catches the new ratchet.

The thresholds are deliberately generous (caches can grow during warmup) but
strict enough to flag a *new* unbounded pattern.
"""

from __future__ import annotations

import json

import pytest

CYCLES = 3
REQUESTS_PER_CYCLE = 60


@pytest.fixture
def soak_client(tmp_path, monkeypatch):
    """Flask test client over a small fixture library with debug endpoint reachable."""
    from bpp.web.app import create_app

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    lib = tmp_path / "soaklib"
    lib.mkdir()
    analysis = [
        {
            "filepath": f"{lib}/p_{i:03d}.jpg",
            "date": f"2024-{((i % 12) + 1):02d}-{((i % 27) + 1):02d}T12:00:00",
            "date_day": f"2024-{((i % 12) + 1):02d}-{((i % 27) + 1):02d}",
            "date_month": f"2024-{((i % 12) + 1):02d}",
            "file_size": 1024,
            "file_mtime": 1700000000.0,
            "blur_raw": 100.0,
            "blur_score": 0.5,
            "exposure_score": 0.5,
            "face_score": 0.3,
            "face_count": 0,
            "largest_face_ratio": 0.0,
            "face_center_dist": 0.0,
            "composition_score": 0.5,
            "aggregate_score": 0.5,
        }
        for i in range(10)
    ]
    with open(lib / "analysis.json", "w") as f:
        json.dump(analysis, f)

    app = create_app(workdir=str(lib), library_path=str(lib))
    app.config["TESTING"] = True
    client = app.test_client()
    client.get("/api/v1/status")
    return client


def _snapshot(client) -> dict:
    """Take a memory snapshot via /api/v1/debug/memory."""
    resp = client.get("/api/v1/debug/memory")
    assert resp.status_code == 200
    return resp.get_json()


def _run_cycle(client) -> None:
    """Sustain mixed load that exercises caches + workers."""
    for i in range(REQUESTS_PER_CYCLE):
        client.get("/api/v1/photos")
        client.get("/api/v1/albums")
        client.get("/api/v1/status")
        if i % 5 == 0:
            client.get("/api/v1/logs?limit=20")
            client.get("/api/v1/stats")


class TestMemorySoak:
    def test_no_unbounded_cache_growth_across_cycles(self, soak_client):
        """Cache counts must stabilize between cycles 2 and 3.

        Cycle 1 is warmup — caches legitimately fill up.
        Cycles 2 and 3 should be steady-state: any growth indicates an
        unbounded accumulator was introduced.
        """
        _run_cycle(soak_client)  # warmup
        snap_after_warmup = _snapshot(soak_client)

        _run_cycle(soak_client)
        snap_cycle_2 = _snapshot(soak_client)

        _run_cycle(soak_client)
        snap_cycle_3 = _snapshot(soak_client)

        c2 = snap_cycle_2["caches"]
        c3 = snap_cycle_3["caches"]

        # Caches that must be strictly stable (no per-request growth).
        for key in (
            "clip_embeddings_count",
            "thumb_hash_count",
            "edited_ids_count",
            "auto_enhanced_ids_count",
            "face_cluster_map_entries",
        ):
            assert c3[key] == c2[key], (
                f"{key} grew between steady-state cycles: {c2[key]} -> {c3[key]}. "
                f"This indicates a new unbounded cache or accumulator."
            )

        # Log ring buffer is bounded by RING_BUFFER_SIZE (1000). Allow growth
        # within that ceiling — every request logs at INFO level, so it climbs
        # toward the cap during the test and then stays there.
        assert c3["log_ring_buffer"] <= 1000, (
            f"Log ring buffer exceeded RING_BUFFER_SIZE: {c3['log_ring_buffer']}"
        )

        # BPE cache is capped at lru_cache(maxsize=4096) — no risk of growth
        # in this test (we don't run text searches), but assert the cap
        # never gets exceeded if a future change touches the tokenizer path.
        assert c3["bpe_cache_size"] <= 4096

        # GC: uncollectable garbage must not be growing. Some `gc.garbage`
        # is acceptable (it happens with cyclic references involving
        # __del__), but it should not climb every cycle.
        garbage_warmup = snap_after_warmup["gc"]["garbage_objects"]
        garbage_c3 = snap_cycle_3["gc"]["garbage_objects"]
        assert garbage_c3 - garbage_warmup <= 5, (
            f"gc.garbage grew {garbage_warmup} -> {garbage_c3} across cycles. "
            f"A reference cycle with __del__ may have been introduced."
        )

    def test_thread_count_stable(self, soak_client):
        """Active thread count should not climb across cycles.

        Background workers spawn during startup, but request handling
        should reuse them. A new thread per request indicates a leak.
        """
        _run_cycle(soak_client)  # warmup
        snap1 = _snapshot(soak_client)

        _run_cycle(soak_client)
        snap2 = _snapshot(soak_client)

        threads1 = snap1["threads"]["active"]
        threads2 = snap2["threads"]["active"]

        # Allow up to 2 thread variance (worker join/restart, gc thread).
        assert abs(threads2 - threads1) <= 2, (
            f"Thread count drifted: {threads1} -> {threads2} ({snap2['threads']['names']})"
        )

"""parallel_map keeps only a few jobs per worker queued, topping up as
they finish — not one job per item up front — with results unchanged."""

from __future__ import annotations

import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor

import pytest

from bpp.utils import concurrency


class _CountingPool:
    """A thread pool standing in for the process pool, recording the most
    jobs that were queued or running at once."""

    def __init__(self, max_workers):
        self._pool = ThreadPoolExecutor(max_workers=max_workers)
        self._lock = threading.Lock()
        self.in_flight = 0
        self.peak = 0
        self.submitted: list = []
        _CountingPool.last = self

    def submit(self, fn, *args):
        with self._lock:
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
        self.submitted.append(args)
        fut: Future = self._pool.submit(fn, *args)

        def done(_f):
            with self._lock:
                self.in_flight -= 1

        fut.add_done_callback(done)
        return fut

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._pool.shutdown(wait=True, cancel_futures=True)
        return False


def _square(x):
    if x == 13:
        raise ValueError("bad item")
    return x * x


@pytest.fixture
def pool(monkeypatch):
    monkeypatch.setattr(concurrency, "ProcessPoolExecutor", _CountingPool)


def test_only_a_few_jobs_per_worker_are_queued(pool):
    items = list(range(200))
    results = concurrency.parallel_map(_square, items, workers=4)
    assert _CountingPool.last.peak <= 4 * concurrency.QUEUED_PER_WORKER
    assert results == [None if x == 13 else x * x for x in items]  # same order, failures None


def test_small_inputs_still_finish(pool):
    assert concurrency.parallel_map(_square, list(range(12)), workers=8) == [
        None if x == 13 else x * x for x in range(12)
    ]


def test_real_worker_processes_give_ordered_results():
    items = list(range(20))
    assert concurrency.parallel_map(_square, items, workers=2) == [
        None if x == 13 else x * x for x in items
    ]


def _die_on_five(x):
    if x == 5:
        os._exit(1)  # a worker killed (e.g. out of memory)
    return x


def test_a_dead_worker_stops_the_run_with_a_clear_message():
    with pytest.raises(RuntimeError, match="stopped unexpectedly") as err:
        concurrency.parallel_map(_die_on_five, list(range(40)), workers=2)
    assert "re-run" in str(err.value)


def _interrupt_on_three(x):
    if x == 3:
        raise KeyboardInterrupt
    return x


def test_ctrl_c_stops_without_queueing_the_rest(pool):
    with pytest.raises(KeyboardInterrupt):
        concurrency.parallel_map(_interrupt_on_three, list(range(500)), workers=2)
    assert len(_CountingPool.last.submitted) < 50


def test_progress_is_reported_per_item(pool):
    seen: list = []
    concurrency.parallel_map(_square, list(range(30)), workers=3, on_done=lambda: seen.append(1))
    assert len(seen) == 30


class _Fatal(Exception):
    pass


def _fatal_on_two(x):
    if x == 2:
        raise _Fatal("model failed its integrity check")
    return x


@pytest.mark.parametrize("workers", [1, 3])
def test_fatal_errors_stop_the_run_with_any_worker_count(pool, workers):
    with pytest.raises(_Fatal):
        concurrency.parallel_map(
            _fatal_on_two, list(range(30)), workers=workers, fatal_exceptions=(_Fatal,)
        )


@pytest.mark.parametrize("workers", [1, 3])
def test_one_bad_item_gives_none_with_any_worker_count(pool, workers):
    results = concurrency.parallel_map(_square, list(range(20)), workers=workers)
    assert results == [None if x == 13 else x * x for x in range(20)]


def test_cli_analysis_stops_on_a_model_integrity_failure(pool, tmp_path, monkeypatch):
    from bpp.scoring import aggregate
    from bpp.scoring.model_base import ModelIntegrityError

    def tampered(args):
        raise ModelIntegrityError("SHA-256 mismatch")

    monkeypatch.setattr(aggregate, "process_one", tampered)
    with pytest.raises(ModelIntegrityError):
        aggregate.analyze_all([f"/p/{i}.jpg" for i in range(10)], str(tmp_path), {}, workers=3)

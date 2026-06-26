"""Timing utilities for profiling phases."""

from __future__ import annotations

import time
from collections.abc import Generator
from contextlib import contextmanager

from bpp.utils.logging import get_logger

log = get_logger(__name__)


class Timer:
    def __init__(self) -> None:
        self._sections: list[tuple[str, float]] = []
        self._start: float | None = None
        self._name: str = ""

    @contextmanager
    def section(self, name: str) -> Generator[None, None, None]:
        self._name = name
        self._start = time.monotonic()
        try:
            yield
        finally:
            elapsed = time.monotonic() - self._start
            self._sections.append((name, elapsed))
            log.debug("[timer] %s: %.2fs", name, elapsed)

    def summary(self) -> None:
        total = sum(t for _, t in self._sections)
        parts = ", ".join(f"{n}={t:.1f}s" for n, t in self._sections)
        log.info("Timing: %s (total %.1fs)", parts, total)

"""Background export worker — streams per-photo progress over SSE.

L-S3 release-audit followup. Before, the /api/v1/export endpoint ran
the full export loop synchronously inside the Flask request, holding
the HTTP connection open for the entire job. A parent exporting 100
best photos to JPEG at "high" quality saw no UI feedback for 30-60s
and reasonably assumed the app had frozen.

Same shape as ImportWorker / AnalyzeWorker — wraps
:func:`bpp.output.export.export_selected` in a daemon thread, threads
a per-photo callback into the progress queue, and exposes the queue
to the SSE endpoint at /api/v1/export/progress (in bp_photos.py).
The endpoint returns 202 immediately so the browser can start
consuming events; the worker reports done / error / cancelled events
in the same envelope import + analyze already use.
"""

from __future__ import annotations

from typing import Any

from bpp.utils.logging import get_logger
from bpp.web.base_worker import BackgroundWorker

log = get_logger(__name__)


class ExportWorker(BackgroundWorker):
    """Runs export_selected in a background thread, streaming progress.

    Holds the last completed ExportResult on ``last_result`` so the
    endpoint's done-event finalizer can read the counts + disk_error
    category without having to plumb them through the SSE message
    payload (the message carries them too for the streaming case).
    """

    _worker_name = "Export"

    def __init__(self) -> None:
        super().__init__()
        self.last_result: Any = None

    def start(  # type: ignore[override]
        self,
        selected: list[dict[str, Any]],
        analysis: list[dict[str, Any]],
        outdir: str,
        config: dict[str, Any],
        mode: str = "copy",
        gallery: bool = True,
        fmt: str = "original",
        max_size: int | None = None,
        quality: int = 85,
        write_manifest: bool = False,
        write_xmp: bool = False,
        library_path: str = "",
        strip_metadata: bool = True,
    ) -> bool:
        return self._start_thread(
            selected,
            analysis,
            outdir,
            config,
            mode,
            gallery,
            fmt,
            max_size,
            quality,
            write_manifest,
            write_xmp,
            library_path,
            strip_metadata,
        )

    def _run(  # type: ignore[override]
        self,
        selected: list[dict[str, Any]],
        analysis: list[dict[str, Any]],
        outdir: str,
        config: dict[str, Any],
        mode: str,
        gallery: bool,
        fmt: str,
        max_size: int | None,
        quality: int,
        write_manifest: bool,
        write_xmp: bool,
        library_path: str,
        strip_metadata: bool,
    ) -> None:
        # Bookend logs — same pattern as M10 / M11 / Phase 5.
        import time as _time

        from bpp.output.export import export_selected

        _t0 = _time.perf_counter()
        log.info(
            "Export starting: %d photo(s) -> %s (mode=%s, fmt=%s)",
            len(selected),
            outdir,
            mode,
            fmt,
        )
        self._emit({"type": "start", "total": len(selected), "outdir": outdir})

        def _on_progress(current: int, total: int, filename: str) -> None:
            self._emit(
                {
                    "type": "export_progress",
                    "current": current,
                    "total": total,
                    "filename": filename,
                }
            )

        try:
            result = export_selected(
                selected=selected,
                analysis=analysis,
                outdir=outdir,
                mode=mode,
                gallery=gallery,
                config=config,
                fmt=fmt,
                max_size=max_size,
                quality=quality,
                write_manifest=write_manifest,
                write_xmp=write_xmp,
                library_path=library_path,
                strip_metadata=strip_metadata,
                on_progress=_on_progress,
            )
        finally:
            log.info(
                "Export done in %.1fs (-> %s)",
                _time.perf_counter() - _t0,
                outdir,
            )

        self.last_result = result
        self._emit(
            {
                "type": "done",
                "outdir": outdir,
                "count": result.exported,
                "failed": result.failed,
                "disk_error": result.disk_error,
            }
        )

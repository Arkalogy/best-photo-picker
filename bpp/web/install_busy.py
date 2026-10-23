"""What an install has to wait for: the running work an install may break
by rewriting packages."""

from __future__ import annotations

#: How the built-in workers are named to the user; plugins' by their key.
_WORKER_LABELS = {
    "analyze": "analysis",
    "face": "face grouping",
    "import": "import",
    "clip": "smart search indexing",
}


def busy_workers() -> list[str]:
    """Running work that an install may break by rewriting packages: background
    workers that load any installable package (pip may also upgrade shared
    libraries such as numpy; see ``loads_installable_packages``) and model
    downloads (they check their packages as they go)."""
    from bpp.web import model_download_jobs
    from bpp.web.state import get_ctx_or_none

    busy = list(model_download_jobs.running_labels())
    ctx = get_ctx_or_none()
    if ctx is None:
        return busy  # no library open: no workers
    for name, worker in ctx.workers.items():
        loads = getattr(worker, "loads_installable_packages", None) is not False
        # A plugin's worker needn't be a BackgroundWorker: is_alive may be
        # missing, or a method (a Thread).
        alive = getattr(worker, "is_alive", False)
        if callable(alive):
            alive = alive()
        if alive and loads:
            busy.append(_WORKER_LABELS.get(name, name))
    return busy

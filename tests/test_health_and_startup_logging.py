"""Second-pass supportability review (v0.1.3): a failed face-extraction
resume shows in health, the clustering journal handler reports failure,
startup plugin dispatch and the install-note cleanup are logged,
debug/memory reports current and peak memory, and the API docs match."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

from tests.test_face_recovery_thread import _pending_run_with_one_face  # noqa: E402


def test_a_run_that_fails_to_resume_shows_as_failed_in_health(tmp_path, monkeypatch):
    from bpp.web.face_recovery import recover_pending_face_extractions

    app, ctx, _run_id = _pending_run_with_one_face(tmp_path)

    def crash(*a, **k):
        raise RuntimeError("journal unreadable")

    monkeypatch.setattr("bpp.web.face_worker.extract_and_cluster_faces", crash)
    with app.app_context():
        assert recover_pending_face_extractions(ctx.get_conn(), ctx=ctx, cancel_event=None) == 0
    assert ctx.analysis_store.face_recovery_failed is True
    app.config["TESTING"] = True
    with app.test_client() as c:
        health = c.get("/api/v1/health").get_json()
    assert health["checks"]["face_recovery"]["failed"] is True
    assert health["status"] == "degraded"


def test_the_resume_logs_when_it_starts(tmp_path, monkeypatch, caplog):
    from bpp.web import face_extraction_journal as journal
    from bpp.web.app import create_app
    from bpp.web.state_lifecycle import _start_face_recovery

    monkeypatch.setattr(
        "bpp.web.face_worker.recover_pending_face_extractions",
        lambda conn, *, ctx=None, cancel_event=None: 0,
    )
    app = create_app(library_path=str(tmp_path / "lib"))
    ctx = app.extensions["bpp"]
    with app.app_context():
        journal.start_run(ctx.get_conn())
    with caplog.at_level("INFO"):
        _start_face_recovery(ctx)
        ctx.analysis_store.face_recovery_thread.join(5)
    assert "resume starting" in caplog.text


def test_face_clustering_journal_handler_reports_failure(monkeypatch):
    from bpp.db import journal as journal_mod
    from bpp.web.face_recovery import register_face_clustering_recovery

    captured = {}
    monkeypatch.setattr(
        journal_mod,
        "register_recovery_handler",
        lambda kind, fn, **k: captured.setdefault(kind, fn),
    )
    register_face_clustering_recovery()
    handler = captured["face_clustering"]

    def boom(conn):
        raise RuntimeError("identity table locked")

    monkeypatch.setattr("bpp.web.face_worker._reconstruct_identities", boom)
    monkeypatch.setattr("bpp.db.smart_albums.refresh_smart_albums", lambda *a, **k: None)
    assert handler(object(), {}) is False


def test_startup_plugin_dispatch_and_install_note_failures_are_logged():
    app_src = (REPO_ROOT / "bpp" / "web" / "app.py").read_text()
    assert "pass  # best-effort; loader already logs per-plugin failures" not in app_src
    stream = (REPO_ROOT / "bpp" / "web" / "install_stream.py").read_text()
    assert (
        "with contextlib.suppress(Exception):\n                    pip_process.mark_ended("
        not in stream
    )


def test_debug_memory_reports_current_and_peak(tmp_path):
    from bpp.web.app import create_app

    app = create_app(library_path=str(tmp_path / "lib"))
    app.config["TESTING"] = True
    with app.test_client() as c:
        data = c.get("/api/v1/debug/memory").get_json()
    assert data["process"]["peak_rss_mb"] >= data["process"]["rss_mb"] > 0


def test_clip_tokenizer_names_the_reason_the_vocab_is_missing():
    src = (REPO_ROOT / "bpp" / "scoring" / "clip_tokenizer.py").read_text()
    assert "_vocab.get_or_raise()" in src


def test_api_docs_cover_the_health_and_people_shapes():
    docs = (REPO_ROOT / "docs" / "API.md").read_text()
    assert '"missing_scan"' in docs
    assert docs.count("### GET /api/v1/tags/:tag_id/photos") == 1
    clusters = docs[docs.index("### GET /api/v1/faces/clusters") :]
    clusters = clusters[: clusters.index("### POST /api/v1/faces/avatar")]
    assert '"name": "Alice"' not in clusters
    assert "face_count" in clusters and "dismissed_count" in clusters
    assert "worker queues" not in docs

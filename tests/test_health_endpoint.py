"""Tests for the /api/health aggregate endpoint.

Pins the contract that ops + the desktop wrapper rely on:

- Returns 200 even when degraded (callers parse the ``status`` field).
- Reports DB writability + schema version + DB path.
- Reports storage accessibility, latency, and free-space figures.
- Reports worker liveness as a dict keyed by worker name (registry-driven,
  so a future plugin worker shows up automatically).
- Reports pending operation_journal entries with per-kind counts.
- ``status`` aggregates: "ok" when all green; "degraded" when any probe
  reports a problem; never "down" through this code path.
- ``uptime_s`` is monotonically increasing per ctx (resets across switches).
"""

from __future__ import annotations

import os
import time

import pytest


@pytest.fixture
def app(tmp_path):
    from bpp.web.app import create_app

    workdir = str(tmp_path / "workdir")
    os.makedirs(workdir)
    app = create_app(workdir=workdir)
    # Disable the auth gate for the health endpoint — local-loopback only
    # in production, but the gate still rejects un-tokened test requests.
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()


# ── Shape ────────────────────────────────────────────────────────────


class TestShape:
    def test_returns_200(self, client):
        r = client.get("/api/v1/health")
        assert r.status_code == 200

    def test_top_level_fields(self, client):
        r = client.get("/api/v1/health")
        data = r.get_json()
        required = {"status", "uptime_s", "checks"}
        assert set(data.keys()) >= required, (
            f"missing top-level keys: {required - set(data.keys())}"
        )

    def test_checks_groups(self, client):
        data = client.get("/api/v1/health").get_json()
        required = {"db", "storage", "workers", "journals", "collaborators", "phase5"}
        assert set(data["checks"].keys()) >= required, (
            f"missing checks groups: {required - set(data['checks'].keys())}"
        )

    def test_collaborators_surface_analysis_store_and_caches(self, client):
        """T4: the new ``collaborators`` block must expose the P4
        AnalysisStore + ModelCache state so on-call can tell whether
        the phash compute thread is alive, whether CLIP / face-cluster
        caches are populated, and the analysis-store generation
        (which bumps on every switch_library).
        """
        data = client.get("/api/v1/health").get_json()
        collab = data["checks"]["collaborators"]
        # AnalysisStore block.
        assert "analysis_store" in collab, (
            f"analysis_store block missing from collaborators: {collab}"
        )
        as_block = collab["analysis_store"]
        assert {
            "phash_ready",
            "generation",
            "compute_thread_alive",
            "warm_thread_alive",
        } <= set(as_block.keys())
        # Caches block.
        assert "caches" in collab
        caches = collab["caches"]
        assert "clip_ready" in caches
        assert "clip_embeddings_count" in caches


# ── DB check ─────────────────────────────────────────────────────────


class TestDbCheck:
    def test_db_ok_after_ctx_init(self, client):
        data = client.get("/api/v1/health").get_json()
        db = data["checks"]["db"]
        assert db["ok"] is True
        assert db["writable"] is True

    def test_db_reports_schema_version(self, client):
        data = client.get("/api/v1/health").get_json()
        db = data["checks"]["db"]
        # Must match the live schema version (set by init_db migrations)
        from bpp.db.schema import SCHEMA_VERSION

        assert db["schema_version"] == SCHEMA_VERSION

    def test_db_reports_path(self, client):
        data = client.get("/api/v1/health").get_json()
        assert "path" in data["checks"]["db"]
        assert data["checks"]["db"]["path"].endswith(".db")

    def test_db_connection_failure_does_not_leak_path(self, app, client, monkeypatch):
        """R6-M2: when ctx.get_conn() raises, the response must NOT
        echo the exception text — OSError / sqlite3.Error strings
        often include the absolute DB path, the owner's username,
        mount labels, or SQLite-internal hints. The endpoint reports
        a fixed generic string and logs the detail server-side."""
        import json

        ctx = app.extensions["bpp"]
        secret_path = "/Users/alice/Pictures/Private/data/photopicker.db"

        def _boom():
            raise RuntimeError(f"unable to open {secret_path}: permission denied")

        monkeypatch.setattr(ctx, "get_conn", _boom)

        r = client.get("/api/v1/health")
        # Health stays 200 even when degraded — clients read `status`.
        assert r.status_code == 200
        body = json.dumps(r.get_json())
        assert secret_path not in body, "DB path leaked in /api/v1/health response"
        assert "permission denied" not in body, "Raw exception text leaked"
        assert r.get_json()["checks"]["db"]["error"] == "Database connection failed"
        assert r.get_json()["checks"]["db"]["ok"] is False


# ── Storage check ────────────────────────────────────────────────────


class TestStorageCheck:
    def test_storage_accessible_for_existing_workdir(self, client):
        data = client.get("/api/v1/health").get_json()
        storage = data["checks"]["storage"]
        assert storage["accessible"] is True
        assert "latency_ms" in storage

    def test_storage_reports_disk_usage(self, client):
        data = client.get("/api/v1/health").get_json()
        storage = data["checks"]["storage"]
        assert "free_gb" in storage
        assert "total_gb" in storage
        assert storage["free_gb"] >= 0
        assert storage["total_gb"] >= storage["free_gb"]

    def test_storage_path_in_response(self, client):
        data = client.get("/api/v1/health").get_json()
        assert data["checks"]["storage"]["path"]


# ── Workers check ────────────────────────────────────────────────────


class TestWorkersCheck:
    def test_workers_dict_includes_all_registered(self, client):
        data = client.get("/api/v1/health").get_json()
        workers = data["checks"]["workers"]
        # Registry currently registers analyze, face, import, clip
        for required in ("analyze", "face", "import", "clip"):
            assert required in workers, f"missing worker: {required}"

    def test_idle_workers_report_not_alive(self, client):
        data = client.get("/api/v1/health").get_json()
        workers = data["checks"]["workers"]
        # Fresh ctx: no worker has been started, all should be not-alive
        for name, info in workers.items():
            assert info["alive"] is False, f"{name} reported alive on fresh ctx"


# ── Journals check ───────────────────────────────────────────────────


class TestJournalsCheck:
    def test_no_journals_pending_on_fresh_db(self, client):
        data = client.get("/api/v1/health").get_json()
        assert data["checks"]["journals"]["pending"] == 0
        assert data["checks"]["journals"]["kinds"] == {}

    def test_pending_journal_surfaces(self, app, client):
        from bpp.db.journal import journal_start

        ctx = app.extensions["bpp"]
        with app.app_context():
            conn = ctx.get_conn()
            journal_start(conn, "permanent_delete", {"filepaths": ["/foo.jpg"]})

        data = client.get("/api/v1/health").get_json()
        journals = data["checks"]["journals"]
        assert journals["pending"] == 1
        assert journals["kinds"] == {"permanent_delete": 1}

    def test_pending_journals_aggregate_by_kind(self, app, client):
        from bpp.db.journal import journal_start

        ctx = app.extensions["bpp"]
        with app.app_context():
            conn = ctx.get_conn()
            journal_start(conn, "permanent_delete", {"filepaths": ["/a.jpg"]})
            journal_start(conn, "permanent_delete", {"filepaths": ["/b.jpg"]})
            journal_start(conn, "face_clustering", {})

        data = client.get("/api/v1/health").get_json()
        journals = data["checks"]["journals"]
        assert journals["pending"] == 3
        assert journals["kinds"] == {"permanent_delete": 2, "face_clustering": 1}


# ── Aggregate status ─────────────────────────────────────────────────


class TestAggregateStatus:
    def test_status_ok_on_clean_ctx(self, client):
        data = client.get("/api/v1/health").get_json()
        assert data["status"] == "ok"

    def test_status_degraded_when_journal_pending(self, app, client):
        from bpp.db.journal import journal_start

        ctx = app.extensions["bpp"]
        with app.app_context():
            conn = ctx.get_conn()
            journal_start(conn, "permanent_delete", {"filepaths": ["/x.jpg"]})

        data = client.get("/api/v1/health").get_json()
        assert data["status"] == "degraded"


# ── Uptime ───────────────────────────────────────────────────────────


class TestUptime:
    def test_uptime_is_non_negative(self, client):
        data = client.get("/api/v1/health").get_json()
        assert data["uptime_s"] >= 0

    def test_uptime_grows(self, client):
        first = client.get("/api/v1/health").get_json()["uptime_s"]
        time.sleep(0.05)
        second = client.get("/api/v1/health").get_json()["uptime_s"]
        assert second >= first


# ── E2E fixture sentinel ─────────────────────────────────────────────


class TestE2EFixtureSentinel:
    """The mutating e2e helpers refuse to run unless the server reports
    is_fixture=true. Before this guard existed, an e2e run pointed at the
    user's real library left 5 `__e2e_album_*` rows. The contract:
    sentinel file at the library root ⇒ true, absent ⇒ false. Must
    inspect the resolved library root (ctx.dirs['root']), NOT workdir or
    state['library_path'] (which can fall back to the user's default
    library under TESTING and silently green-light real-library writes).
    """

    def _make_app(self, tmp_path, with_sentinel: bool):
        from bpp.web.app import create_app

        lib = tmp_path / ("lib_yes" if with_sentinel else "lib_no")
        lib.mkdir()
        if with_sentinel:
            (lib / ".bpp-e2e-fixture").write_text("test fixture")
        app = create_app(workdir=str(lib), library_path=str(lib))
        app.config["TESTING"] = True
        return app.test_client()

    def test_no_sentinel_reports_false(self, tmp_path):
        c = self._make_app(tmp_path, with_sentinel=False)
        r = c.get("/api/v1/_diag/is_e2e_fixture")
        assert r.status_code == 200
        assert r.get_json() == {"is_fixture": False}

    def test_sentinel_present_reports_true(self, tmp_path):
        c = self._make_app(tmp_path, with_sentinel=True)
        r = c.get("/api/v1/_diag/is_e2e_fixture")
        assert r.status_code == 200
        assert r.get_json() == {"is_fixture": True}, (
            "fixture sentinel must be detected at the library root — "
            "if this fails, the e2e mutation helpers will refuse to run "
            "even against a legitimate fixture, blocking the suite."
        )

    def _make_app_obj(self, tmp_path, with_sentinel: bool):
        from bpp.web.app import create_app

        lib = tmp_path / ("lib_yes" if with_sentinel else "lib_no")
        lib.mkdir()
        if with_sentinel:
            (lib / ".bpp-e2e-fixture").write_text("test fixture")
        app = create_app(workdir=str(lib), library_path=str(lib))
        app.config["TESTING"] = True
        return app, lib

    def test_helper_reflects_sentinel(self, tmp_path):
        """is_e2e_fixture_library() — the function the destructive-endpoint
        rate-limit bypass in app.py gates on. A real library (no sentinel)
        keeps the limiter; a fixture bypasses it so the Playwright suite
        isn't throttled mid-run."""
        from bpp.web.bp_health import _e2e_fixture_cache, is_e2e_fixture_library

        _e2e_fixture_cache.clear()
        app_no, _ = self._make_app_obj(tmp_path, with_sentinel=False)
        with app_no.app_context():
            assert is_e2e_fixture_library() is False

        _e2e_fixture_cache.clear()
        app_yes, _ = self._make_app_obj(tmp_path, with_sentinel=True)
        with app_yes.app_context():
            assert is_e2e_fixture_library() is True

    def test_helper_caches_per_root(self, tmp_path):
        """The result is cached by library root so the gate doesn't stat
        the filesystem on every destructive request. Deleting the sentinel
        after the first lookup must NOT flip the cached value."""
        from bpp.web.bp_health import _e2e_fixture_cache, is_e2e_fixture_library

        _e2e_fixture_cache.clear()
        app_yes, lib = self._make_app_obj(tmp_path, with_sentinel=True)
        with app_yes.app_context():
            assert is_e2e_fixture_library() is True
            (lib / ".bpp-e2e-fixture").unlink()
            # Still True — served from cache, no re-stat.
            assert is_e2e_fixture_library() is True, (
                "fixture detection must be cached per root; the gate runs "
                "on every POST/PUT/DELETE and shouldn't hit the filesystem "
                "each time"
            )


# ── Phase 5 background-backfill health surface (M8 followup) ─────────


class TestPhase5HealthCheck:
    """The Phase 5 daemon (smart-album backfill) sets ctx.phase5_failed
    when it errors out. /api/v1/health exposes this so the operator
    sees 'smart album counts may be stale' without grepping server.log
    for the ERROR line, and so /api/v1/health flips to status=degraded.
    """

    def test_default_state_reports_no_failure(self, client, app):
        """A freshly-booted ctx that hasn't crashed Phase 5 reports
        ``failed=False``. (The in_flight flag may be True or False
        depending on whether the startup daemon has finished by the
        time the test client fires the request — we wait for it here
        to make the assertion deterministic.)"""
        ctx = app.extensions["bpp"]
        ctx.smart_album_backfill_done.wait(timeout=5)
        data = client.get("/api/v1/health").get_json()
        phase5 = data["checks"]["phase5"]
        assert phase5["failed"] is False
        assert phase5["in_flight"] is False

    def test_phase5_failed_flips_status_to_degraded(self, client, app):
        """When the daemon's except path sets ctx.phase5_failed = True,
        the aggregate status must surface 'degraded' so the caller
        knows to display the stale-counts hint."""
        from bpp.web.state import get_ctx

        with app.app_context():
            ctx = get_ctx()
            ctx.phase5_failed = True

        data = client.get("/api/v1/health").get_json()
        assert data["status"] == "degraded", (
            "A failed Phase 5 must degrade the aggregate health report so the operator notices."
        )
        assert data["checks"]["phase5"]["failed"] is True

    def test_phase5_in_flight_reports_truthy(self, client, app):
        """Backfill in flight (daemon running, Event cleared) shows
        up in the health response so a caller waiting on it has a
        non-grepping signal.

        Uses app.extensions['bpp'] directly (the same handle the
        request-time get_ctx() resolves to) so the Event mutation we
        make here is visible to the subsequent /api/v1/health request.
        We wait for the startup daemon to finish first so it can't
        race-set the Event between our clear() and the request.
        """
        ctx = app.extensions["bpp"]
        # Let the startup-spawned Phase 5 daemon finish so it doesn't
        # asynchronously set the Event between our clear() and the
        # request below.
        assert ctx.smart_album_backfill_done.wait(timeout=5)
        ctx.smart_album_backfill_done.clear()

        try:
            data = client.get("/api/v1/health").get_json()
            assert data["checks"]["phase5"]["in_flight"] is True
        finally:
            # Restore default so we don't pollute other tests.
            ctx.smart_album_backfill_done.set()

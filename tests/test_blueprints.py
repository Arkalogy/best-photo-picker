"""TDD tests for the Flask blueprint refactor of app.py."""

from __future__ import annotations

import json
import os

import pytest
from PIL import Image

from bpp.web.app import create_app


def _make_test_image(path, size=(100, 100), color="red"):
    """Create a minimal JPEG image for testing."""
    img = Image.new("RGB", size, color)
    img.save(str(path), "JPEG")
    return str(path)


def _make_analysis(n: int = 10, src_dir: str | None = None) -> list[dict]:
    """Create synthetic analysis data for testing.

    If src_dir is given, creates real image files on disk.
    """
    items = []
    for i in range(n):
        if src_dir:
            fp = os.path.join(src_dir, f"img_{i:03d}.jpg")
            _make_test_image(fp)
        else:
            fp = f"/tmp/test_photos/img_{i:03d}.jpg"
        items.append(
            {
                "filepath": fp,
                "date": f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}T12:00:00",
                "date_day": f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}",
                "date_month": f"2024-{(i % 12) + 1:02d}",
                "file_size": 1024 * (i + 1),
                "file_mtime": 1700000000.0 + i,
                "blur_raw": 100.0 + i * 50,
                "blur_score": i / max(n - 1, 1),
                "exposure_score": 0.5 + (i % 3) * 0.15,
                "face_score": 0.3 + (i % 4) * 0.1,
                "face_count": i % 3,
                "largest_face_ratio": 0.05,
                "face_center_dist": 0.3,
                "composition_score": 0.4 + (i % 5) * 0.1,
                "aggregate_score": 0.3 + i * 0.05,
            }
        )
    return items


@pytest.fixture
def bp_app(tmp_path):
    """Create a Flask app for blueprint structure testing."""
    workdir = str(tmp_path / "workdir")
    os.makedirs(workdir)
    analysis = _make_analysis(10)
    with open(os.path.join(workdir, "analysis.json"), "w") as f:
        json.dump(analysis, f)
    app = create_app(workdir=workdir)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def web_client(bp_app):
    return bp_app.test_client()


# --- Step 0: Structure tests (RED until Step 1) ---


class TestBlueprintStructure:
    """Tests that app.py is properly split into blueprints."""

    def test_app_has_blueprints(self, bp_app):
        """App must register the expected 6 blueprints."""
        bp_names = set(bp_app.blueprints.keys())
        expected = {"core", "photos", "albums", "media", "analysis", "faces"}
        assert expected.issubset(bp_names), f"Missing: {expected - bp_names}"

    def test_state_accessible_via_extensions(self, bp_app):
        """WebAppState must be stored on app.extensions['bpp']."""
        assert "bpp" in bp_app.extensions
        ctx = bp_app.extensions["bpp"]
        assert hasattr(ctx, "get_conn")
        assert hasattr(ctx, "load_analysis_if_needed")
        assert hasattr(ctx, "build_photo_dict")
        assert hasattr(ctx, "ensure_workdir")

    def test_get_ctx_helper(self, bp_app):
        """get_ctx() returns the WebAppState inside a request context."""
        from bpp.web.state import get_ctx

        with bp_app.test_request_context():
            ctx = get_ctx()
            assert ctx is bp_app.extensions["bpp"]

    def test_state_has_workers(self, bp_app):
        """WebAppState must expose all background workers."""
        ctx = bp_app.extensions["bpp"]
        assert hasattr(ctx, "worker")
        assert hasattr(ctx, "face_worker")
        assert hasattr(ctx, "import_worker")
        assert hasattr(ctx, "clip_worker")

    def test_state_has_lock(self, bp_app):
        """WebAppState must have a threading lock."""
        ctx = bp_app.extensions["bpp"]
        assert hasattr(ctx, "lock")


class TestBlueprintRoutes:
    """Verify each blueprint registers its expected routes."""

    def test_core_routes(self, web_client):
        assert web_client.get("/").status_code == 200
        assert web_client.get("/api/v1/status").status_code == 200
        assert web_client.get("/api/v1/presets").status_code == 200

    def test_photos_routes(self, web_client):
        assert web_client.get("/api/v1/photos").status_code == 200
        assert web_client.get("/api/v1/overrides").status_code == 200

    def test_albums_routes(self, web_client):
        assert web_client.get("/api/v1/albums").status_code == 200

    def test_media_routes(self, web_client):
        resp = web_client.get("/thumb/nonexistent")
        assert resp.status_code == 404

    def test_analysis_routes(self, web_client):
        resp = web_client.get("/api/v1/library/status")
        assert resp.status_code == 200

    def test_faces_routes(self, web_client):
        resp = web_client.get("/api/v1/dedup/feedback/stats")
        assert resp.status_code == 200


# --- Export API endpoint tests ---


@pytest.fixture
def export_app(tmp_path):
    """Create a Flask app with real image files for export testing."""
    workdir = str(tmp_path / "workdir")
    src_dir = str(tmp_path / "workdir" / "photos")
    os.makedirs(src_dir)
    analysis = _make_analysis(3, src_dir=src_dir)
    with open(os.path.join(workdir, "analysis.json"), "w") as f:
        json.dump(analysis, f)
    app = create_app(workdir=workdir)
    app.config["TESTING"] = True
    return app, analysis, tmp_path


@pytest.fixture
def export_client(export_app):
    app, analysis, tmp_path = export_app
    return app.test_client(), analysis, tmp_path


class TestExportAPI:
    """Tests for the /api/export endpoint."""

    def _outdir(self, tmp_path, name="export_out"):
        """Return an outdir path inside workdir (which is an allowed path)."""
        return str(tmp_path / "workdir" / name)

    def test_export_modes_lists_registry_minus_zip(self, export_client):
        """GET /api/v1/export/modes feeds the modal dropdown from
        ExportModeRegistry; zip is excluded (it's the separate checkbox)."""
        client, _, _ = export_client
        resp = client.get("/api/v1/export/modes")
        assert resp.status_code == 200
        names = {m["name"] for m in resp.get_json()["modes"]}
        assert {"copy", "hardlink", "symlink"} <= names, names
        assert "zip" not in names
        # each entry carries a human description for the dropdown label
        first = resp.get_json()["modes"][0]
        assert "description" in first and "builtin" in first

    def test_missing_outdir(self, export_client):
        client, analysis, _ = export_client
        resp = client.post(
            "/api/v1/export",
            json={"selected_paths": [analysis[0]["filepath"]]},
        )
        assert resp.status_code == 400
        assert "outdir" in resp.get_json()["error"].lower()

    def test_no_selected_paths(self, export_client):
        client, _, tmp_path = export_client
        resp = client.post("/api/v1/export", json={"outdir": self._outdir(tmp_path)})
        assert resp.status_code == 400
        assert "selected" in resp.get_json()["error"].lower()

    def test_path_traversal_rejected(self, export_client):
        client, analysis, _ = export_client
        resp = client.post(
            "/api/v1/export",
            json={
                "outdir": "/etc/evil_export",
                "selected_paths": [analysis[0]["filepath"]],
            },
        )
        assert resp.status_code == 400
        assert "outside" in resp.get_json()["error"].lower()

    def test_invalid_format(self, export_client):
        client, analysis, tmp_path = export_client
        resp = client.post(
            "/api/v1/export",
            json={
                "outdir": self._outdir(tmp_path),
                "selected_paths": [analysis[0]["filepath"]],
                "fmt": "gif",
            },
        )
        assert resp.status_code == 400
        assert "format" in resp.get_json()["error"].lower()

    def test_invalid_quality_too_high(self, export_client):
        client, analysis, tmp_path = export_client
        resp = client.post(
            "/api/v1/export",
            json={
                "outdir": self._outdir(tmp_path),
                "selected_paths": [analysis[0]["filepath"]],
                "quality": 200,
            },
        )
        assert resp.status_code == 400
        assert "quality" in resp.get_json()["error"].lower()

    def test_invalid_max_size_too_small(self, export_client):
        client, analysis, tmp_path = export_client
        resp = client.post(
            "/api/v1/export",
            json={
                "outdir": self._outdir(tmp_path),
                "selected_paths": [analysis[0]["filepath"]],
                "max_size": 50,
            },
        )
        assert resp.status_code == 400
        assert "max_size" in resp.get_json()["error"].lower()

    def test_successful_export(self, export_client):
        client, analysis, tmp_path = export_client
        outdir = self._outdir(tmp_path)
        paths = [a["filepath"] for a in analysis[:2]]
        resp = client.post(
            "/api/v1/export",
            json={"outdir": outdir, "selected_paths": paths},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "exported"
        assert data["count"] == 2
        assert os.path.isdir(os.path.join(outdir, "selected"))

    def test_existing_outdir_merges_preserving_unrelated_files(self, export_client):
        """UAT: pointing at an existing folder must merge (not wipe) — the
        previous 'force=true wipes the dir' contract destroyed user data
        when they picked ~/Downloads or any other shared folder."""
        client, analysis, tmp_path = export_client
        outdir = self._outdir(tmp_path, "out_exists")
        os.makedirs(outdir)
        sentinel = os.path.join(outdir, "user_note.txt")
        with open(sentinel, "w") as f:
            f.write("user data")
        resp = client.post(
            "/api/v1/export",
            json={
                "outdir": outdir,
                "selected_paths": [analysis[0]["filepath"]],
            },
        )
        assert resp.status_code == 200, resp.data
        assert resp.get_json()["count"] == 1
        assert os.path.isfile(sentinel), "unrelated file must survive export"
        with open(sentinel) as f:
            assert f.read() == "user data"

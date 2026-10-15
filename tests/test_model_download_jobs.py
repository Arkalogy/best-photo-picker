"""Model downloads run as background jobs with progress.

The picker's Download / Redownload used to hold one HTTP request open
for the whole fetch; the browser gives up after 30 s, so large models
"failed" in the UI while the server kept downloading, and a retry
started a second download. These tests pin the job behaviour.
"""

from __future__ import annotations

import io
import json
import threading
import time
from typing import ClassVar
from unittest.mock import patch

import pytest

from bpp.web import model_download_jobs as jobs


@pytest.fixture(autouse=True)
def _fresh_jobs():
    jobs._reset_for_tests()
    yield
    jobs._reset_for_tests()


def _wait(key, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        snap = jobs.snapshot(key)
        if snap and snap["status"] != "running":
            return snap
        time.sleep(0.01)
    raise AssertionError(f"job {key} still running: {jobs.snapshot(key)}")


class TestJobRegistry:
    def test_start_returns_immediately_and_finishes_in_background(self):
        release = threading.Event()

        def work():
            release.wait(5)
            return {"size_bytes": 3}

        _job_obj, started = jobs.start("k1", "Model", work)
        assert started is True
        assert jobs.snapshot("k1")["status"] == "running"
        release.set()
        assert _wait("k1")["status"] == "done"
        assert _wait("k1")["result"] == {"size_bytes": 3}

    def test_duplicate_start_joins_the_running_job(self):
        release = threading.Event()
        calls = []

        def work():
            calls.append(1)
            release.wait(5)
            return {}

        first, started1 = jobs.start("dup", "Model", work)
        second, started2 = jobs.start("dup", "Model", work)
        assert started1 is True and started2 is False
        assert first is second
        release.set()
        _wait("dup")
        assert calls == [1], "a second click must not start a second download"

    def test_finished_job_can_be_started_again(self):
        jobs.start("again", "Model", lambda: {})
        _wait("again")
        _, started = jobs.start("again", "Model", lambda: {})
        assert started is True

    def test_failure_is_recorded_with_the_user_facing_reason(self):
        from bpp.errors import BppError

        def work():
            raise BppError("x", user_message="Couldn't download Model: connection reset")

        jobs.start("bad", "Model", work)
        snap = _wait("bad")
        assert snap["status"] == "error"
        assert snap["error"] == "Couldn't download Model: connection reset"

    def test_unexpected_exception_reason_is_plain_and_pathless(self):
        def work():
            raise OSError(28, "No space left on device", "/Users/someone/.cache/m.tmp")

        jobs.start("os", "Model", work)
        snap = _wait("os")
        assert snap["status"] == "error"
        assert "free disk space" in snap["error"]
        assert "/Users" not in snap["error"]

    def test_progress_from_download_file_reaches_the_job(self, tmp_path):
        from bpp.utils import download

        body = b"x" * (3 * 1024 * 1024)

        class _Resp(io.BytesIO):
            headers: ClassVar[dict[str, str]] = {"Content-Length": str(len(body))}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        release = threading.Event()
        seen = []

        def work():
            with patch.object(download.urllib.request, "urlopen", return_value=_Resp(body)):
                download.download_file(
                    "https://x/m.onnx", str(tmp_path / "m"), registry_id="openai_clip_vit_b32_onnx"
                )
            seen.append(jobs.snapshot("prog"))
            release.wait(5)
            return {}

        jobs.start("prog", "Model", work)
        deadline = time.time() + 5
        while not seen and time.time() < deadline:
            time.sleep(0.01)
        snap = seen[0]
        assert snap["bytes_total"] == len(body)
        assert snap["bytes_done"] == len(body)
        release.set()
        _wait("prog")


class TestDownloadProgressCallback:
    def test_download_file_reports_progress_without_a_job(self, tmp_path):
        from bpp.utils import download

        body = b"y" * (2 * 1024 * 1024 + 7)

        class _Resp(io.BytesIO):
            headers: ClassVar[dict[str, str]] = {"Content-Length": str(len(body))}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        calls = []
        with (
            patch.object(download.urllib.request, "urlopen", return_value=_Resp(body)),
            download.report_progress(lambda done, total: calls.append((done, total))),
        ):
            download.download_file(
                "https://x/y", str(tmp_path / "y"), registry_id="openai_clip_vit_b32_onnx"
            )
        assert calls, "progress callback never called"
        assert calls[-1] == (len(body), len(body))
        assert [c[0] for c in calls] == sorted(c[0] for c in calls)
        assert (tmp_path / "y").read_bytes() == body


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BPP_ACCEPTANCE_LOG_PATH", str(tmp_path / "acceptance.jsonl"))
    from bpp.web.app import create_app

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    (workdir / "analysis.json").write_text("[]", encoding="utf-8")
    app = create_app(workdir=str(workdir))
    app.config["TESTING"] = True
    return app.test_client()


def _job(client, key):
    resp = client.get(f"/api/v1/models/download-jobs/{key}")
    return resp.status_code, resp.get_json()


class TestRedownloadEndpoint:
    def test_returns_202_with_a_job_while_the_download_continues(self, client, tmp_path):
        from bpp.web import bp_models

        release = threading.Event()
        target = tmp_path / "models" / "yunet.onnx"

        def slow_download(url, dest, *, registry_id, sha256=None, **kw):
            release.wait(5)
            with open(dest, "wb") as f:
                f.write(b"model")

        with (
            patch.object(
                bp_models, "_model_path_url_sha", return_value=(str(target), "https://x/y", None)
            ),
            patch.object(bp_models, "_resolve_legal_id", return_value="openai_clip_vit_b32_onnx"),
            patch.object(bp_models, "_reset_model_cache"),
            patch("bpp.utils.download.download_file", side_effect=slow_download),
        ):
            t0 = time.time()
            resp = client.post(
                "/api/v1/models/redownload",
                data=json.dumps({"name": "YuNet face detection"}),
                headers={"Content-Type": "application/json"},
            )
            assert time.time() - t0 < 2
            assert resp.status_code == 202, resp.get_json()
            key = resp.get_json()["job"]
            status, snap = _job(client, key)
            assert status == 200 and snap["status"] == "running"
            release.set()
            snap = _wait(key)
        assert snap["status"] == "done", snap
        assert target.read_bytes() == b"model"

    def test_unknown_job_is_404(self, client):
        status, _ = _job(client, "model:nope")
        assert status == 404


class TestEnsureWeightsEndpoint:
    def test_returns_202_and_finishes_in_background(self, client, tmp_path):
        weight = tmp_path / "w.onnx"
        weight.write_bytes(b"x" * 1024)
        with (
            patch("bpp.web.bp_models._enforce_download_license_gate"),
            patch("bpp.web.bp_models._enforce_download_status_gate"),
            patch(
                "bpp.scoring.face_embed_buffalo_s.ensure_buffalo_s_model",
                return_value=str(weight),
            ),
        ):
            resp = client.post(
                "/api/v1/face-embedders/ensure-weights",
                data=json.dumps({"registry_id": "insightface_buffalo_s"}),
                headers={"Content-Type": "application/json"},
            )
            assert resp.status_code == 202, resp.get_json()
            snap = _wait(resp.get_json()["job"])
        assert snap["status"] == "done"
        assert snap["result"] == {"ok": True, "size_bytes": 1024}

    def test_unaccepted_restricted_model_is_refused_immediately(self, client):
        resp = client.post(
            "/api/v1/face-embedders/ensure-weights",
            data=json.dumps({"registry_id": "insightface_buffalo_s"}),
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 409, resp.get_json()

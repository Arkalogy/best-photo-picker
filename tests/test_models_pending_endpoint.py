"""Tests for `GET /api/models/pending` — the data feed for the
per-model consent prompt that fires before analyze.

The endpoint must:
  * return only models whose path is missing on disk (don't ask the
    user to consent to bytes they already have)
  * include name + size_mb + host + url for each entry (so the user
    can give informed consent)
  * be auth-gated like the rest of `/api/*`
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest


@pytest.fixture
def app(tmp_path):
    """Fresh Flask app with TESTING=False so the auth middleware actually runs."""
    from bpp.web.app import create_app

    workdir = str(tmp_path / "workdir")
    os.makedirs(workdir)
    app = create_app(workdir=workdir)
    app.config["TESTING"] = False
    return app


def _get(app, path, *, remote_addr="127.0.0.1", token=None):
    headers = {}
    if token:
        headers["X-Auth-Token"] = token
    return app.test_client().get(
        path, headers=headers, environ_overrides={"REMOTE_ADDR": remote_addr}
    )


class TestModelsPendingEndpoint:
    def test_returns_pending_only(self, app, tmp_path):
        """The list must filter to models whose path is missing on disk."""
        from bpp.scoring.model_manifest import ModelEntry

        present = tmp_path / "present.onnx"
        present.write_bytes(b"x")
        missing = tmp_path / "missing.onnx"

        fake = [
            ModelEntry(
                name="Already cached",
                path=present,
                url="https://example.com/p.onnx",
                sha256="0" * 64,
                size_mb=1.0,
                host="example.com",
                legal_entry_id="openai_clip_vit_b32_onnx",
            ),
            ModelEntry(
                name="Needs download",
                path=missing,
                url="https://example.com/m.onnx",
                sha256="0" * 64,
                size_mb=42,
                host="example.com",
                legal_entry_id="openai_clip_vit_b32_onnx",
            ),
        ]
        with patch("bpp.scoring.model_manifest.all_models", return_value=fake):
            ctx = app.extensions["bpp"]
            r = _get(app, "/api/v1/models/pending", token=ctx.auth_token)
        assert r.status_code == 200
        body = r.get_json()
        assert [m["name"] for m in body["models"]] == ["Needs download"]
        assert body["models"][0]["size_mb"] == 42
        assert body["models"][0]["host"] == "example.com"
        assert body["models"][0]["url"] == "https://example.com/m.onnx"
        assert body["total_mb"] == 42

    def test_returns_empty_when_all_models_cached(self, app, tmp_path):
        """User has analyzed before → next click goes straight through,
        no consent prompt, no surprise."""
        from bpp.scoring.model_manifest import ModelEntry

        present = tmp_path / "present.onnx"
        present.write_bytes(b"x")

        fake = [
            ModelEntry(
                name="Cached",
                path=present,
                url="https://example.com/p.onnx",
                sha256="0" * 64,
                size_mb=1.0,
                host="example.com",
                legal_entry_id="openai_clip_vit_b32_onnx",
            ),
        ]
        with patch("bpp.scoring.model_manifest.all_models", return_value=fake):
            ctx = app.extensions["bpp"]
            r = _get(app, "/api/v1/models/pending", token=ctx.auth_token)
        assert r.status_code == 200
        body = r.get_json()
        assert body["models"] == []
        assert body["total_mb"] == 0

    def test_bundled_fallback_counts_as_present(self, app, tmp_path):
        """If a model has a bundled fallback that exists, no download is
        needed — even if the cache path is empty."""
        from bpp.scoring.model_manifest import ModelEntry

        cache_path = tmp_path / "missing.tflite"
        bundled_path = tmp_path / "bundled.tflite"
        bundled_path.write_bytes(b"bundled")

        fake = [
            ModelEntry(
                name="BlazeFace bundled",
                path=cache_path,
                url="https://example.com/m.tflite",
                sha256="0" * 64,
                size_mb=0.2,
                host="example.com",
                legal_entry_id="openai_clip_vit_b32_onnx",
                bundled_path=str(bundled_path),
            ),
        ]
        with patch("bpp.scoring.model_manifest.all_models", return_value=fake):
            ctx = app.extensions["bpp"]
            r = _get(app, "/api/v1/models/pending", token=ctx.auth_token)
        assert r.status_code == 200
        assert r.get_json()["models"] == []

    def test_requires_auth(self, app):
        """Endpoint is under /api/, so the auth gate applies."""
        r = _get(app, "/api/v1/models/pending")
        # No token → 403 (loopback regression case)
        assert r.status_code == 403

    def test_face_detectors_surface_when_no_permissive_detector(self, app):
        """No face detector is usable without a click-through, so the
        face_detector kind is not "covered". YuNet (attribution) is
        offered in the combined acceptance step; research-only SCRFD is
        surfaced in the Settings → Models hint instead of being dropped."""
        ctx = app.extensions["bpp"]
        with (
            patch("bpp.registry.acceptance_log.has_accepted", return_value=False),
            patch("bpp.registry.acceptance.is_acceptance_valid_for", return_value=False),
        ):
            r = _get(app, "/api/v1/models/pending", token=ctx.auth_token)
        assert r.status_code == 200
        from bpp.web.model_filter import _covered_kinds

        assert "face_detector" not in _covered_kinds()
        body = r.get_json()
        blocked_ids = {b["legal_entry_id"] for b in body["blocked"]}
        attribution_ids = {a["legal_entry_id"] for a in body["attribution"]}
        assert "insightface_scrfd_25g" in blocked_ids, blocked_ids
        assert "opencv_yunet" not in blocked_ids, blocked_ids
        assert "opencv_yunet" in attribution_ids or not any(
            m["name"] == "YuNet face detection" for m in body["models"]
        ), body

    def test_restricted_uncovered_capability_still_blocked(self, app):
        """Control for the suppression above: a restricted entry whose
        kind has no permissive sibling (NudeNet — nudity_classifier) is
        still surfaced, so the 'Settings → Models' hint stays complete
        for genuinely-gated capabilities."""
        ctx = app.extensions["bpp"]
        r = _get(app, "/api/v1/models/pending", token=ctx.auth_token)
        assert r.status_code == 200
        blocked_ids = {b["legal_entry_id"] for b in r.get_json()["blocked"]}
        assert "nudenet_320n" in blocked_ids, (
            "restricted entry with no permissive sibling must still be "
            f"surfaced as blocked; blocked={blocked_ids}"
        )

    def test_restricted_model_never_offered_as_free_download(self, app, tmp_path):
        """THE legal-protection regression: a click-through model that is
        pending and unaccepted must never be in the free `models` list.
        Pre-fix, SFace's manifest entry had no legal_entry_id, so it
        leaked into the downloadable list. An attribution model like SFace
        is listed under `attribution`: it downloads only once the user
        accepts it in the pre-analysis dialog."""
        from bpp.scoring.model_manifest import ModelEntry

        fake = [
            ModelEntry(
                name="SFace face recognition",
                path=tmp_path / "sface.onnx",  # missing → pending
                url="https://example.com/sface.onnx",
                sha256="0" * 64,
                size_mb=38,
                host="example.com",
                legal_entry_id="sface_yunet",
            ),
        ]
        with (
            patch("bpp.scoring.model_manifest.all_models", return_value=fake),
            patch("bpp.registry.acceptance_log.has_accepted", return_value=False),
            patch("bpp.registry.acceptance.is_acceptance_valid_for", return_value=False),
        ):
            ctx = app.extensions["bpp"]
            r = _get(app, "/api/v1/models/pending", token=ctx.auth_token)
        assert r.status_code == 200
        body = r.get_json()
        assert all(m["name"] != "SFace face recognition" for m in body["models"]), (
            f"SFace must not be a free download; models={body['models']}"
        )
        assert all(b["legal_entry_id"] != "sface_yunet" for b in body["blocked"]), (
            f"SFace is accepted in the pre-analysis step; blocked={body['blocked']}"
        )
        (item,) = body["attribution"]
        assert item["legal_entry_id"] == "sface_yunet"
        assert item["needs_acceptance"] is True
        assert body["attribution_mb"] == 38
        assert body["total_mb"] == 0

    def test_restricted_covered_alternate_dropped_from_both_lists(self, app, tmp_path):
        """A pending, unaccepted, restricted alternate for a covered
        capability is dropped from BOTH lists — not offered as a free
        download, not nagged for a license. Exercises the first-pass
        suppression path; the face_detector kind is marked covered here
        to simulate a permissive default detector."""
        from bpp.scoring.model_manifest import ModelEntry

        fake = [
            ModelEntry(
                name="SCRFD face detection",
                path=tmp_path / "scrfd.onnx",  # missing → pending
                url="https://example.com/scrfd.onnx",
                sha256="0" * 64,
                size_mb=0.3,
                host="example.com",
                legal_entry_id="insightface_scrfd_25g",
            ),
        ]
        with (
            patch("bpp.scoring.model_manifest.all_models", return_value=fake),
            patch("bpp.registry.acceptance_log.has_accepted", return_value=False),
            patch("bpp.registry.acceptance.is_acceptance_valid_for", return_value=False),
            patch("bpp.web.model_filter._covered_kinds", return_value={"face_detector"}),
        ):
            ctx = app.extensions["bpp"]
            r = _get(app, "/api/v1/models/pending", token=ctx.auth_token)
        assert r.status_code == 200
        body = r.get_json()
        assert all(m["name"] != "SCRFD face detection" for m in body["models"]), (
            f"covered restricted alternate must not be a free download; models={body['models']}"
        )
        assert all(b["legal_entry_id"] != "insightface_scrfd_25g" for b in body["blocked"]), (
            f"covered restricted alternate must not be nagged; blocked={body['blocked']}"
        )


def test_restricted_manifest_entries_are_linked():
    """Structural guard: the known restricted models in the download
    manifest MUST carry their legal_entry_id, and every link must resolve
    to a real registry entry. An unlinked restricted entry would be
    offered as a free download by the pre-flight dialog."""
    from bpp.registry import get_entry
    from bpp.scoring.model_manifest import all_models

    by_name = {m.name: m for m in all_models()}
    for m in all_models():
        if m.legal_entry_id is not None:
            assert get_entry(m.legal_entry_id) is not None, (
                f"{m.name} links to unknown legal id {m.legal_entry_id!r}"
            )
    assert by_name["SFace face recognition"].legal_entry_id == "sface_yunet"
    assert by_name["YuNet face detection"].legal_entry_id == "opencv_yunet"
    assert by_name["SCRFD face detection"].legal_entry_id == "insightface_scrfd_25g"


def test_every_manifest_entry_is_linked_to_its_registry_entry():
    """Every download links to a registry entry — the only place its
    license is written — and agrees with what its scoring module
    declares for the same file."""
    import pathlib

    from bpp.registry import get_entry
    from bpp.scoring.model_base import ModelRegistry
    from bpp.scoring.model_manifest import all_models
    from bpp.web.bp_models import declared_legal_id

    by_path = {pathlib.Path(e.path): declared_legal_id(e) for e in ModelRegistry.all()}
    for m in all_models():
        assert m.legal_entry_id and get_entry(m.legal_entry_id), m.name
        if m.path in by_path:
            assert by_path[m.path] == m.legal_entry_id, m.name


class TestModelsUninstallEndpoint:
    """`POST /api/models/uninstall` lets the user free disk space for
    a model they don't use (CLIP alone is ~580 MB). Files come back on
    next analyze if needed."""

    def _post(self, app, body, *, token=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Auth-Token"] = token
        return app.test_client().post(
            "/api/v1/models/uninstall",
            json=body,
            headers=headers,
            environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
        )

    def test_deletes_model_file_and_reports_bytes(self, app, tmp_path):
        """Happy path: file exists → delete + return bytes_freed."""
        from bpp.web import bp_models

        target = tmp_path / "yolo11n.onnx"
        target.write_bytes(b"x" * 12345)

        with patch.object(
            bp_models,
            "_model_path_url_sha",
            return_value=(str(target), "https://x", "0" * 64),
        ):
            ctx = app.extensions["bpp"]
            r = self._post(app, {"name": "YOLO pet detector"}, token=ctx.auth_token)
        assert r.status_code == 200
        body = r.get_json()
        assert body["bytes_freed"] == 12345
        assert not target.exists(), "model file must be removed from disk"

    def test_idempotent_when_already_missing(self, app, tmp_path):
        """If the file isn't on disk (already deleted), succeed with
        bytes_freed=0 — clicking Uninstall twice shouldn't error."""
        from bpp.web import bp_models

        target = tmp_path / "missing.onnx"

        with patch.object(
            bp_models,
            "_model_path_url_sha",
            return_value=(str(target), "https://x", "0" * 64),
        ):
            ctx = app.extensions["bpp"]
            r = self._post(app, {"name": "YOLO pet detector"}, token=ctx.auth_token)
        assert r.status_code == 200
        assert r.get_json()["bytes_freed"] == 0

    def test_also_removes_stray_tmp_file(self, app, tmp_path):
        """An interrupted download leaves a `.tmp` file. Uninstall
        should clean that up too."""
        from bpp.web import bp_models

        target = tmp_path / "model.onnx"
        tmp_file = target.with_suffix(target.suffix + ".tmp")
        target.write_bytes(b"data")
        tmp_file.write_bytes(b"partial")

        with patch.object(
            bp_models,
            "_model_path_url_sha",
            return_value=(str(target), "https://x", "0" * 64),
        ):
            ctx = app.extensions["bpp"]
            r = self._post(app, {"name": "YOLO pet detector"}, token=ctx.auth_token)
        assert r.status_code == 200
        assert not target.exists()
        assert not tmp_file.exists()

    def test_unknown_name_400(self, app):
        ctx = app.extensions["bpp"]
        r = self._post(app, {"name": "Nonexistent model"}, token=ctx.auth_token)
        assert r.status_code == 400

    def test_missing_name_400(self, app):
        ctx = app.extensions["bpp"]
        r = self._post(app, {}, token=ctx.auth_token)
        assert r.status_code == 400

    def test_requires_auth(self, app):
        r = self._post(app, {"name": "YOLO pet detector"})
        assert r.status_code == 403


class TestModelsListEndpoint:
    """Settings → Advanced → ML Models reads `/api/v1/models` to render
    the per-feature status list. The endpoint imports model-path
    constants from across the bpp.scoring tree; if a constant moves
    or a module gets split, the import quietly breaks and the panel
    shows 'Could not load model info.' This test fails fast on that
    drift instead of waiting for a user to open Settings."""

    def test_returns_features_list(self, app):
        ctx = app.extensions["bpp"]
        r = _get(app, "/api/v1/models", token=ctx.auth_token)
        assert r.status_code == 200, (
            f"/api/v1/models returned {r.status_code}; the Settings → ML "
            f"Models panel will render 'Could not load model info.'"
        )
        body = r.get_json()
        assert isinstance(body, list)
        assert len(body) > 0, "expected at least one feature entry"
        # Every entry must carry the keys the frontend renders.
        for f in body:
            assert "label" in f, f"feature missing label: {f}"
            assert "status" in f, f"feature missing status: {f}"

    def test_requires_auth(self, app):
        r = _get(app, "/api/v1/models")
        assert r.status_code == 403


def test_compute_pending_and_blocked_runs_without_http(tmp_path):
    """The eligibility logic was extracted into a pure function — it must
    be exercisable directly, no Flask request needed (the point of the
    split). Mirrors the endpoint's restricted-SFace assertion."""
    from bpp.scoring.model_manifest import ModelEntry
    from bpp.web.model_filter import compute_pending_and_blocked

    fake = [
        ModelEntry(
            name="SFace face recognition",
            path=tmp_path / "s.onnx",
            url="https://example.com/s.onnx",
            sha256="0" * 64,
            size_mb=38,
            host="example.com",
            legal_entry_id="sface_yunet",
        ),
    ]
    with (
        patch("bpp.scoring.model_manifest.all_models", return_value=fake),
        patch("bpp.registry.acceptance_log.has_accepted", return_value=False),
        patch("bpp.registry.acceptance.is_acceptance_valid_for", return_value=False),
    ):
        items, blocked = compute_pending_and_blocked()
    (item,) = items
    assert item["legal_entry_id"] == "sface_yunet"
    assert item["needs_acceptance"] is True
    assert all(b["legal_entry_id"] != "sface_yunet" for b in blocked)

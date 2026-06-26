"""Tests for the catalog ensure/uninstall-weights endpoints.

Catalog entries (currently only ``insightface_buffalo_s``) are
runtime-fetched models with no install wiring in the legacy
``ModelRegistry``. The Settings → Models picker UI fix introduced
two endpoints so the menu can offer an explicit Download → Use →
Uninstall lifecycle instead of silently fetching weights on first
analyze:

* ``POST /api/v1/face-embedders/ensure-weights`` — synchronously
  triggers the per-entry ensure function and returns the resulting
  size on disk.
* ``POST /api/v1/face-embedders/uninstall-weights`` — deletes the
  per-entry cache. Idempotent.

Both endpoints reject unknown registry ids and require a body with
``registry_id``. The ensure path surfaces underlying load failures
(policy refusal, integrity mismatch, network) through ``BppError``
with the diagnostic message intact.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    """Boot a Flask test client with auth bypassed."""
    monkeypatch.setenv(
        "BPP_ACCEPTANCE_LOG_PATH",
        str(tmp_path / "acceptance.jsonl"),
    )
    from bpp.web.app import create_app

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    (workdir / "analysis.json").write_text("[]", encoding="utf-8")
    app = create_app(workdir=str(workdir))
    app.config["TESTING"] = True
    return app.test_client()


def _post_ensure(client, body):
    resp = client.post(
        "/api/v1/face-embedders/ensure-weights",
        data=json.dumps(body),
        headers={"Content-Type": "application/json"},
    )
    payload = None
    with contextlib.suppress(Exception):
        payload = resp.get_json()
    return resp.status_code, payload


def _post_uninstall(client, body):
    resp = client.post(
        "/api/v1/face-embedders/uninstall-weights",
        data=json.dumps(body),
        headers={"Content-Type": "application/json"},
    )
    payload = None
    with contextlib.suppress(Exception):
        payload = resp.get_json()
    return resp.status_code, payload


class TestEnsureWeightsValidation:
    def test_missing_registry_id_returns_400(self, client):
        status, payload = _post_ensure(client, {})
        assert status == 400, f"got {status}: {payload}"
        body = (payload or {}).get("error", "") + (payload or {}).get("message", "")
        assert "registry_id" in body.lower()

    def test_unknown_registry_id_returns_400(self, client):
        status, payload = _post_ensure(client, {"registry_id": "nonexistent_catalog_entry"})
        assert status == 400, f"got {status}: {payload}"
        # Surface the specific error so the toast tells the user the
        # endpoint refused the id — not a confused 500.
        msg = (payload or {}).get("error", "") + (payload or {}).get("message", "").lower()
        assert "catalog loader" in msg

    def test_installable_entry_rejected(self, client):
        # opencv_yunet is installable via the legacy ModelRegistry path,
        # not a catalog entry. Calling ensure-weights for it would
        # bypass that wiring; the endpoint refuses to handle anything
        # outside its loader registry.
        status, payload = _post_ensure(client, {"registry_id": "opencv_yunet"})
        assert status == 400, f"got {status}: {payload}"


class TestEnsureWeightsSuccessPath:
    def test_calls_buffalo_s_ensure_fn(self, client, tmp_path):
        # Stub the ensure function to avoid the real ~121 MB download
        # and verify the endpoint actually invokes it and reports
        # the resulting file size.
        fake_weight = tmp_path / "buffalo_s.onnx"
        fake_weight.write_bytes(b"x" * 1024)

        with patch(
            "bpp.scoring.face_embed_buffalo_s.ensure_buffalo_s_model",
            return_value=str(fake_weight),
        ) as mock_ensure:
            status, payload = _post_ensure(client, {"registry_id": "insightface_buffalo_s"})

        assert status == 200, f"got {status}: {payload}"
        assert payload == {"ok": True, "size_bytes": 1024}
        mock_ensure.assert_called_once()

    def test_ensure_failure_surfaces_diagnostic(self, client):
        # When the underlying ensure raises (policy block, integrity,
        # network), the endpoint must return the reason so the picker
        # toast can be specific — a bare "Failed" leaves the user
        # guessing.
        with patch(
            "bpp.scoring.face_embed_buffalo_s.ensure_buffalo_s_model",
            side_effect=RuntimeError("policy gate: missing acceptance"),
        ):
            status, payload = _post_ensure(client, {"registry_id": "insightface_buffalo_s"})

        assert status >= 400
        msg = (payload or {}).get("error", "") + (payload or {}).get("message", "")
        assert "missing acceptance" in msg


class TestUninstallWeights:
    def test_missing_registry_id_returns_400(self, client):
        status, payload = _post_uninstall(client, {})
        assert status == 400, f"got {status}: {payload}"

    def test_unknown_registry_id_returns_400(self, client):
        status, payload = _post_uninstall(client, {"registry_id": "nonexistent_catalog_entry"})
        assert status == 400, f"got {status}: {payload}"

    def test_calls_buffalo_s_remove_fn_and_returns_bytes_freed(self, client):
        with patch(
            "bpp.scoring.face_embed_buffalo_s.remove_local_weights",
            return_value=127_596_032,
        ) as mock_remove:
            status, payload = _post_uninstall(client, {"registry_id": "insightface_buffalo_s"})

        assert status == 200, f"got {status}: {payload}"
        assert payload == {"ok": True, "bytes_freed": 127_596_032}
        mock_remove.assert_called_once()

    def test_idempotent_when_nothing_on_disk(self, client):
        # remove_local_weights returns 0 when there's nothing to delete;
        # the endpoint must not treat that as an error.
        with patch(
            "bpp.scoring.face_embed_buffalo_s.remove_local_weights",
            return_value=0,
        ):
            status, payload = _post_uninstall(client, {"registry_id": "insightface_buffalo_s"})

        assert status == 200, f"got {status}: {payload}"
        assert payload == {"ok": True, "bytes_freed": 0}


class TestEntriesResponseIncludesCatalogOnDisk:
    """The picker decides Download-vs-Use ordering by reading
    ``catalog_on_disk`` on each entry. Surface it from the entries
    endpoint so the frontend doesn't have to call ensure-weights
    just to find out whether the file is already cached."""

    def test_entry_dict_carries_catalog_on_disk_flag(self, client):
        from bpp.registry.model_registry import get_entry
        from bpp.web.bp_model_registry import _entry_to_picker_dict

        buffalo = get_entry("insightface_buffalo_s")
        assert buffalo is not None, "test prereq: entry exists"

        with patch(
            "bpp.scoring.face_embed_buffalo_s.is_on_disk",
            return_value=False,
        ):
            d = _entry_to_picker_dict(buffalo)
        assert d["catalog_on_disk"] is False

        with patch(
            "bpp.scoring.face_embed_buffalo_s.is_on_disk",
            return_value=True,
        ):
            d = _entry_to_picker_dict(buffalo)
        assert d["catalog_on_disk"] is True

    def test_non_catalog_entry_reports_catalog_on_disk_false(self, client):
        from bpp.registry.model_registry import get_entry
        from bpp.web.bp_model_registry import _entry_to_picker_dict

        # opencv_yunet has install wiring (legacy ModelRegistry); it's
        # not in the catalog-loader map, so catalog_on_disk should be
        # False regardless of whether the file exists.
        yunet = get_entry("opencv_yunet")
        assert yunet is not None
        d = _entry_to_picker_dict(yunet)
        assert d["catalog_on_disk"] is False


class TestOnDemandModelsAreCatalogEntries:
    """LaMa and NudeNet are runtime-fetched models that ALSO carry a
    (fileless) legacy feature row. They MUST be registered as catalog
    entries — otherwise the picker can't offer Download/Uninstall and
    the row dead-ends at "Review the license ✓" + a disabled Uninstall.
    Regression for that dead-end."""

    def test_lama_and_nudenet_have_catalog_loaders(self, client):
        from bpp.web.bp_model_registry import _catalog_loaders

        loaders = _catalog_loaders()
        for cid in ("lama_inpaint_research", "nudenet_320n"):
            assert cid in loaders, f"{cid} missing catalog loader"
            is_on_disk, ensure_fn, remove_fn = loaders[cid]
            assert callable(is_on_disk)
            assert callable(ensure_fn)
            assert callable(remove_fn)

    def test_lama_picker_dict_flags_is_catalog_entry(self, client):
        from bpp.registry.model_registry import get_entry
        from bpp.web.bp_model_registry import _entry_to_picker_dict

        lama = get_entry("lama_inpaint_research")
        assert lama is not None
        d = _entry_to_picker_dict(lama)
        assert d["is_catalog_entry"] is True, (
            "LaMa must be flagged is_catalog_entry so the picker routes "
            "Download/Uninstall to the catalog endpoints"
        )

    def test_non_catalog_entry_flags_is_catalog_entry_false(self, client):
        from bpp.registry.model_registry import get_entry
        from bpp.web.bp_model_registry import _entry_to_picker_dict

        scrfd = get_entry("insightface_scrfd_25g")
        assert scrfd is not None
        assert _entry_to_picker_dict(scrfd)["is_catalog_entry"] is False

    def test_ensure_weights_accepts_lama(self, client):
        # The endpoint must recognise LaMa (not reject as "no catalog
        # loader"). Patch the ensure fn so no real download happens.
        with patch(
            "bpp.ai.inpainting.ensure_lama_model",
            return_value=__file__,  # any existing path so getsize() works
        ):
            status, payload = _post_ensure(client, {"registry_id": "lama_inpaint_research"})
        assert status == 200, payload
        assert payload["ok"] is True

"""Redownload endpoint ↔ legal registry: the license gate can't be skipped.

Regression: the Content-filter row's file ("NudeNet 320n") wasn't in
the old hand-kept file → legal-id map, so Settings → Models → Download /
Redownload ran ``download_file(..., registry_id=None)`` — no status and
no acceptance gate for an AGPL model that must only download after
acceptance.

Pinned here:

* Structural: with every scoring module imported, every ModelRegistry
  file declares a legal-registry entry, and that entry exists.
* Fail-closed: a file that declares no entry is refused.
* NudeNet redownload without (current) acceptance is refused before the
  network call and before the existing file is touched.
"""

from __future__ import annotations

import dataclasses
import importlib
from unittest.mock import patch

import pytest

from bpp.errors import BppError
from bpp.registry import (
    UseContext,
    confirm_acceptance,
    get_entry,
    prepare_acceptance,
    set_use_context,
    utc_now_iso,
)
from bpp.registry.acceptance_log import append_row, find_latest_for_model
from bpp.scoring.model_base import ModelEntry, ModelRegistry
from bpp.web import bp_models

_ALL_SCORING_MODULES = (
    "bpp.scoring.face",
    "bpp.scoring.face_blazeface_fr",
    "bpp.scoring.face_embed",
    "bpp.scoring.face_embed_sface_runtime",
    "bpp.scoring.face_expression",
    "bpp.scoring.face_hand_filter",
    "bpp.scoring.face_scrfd",
    "bpp.scoring.pose",
    "bpp.scoring.segmentation",
    "bpp.scoring.pets",
    "bpp.scoring.nudity",
    "bpp.scoring.clip_embed",
    "bpp.scoring.clip_tokenizer",
)


def _import_everything() -> None:
    for mod in _ALL_SCORING_MODULES:
        importlib.import_module(mod)
    bp_models._ensure_model_modules_imported()


def _accept(model_id: str) -> None:
    set_use_context(UseContext.PERSONAL, set_via="test")
    entry = get_entry(model_id)
    assert entry is not None
    draft = prepare_acceptance(entry, use_context=UseContext.PERSONAL)
    confirm_acceptance(
        draft,
        checkbox_responses={cb: True for cb, _ in draft.required_checkboxes},
        accepted_at=utc_now_iso(),
        separate_rights_asserted=False,
        source_of_rights_note="",
    )


def _make_stale(model_id: str) -> None:
    """Append an acceptance of an OLDER acknowledgment text (simulates
    a user who accepted before the disclaimer-version bump)."""
    latest = find_latest_for_model(model_id)
    assert latest is not None
    append_row(dataclasses.replace(latest, ack_text_sha256="0" * 64, ack_text_version="old"))


class TestStructuralLink:
    def test_every_model_file_declares_a_registered_legal_entry(self) -> None:
        """The scoring module's declaration is the only link from a file
        to its license, so every file must have one, and it must exist."""
        _import_everything()
        problems = {}
        for entry in ModelRegistry.all():
            declared = bp_models.declared_legal_id(entry)
            if declared is None or get_entry(declared) is None:
                problems[entry.name] = declared
        assert not problems, f"files with no / an unknown legal entry: {problems}"

    def test_nudenet_resolves(self) -> None:
        _import_everything()
        assert bp_models._resolve_legal_id("NudeNet 320n") == "nudenet_320n"

    def test_mediapipe_files_resolve_to_their_entries(self) -> None:
        _import_everything()
        assert bp_models._resolve_legal_id("Selfie segmenter") == "mediapipe_selfie_segmenter"
        assert bp_models._resolve_legal_id("BlazeFace full-range") == (
            "mediapipe_blazeface_full_range"
        )

    def test_singleton_registry_id_agrees_with_declared_legal_id(self) -> None:
        """An entry that sets ``legal_id`` AND is ModelSingleton-backed
        must agree with the singleton's ``registry_id`` (the id its own
        download path passes to download_file)."""
        _import_everything()
        for entry in ModelRegistry.all():
            owner = getattr(entry.reset, "__self__", None)
            rid = getattr(owner, "registry_id", None)
            if entry.legal_id and isinstance(rid, str):
                assert rid == entry.legal_id, entry.name


class TestFailClosed:
    def test_file_with_no_legal_entry_is_refused(self) -> None:
        ModelRegistry.register(
            ModelEntry(
                name="FAKE licensed model",
                path="/tmp/bpp-fake-licensed.onnx",
                url="https://example.invalid/x.onnx",
                sha256="0" * 64,
                reset=lambda: None,
            ),
            replace=True,
        )
        try:
            with patch("bpp.utils.download.download_file") as dl, pytest.raises(BppError):
                bp_models._redownload_model("FAKE licensed model")
            dl.assert_not_called()
        finally:
            ModelRegistry._entries.pop("FAKE licensed model", None)


@pytest.mark.no_preaccept_permissive
class TestNudeNetRedownloadGate:
    def _run(self, tmp_path):
        """Point the NudeNet entry at a tmp file holding sentinel bytes,
        then attempt a redownload with the network mocked."""
        _import_everything()
        target = tmp_path / "320n.onnx"
        target.write_bytes(b"existing")
        real = ModelRegistry.get("NudeNet 320n")
        assert real is not None
        ModelRegistry.register(
            dataclasses.replace(real, path=str(target)), replace=True, builtin=True
        )
        try:
            with patch("bpp.utils.download.download_file") as dl:
                try:
                    bp_models._redownload_model("NudeNet 320n")
                    err = None
                except BppError as e:
                    err = e
            return dl, err, target
        finally:
            ModelRegistry.register(real, replace=True, builtin=True)

    def test_refused_without_acceptance(self, tmp_path) -> None:
        dl, err, target = self._run(tmp_path)
        assert err is not None
        assert err.http_status == 409
        assert "Settings → Models" in err.user_message
        dl.assert_not_called()
        assert target.read_bytes() == b"existing", "existing copy deleted before refusal"

    def test_refused_with_stale_acceptance(self, tmp_path) -> None:
        _accept("nudenet_320n")
        _make_stale("nudenet_320n")
        dl, err, _ = self._run(tmp_path)
        assert err is not None
        assert "re-accept" in err.user_message
        dl.assert_not_called()

    def test_allowed_with_current_acceptance_and_gated_by_registry_id(self, tmp_path) -> None:
        _accept("nudenet_320n")
        with (
            patch("bpp.web.bp_models.os.replace"),
            patch("bpp.web.bp_models._reset_model_cache"),
        ):
            dl, err, _ = self._run(tmp_path)
        assert err is None
        dl.assert_called_once()
        assert dl.call_args.kwargs["registry_id"] == "nudenet_320n"


class TestEndpointRefusal:
    @pytest.mark.no_preaccept_permissive
    def test_http_redownload_nudenet_without_acceptance_is_409(self, tmp_path) -> None:
        from bpp.web.app import create_app

        workdir = tmp_path / "workdir"
        workdir.mkdir()
        (workdir / "analysis.json").write_text("[]", encoding="utf-8")
        app = create_app(workdir=str(workdir))
        app.config["TESTING"] = True
        with patch("bpp.utils.download.download_file") as dl:
            resp = app.test_client().post(
                "/api/v1/models/redownload", json={"name": "NudeNet 320n"}
            )
        assert resp.status_code == 409, resp.get_json()
        dl.assert_not_called()

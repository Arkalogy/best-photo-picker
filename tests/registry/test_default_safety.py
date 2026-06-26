"""Pin the Batch-2 default-safety guarantees.

Two pieces of Batch 2:

* Item 1 — lock SFace as the default face embedder via the registry.
* Item 2 — fail the build if any model weight file ships in an
  Arkalogy artifact.

These tests pin both pieces at the unit level so a refactor cannot
quietly weaken either guard.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from bpp.registry import (
    get_default_for_kind,
    iter_entries,
    register_entry,
)
from bpp.registry.builtins import DLIB_ENTRY, SFACE_ENTRY, register_builtins
from bpp.registry.model_registry import _reset_registry_for_tests


@pytest.fixture(autouse=True)
def _seed_registry() -> None:
    """Reset and re-seed the registry between tests so each starts
    from the same fresh built-in state."""
    _reset_registry_for_tests()
    register_builtins()


# ── Item 1: registry-driven default ──


class TestRegistryDefaultIsSFace:
    """A new install lands on SFace (Apache 2.0) with no user action
    and no path to a restricted model. These tests pin the
    "default = sface" contract at the registry."""

    def test_get_default_for_face_embedder_returns_sface(self) -> None:
        default = get_default_for_kind("face_embedder")
        assert default is not None
        assert default.id == "sface_yunet"
        assert default is SFACE_ENTRY

    def test_dlib_is_registered_but_not_the_default(self) -> None:
        default = get_default_for_kind("face_embedder")
        assert default is not DLIB_ENTRY
        assert DLIB_ENTRY.default_for_kind is False
        ids = {e.id for e in iter_entries()}
        assert DLIB_ENTRY.id in ids

    def test_get_default_for_unknown_kind_returns_none(self) -> None:
        """A kind no built-in covers (yet) returns None so callers
        can apply their own fallback. None is "no preference recorded,"
        not an error."""
        assert get_default_for_kind("semantic_embedder") is None

    def test_default_face_embedder_is_permissive(self) -> None:
        """The default for any kind MUST NOT carry a known commercial-
        use restriction. Structural guarantee against a future PR
        sneaking a restricted model into the default slot."""
        default = get_default_for_kind("face_embedder")
        assert default is not None
        assert default.commercial_use_restriction_known is False
        assert default.bppicker_commercial_default_allowed is True


class TestDefaultForKindInvariant:
    """Exactly one entry per kind may have default_for_kind=True."""

    def test_two_defaults_for_same_kind_raises(self) -> None:
        rogue_default = replace(
            DLIB_ENTRY,
            id="dlib_rogue_default",
            default_for_kind=True,
        )
        register_entry(rogue_default)
        with pytest.raises(RuntimeError, match="Multiple registered entries"):
            get_default_for_kind("face_embedder")

    def test_no_restricted_entry_is_marked_default(self) -> None:
        """Core legal-posture guarantee: a restricted model cannot
        be a default. Catches a future seed entry that mismatches
        commercial_use_restriction_known=True with
        default_for_kind=True."""
        for entry in iter_entries():
            if entry.default_for_kind:
                assert not entry.commercial_use_restriction_known, (
                    f"Entry {entry.id!r} is default AND restricted. "
                    f"A restricted model must never be the default."
                )


# ── Item 1 dispatch wiring ──


class TestEmbeddingMethodReadsRegistryDefault:
    """embedding_method(conn) now reads the registry default when no
    setting is configured, instead of going straight to availability
    detection. Pin the registry-consult path."""

    @staticmethod
    def _empty_settings_db() -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
        return conn

    def test_no_setting_returns_sface_via_registry_default(self) -> None:
        from bpp.scoring import face_embed

        conn = self._empty_settings_db()
        with patch.object(face_embed, "_get_sface_recognizer", return_value=object()):
            assert face_embed.embedding_method(conn) == "sface"

    def test_no_setting_no_conn_consults_registry_default(self) -> None:
        from bpp.scoring import face_embed

        # Even without a conn we now route through the registry
        # default before the availability check.
        with patch.object(face_embed, "_get_sface_recognizer", return_value=object()):
            assert face_embed.embedding_method() == "sface"


# ── Item 2: bundled-weights guard ──


def _import_guard_script() -> object:
    """Import scripts/check_no_bundled_weights.py as a module so
    tests can interrogate the classifier directly."""
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "check_no_bundled_weights.py"
    spec = importlib.util.spec_from_file_location("_check_no_bundled_weights", str(script_path))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TestBundledWeightsGuard:
    """Plant fake files in tempdirs and confirm the scanner finds
    them."""

    def test_clean_tree_is_reported_clean(self, tmp_path: Path) -> None:
        guard = _import_guard_script()
        (tmp_path / "module.py").write_text("# not a weight\n")
        (tmp_path / "README.md").write_text("docs\n")
        offenders = guard._scan_directory(tmp_path)
        assert offenders == []

    def test_weight_extension_file_is_caught(self, tmp_path: Path) -> None:
        guard = _import_guard_script()
        offender = tmp_path / "model.onnx"
        offender.write_bytes(b"fake bytes")
        offenders = guard._scan_directory(tmp_path)
        assert len(offenders) == 1
        path, reason = offenders[0]
        assert path == offender
        assert ".onnx" in reason

    def test_allowlisted_path_is_not_an_offender(self, tmp_path: Path) -> None:
        guard = _import_guard_script()
        rel = "scoring/models/blaze_face_short_range.tflite"
        offender = tmp_path / "scoring" / "models" / "blaze_face_short_range.tflite"
        offender.parent.mkdir(parents=True, exist_ok=True)
        offender.write_bytes(b"fake")
        assert rel in guard.ALLOWLIST
        offenders = guard._scan_directory(tmp_path)
        assert offenders == []

    def test_blocklisted_filename_caught_under_safe_extension(
        self,
        tmp_path: Path,
    ) -> None:
        """Even if a maintainer renames a restricted bundle to look
        benign, the blocklist match fires."""
        guard = _import_guard_script()
        offender = tmp_path / "adaface_ir50_ms1mv2.txt"
        offender.write_bytes(b"fake")
        offenders = guard._scan_directory(tmp_path)
        assert len(offenders) == 1
        _path, reason = offenders[0]
        assert "blocklisted" in reason
        assert "AdaFace" in reason

    def test_blocklisted_filename_beats_allowlist(self, tmp_path: Path) -> None:
        """A path appearing in both lists is treated as blocked.
        Catches a future "I'll allowlist my AdaFace checkpoint" PR."""
        guard = _import_guard_script()
        offender = tmp_path / "buffalo_s.onnx"
        offender.write_bytes(b"fake")
        guard.ALLOWLIST["buffalo_s.onnx"] = "test exemption"
        try:
            offenders = guard._scan_directory(tmp_path)
            assert len(offenders) == 1
            _path, reason = offenders[0]
            assert "blocklisted" in reason
        finally:
            del guard.ALLOWLIST["buffalo_s.onnx"]

    def test_real_repo_scan_is_clean(self) -> None:
        """The repo as it currently stands must pass the guard."""
        guard = _import_guard_script()
        repo_root = Path(__file__).resolve().parents[2]
        offenders = guard._scan_directory(repo_root / "bpp")
        assert offenders == [], (
            f"bpp/ contains weight-shape files not on the allowlist: {offenders}"
        )

    def test_blocklist_covers_known_restricted_bundles(self) -> None:
        """The legal-posture rollout specifically named these as restricted
        bundles that must never ship."""
        guard = _import_guard_script()
        names = {pattern for pattern, _ in guard.RESTRICTED_BLOCKLIST}
        for required in (
            "adaface_",
            "buffalo_s",
            "buffalo_m",
            "buffalo_l",
            "antelopev2",
            "w600k_",
        ):
            assert required in names, (
                f"Blocklist missing {required!r} — the legal-posture spec "
                f"identified this as a restricted bundle."
            )

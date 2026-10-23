"""Anti-rollback for the signed remote manifest.

Pins:

* :func:`verify_manifest` requires a positive integer
  ``manifest_version`` and exposes it on the result. A manifest
  without one (every manifest published before the rule) is
  refused, so an old signed copy can never be replayed as current.
* The high-water mark (:mod:`bpp.registry.manifest_rollback`) lives
  under ``cache_dir()``, one row per registry URL; a missing or
  damaged file reads as 0 and only a higher version is written back.
* The startup overlay refuses a manifest whose version is below the
  recorded one for its URL (the replay case: a withdrawn model would
  come back as available), records a higher one, and accepts an
  equal one (a refetch of the same manifest).
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import bpp.registry as registry_pkg
from bpp.registry import (
    DEFAULT_REMOTE_REGISTRY_URL,
    ModelStatus,
    TrustedKey,
    get_entry,
    sign_manifest,
    verify_manifest,
)
from bpp.registry.manifest_rollback import (
    STATE_FILENAME,
    highest_applied_version,
    record_applied_version,
    rollback_reason,
    state_path,
)

PROBE_ID = "rollback_probe_entry"
SRC = DEFAULT_REMOTE_REGISTRY_URL
OTHER_SRC = "http://127.0.0.1:9088/registry.json"


def _key() -> tuple[str, Ed25519PrivateKey, TrustedKey]:
    priv = Ed25519PrivateKey.generate()
    raw_pub = priv.public_key().public_bytes_raw()
    key_id = "test-key-" + base64.b64encode(raw_pub[:6]).decode("ascii")
    trusted = TrustedKey(
        key_id=key_id,
        public_key_b64=base64.b64encode(raw_pub).decode("ascii"),
        maintainer_label="test",
    )
    return key_id, priv, trusted


def _manifest(version: int | None, status: str = "available") -> dict:
    m: dict = {
        "schema_version": 1,
        "generated_at": "2026-10-18T00:00:00Z",
        "entries": [
            {
                "id": PROBE_ID,
                "display_name": "Rollback probe",
                "kind": "face_embedder",
                "source_url": "https://example.invalid/p.onnx",
                "terms_url": "https://example.invalid/L",
                "terms_retrieved_at": "2026-10-18",
                "license_summary": "Permissive test entry",
                "requires_explicit_ack": False,
                "upstream_claimed_license_class": "apache_2_0",
                "commercial_use_restriction_known": False,
                "bppicker_commercial_default_allowed": True,
                "status": status,
                "training_data": "synthetic",
                "weight_sha256": "0" * 64,
            }
        ],
    }
    if version is not None:
        m["manifest_version"] = version
    return m


@pytest.fixture
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("BPP_CACHE_DIR", str(tmp_path))
    return tmp_path


class TestVerifierRequiresVersion:
    def test_versioned_manifest_verifies_and_exposes_version(self) -> None:
        key_id, priv, trusted = _key()
        result = verify_manifest(
            sign_manifest(_manifest(7), [(key_id, priv)]), trusted_keys=[trusted]
        )
        assert result.is_valid
        assert result.manifest_version == 7

    def test_missing_version_is_refused(self) -> None:
        key_id, priv, trusted = _key()
        result = verify_manifest(
            sign_manifest(_manifest(None), [(key_id, priv)]), trusted_keys=[trusted]
        )
        assert not result.is_valid
        assert any("manifest_version" in err for err in result.errors), result.errors

    @pytest.mark.parametrize("bad", [0, -1, "7", 7.0, True, None])
    def test_non_positive_integer_version_is_refused(self, bad: object) -> None:
        key_id, priv, trusted = _key()
        m = _manifest(1)
        m["manifest_version"] = bad
        result = verify_manifest(sign_manifest(m, [(key_id, priv)]), trusted_keys=[trusted])
        assert not result.is_valid, bad
        assert any("manifest_version" in err for err in result.errors), (bad, result.errors)


class TestHighWaterMark:
    def test_absent_reads_as_zero(self, cache_dir: Path) -> None:
        assert state_path() == cache_dir / STATE_FILENAME
        assert highest_applied_version(SRC) == 0
        assert rollback_reason(1, source_url=SRC) is None

    def test_record_then_read(self, cache_dir: Path) -> None:
        record_applied_version(3, source_url=SRC, generated_at="2026-10-18T00:00:00Z")
        assert highest_applied_version(SRC) == 3
        saved = json.loads(state_path().read_text())["sources"][SRC]
        assert saved["manifest_version"] == 3
        assert saved["generated_at"] == "2026-10-18T00:00:00Z"
        assert saved["applied_at"]

    def test_lower_version_is_a_rollback_and_is_not_recorded(self, cache_dir: Path) -> None:
        record_applied_version(3, source_url=SRC, generated_at=None)
        reason = rollback_reason(2, source_url=SRC)
        assert reason is not None and "rollback" in reason.lower(), reason
        record_applied_version(2, source_url=SRC, generated_at=None)
        assert highest_applied_version(SRC) == 3

    def test_equal_version_is_not_a_rollback(self, cache_dir: Path) -> None:
        record_applied_version(3, source_url=SRC, generated_at=None)
        assert rollback_reason(3, source_url=SRC) is None

    def test_marks_are_per_url(self, cache_dir: Path) -> None:
        """A mock registry (another URL) neither inherits nor poisons the
        real registry's mark."""
        record_applied_version(100, source_url=OTHER_SRC, generated_at=None)
        assert highest_applied_version(SRC) == 0
        assert rollback_reason(6, source_url=SRC) is None
        record_applied_version(6, source_url=SRC, generated_at=None)
        assert highest_applied_version(OTHER_SRC) == 100
        assert highest_applied_version(SRC) == 6

    @pytest.mark.parametrize(
        "junk",
        [
            "",
            "not json",
            "[]",
            '{"sources": []}',
            json.dumps({"sources": {SRC: {"manifest_version": "9"}}}),
        ],
    )
    def test_damaged_state_reads_as_zero(self, cache_dir: Path, junk: str) -> None:
        state_path().write_text(junk)
        assert highest_applied_version(SRC) == 0, junk

    def test_unwritable_cache_dir_does_not_raise(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        blocker = tmp_path / "file-not-dir"
        blocker.write_text("x")
        monkeypatch.setenv("BPP_CACHE_DIR", str(blocker))
        record_applied_version(1, source_url=SRC, generated_at=None)  # logs, no exception
        assert highest_applied_version(SRC) == 0

    def test_no_temp_or_lock_file_is_left_behind(self, cache_dir: Path) -> None:
        record_applied_version(1, source_url=SRC, generated_at=None)
        names = [p.name for p in cache_dir.iterdir()]
        assert STATE_FILENAME in names, names
        assert not [n for n in names if n.endswith((".tmp", ".lock"))], names

    def test_damaged_state_is_set_aside_not_overwritten(self, cache_dir: Path) -> None:
        state_path().write_text("{not json")
        record_applied_version(2, source_url=SRC, generated_at=None)
        assert highest_applied_version(SRC) == 2
        kept = [p.name for p in cache_dir.iterdir() if ".corrupt-" in p.name]
        assert len(kept) == 1, kept

    def test_stale_lock_does_not_block(self, cache_dir: Path) -> None:
        lock = cache_dir / (STATE_FILENAME + ".lock")
        lock.write_text("")
        old = time.time() - 60
        os.utime(lock, (old, old))
        record_applied_version(2, source_url=SRC, generated_at=None)
        assert highest_applied_version(SRC) == 2
        assert not lock.exists()

    def test_held_lock_is_waited_for_then_written_anyway(
        self, cache_dir: Path, monkeypatch: pytest.MonkeyPatch, caplog
    ) -> None:
        import bpp.registry.manifest_rollback as mr

        monkeypatch.setattr(mr, "_LOCK_WAIT_S", 0.1)
        lock = cache_dir / (STATE_FILENAME + ".lock")
        lock.write_text("")  # fresh: another process is mid-write
        with caplog.at_level(logging.WARNING, logger="bpp.registry.manifest_rollback"):
            record_applied_version(2, source_url=SRC, generated_at=None)
        assert highest_applied_version(SRC) == 2
        assert lock.exists()  # not ours to remove
        assert any("held too long" in r.getMessage() for r in caplog.records)


class TestStartupOverlay:
    """Drive ``_try_apply_remote_overlay`` with a fake fetch."""

    @pytest.fixture
    def serve(self, cache_dir: Path, monkeypatch: pytest.MonkeyPatch):
        key_id, priv, trusted = _key()
        monkeypatch.delenv("BPP_DISABLE_REMOTE_REGISTRY", raising=False)
        monkeypatch.delenv("BPP_REMOTE_REGISTRY_URL", raising=False)
        monkeypatch.setattr(registry_pkg, "is_all_placeholder_keys", lambda: False)
        monkeypatch.setattr(registry_pkg, "trusted_key_list", lambda: [trusted])

        def _serve(manifest: dict) -> None:
            signed = sign_manifest(manifest, [(key_id, priv)])
            monkeypatch.setattr(
                registry_pkg, "fetch_remote_manifest", lambda: json.dumps(signed).encode("utf-8")
            )
            registry_pkg._try_apply_remote_overlay()

        return _serve

    def test_replayed_older_manifest_is_refused(self, serve, caplog) -> None:
        serve(_manifest(2, status="withdrawn_no_new_downloads"))
        assert get_entry(PROBE_ID).status is ModelStatus.WITHDRAWN_NO_NEW_DOWNLOADS
        assert highest_applied_version(SRC) == 2

        with caplog.at_level(logging.WARNING, logger="bpp.registry"):
            serve(_manifest(1, status="available"))
        assert get_entry(PROBE_ID).status is ModelStatus.WITHDRAWN_NO_NEW_DOWNLOADS
        assert highest_applied_version(SRC) == 2
        assert any("rollback" in rec.getMessage().lower() for rec in caplog.records), [
            rec.getMessage() for rec in caplog.records
        ]

    def test_same_version_refetch_and_newer_version_apply(self, serve) -> None:
        serve(_manifest(2, status="withdrawn_no_new_downloads"))
        serve(_manifest(2, status="withdrawn_no_new_downloads"))
        assert highest_applied_version(SRC) == 2
        serve(_manifest(3, status="available"))
        assert get_entry(PROBE_ID).status is ModelStatus.AVAILABLE
        assert highest_applied_version(SRC) == 3

    def test_unversioned_manifest_is_not_applied(self, serve) -> None:
        serve(_manifest(None))
        assert get_entry(PROBE_ID) is None
        assert highest_applied_version(SRC) == 0

    def test_mark_follows_the_registry_url(self, serve, monkeypatch) -> None:
        serve(_manifest(5))
        monkeypatch.setenv("BPP_REMOTE_REGISTRY_URL", OTHER_SRC)
        serve(_manifest(1, status="withdrawn_no_new_downloads"))
        assert get_entry(PROBE_ID).status is ModelStatus.WITHDRAWN_NO_NEW_DOWNLOADS
        assert highest_applied_version(OTHER_SRC) == 1
        assert highest_applied_version(SRC) == 5


class TestVerifyCommand:
    def test_prints_manifest_version(self, tmp_path: Path, monkeypatch, capsys) -> None:
        from bpp.commands import model_commands

        key_id, priv, trusted = _key()
        monkeypatch.setattr(registry_pkg, "trusted_key_list", lambda: [trusted])
        path = tmp_path / "signed.json"
        path.write_text(json.dumps(sign_manifest(_manifest(5), [(key_id, priv)])))
        assert model_commands.do_registry_verify(argparse.Namespace(path=str(path))) == 0
        assert "manifest_version: 5" in capsys.readouterr().out

    def test_non_object_file_fails_cleanly(self, tmp_path: Path, capsys) -> None:
        from bpp.commands import model_commands

        path = tmp_path / "bad.json"
        path.write_text("[1, 2]")
        assert model_commands.do_registry_verify(argparse.Namespace(path=str(path))) == 1
        assert "not a JSON object" in capsys.readouterr().err
        path.write_text('{"entries": [1,]}')
        assert model_commands.do_registry_verify(argparse.Namespace(path=str(path))) == 1
        err = capsys.readouterr().err
        assert "not valid JSON" in err and "line 1" in err, err

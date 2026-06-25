"""Status-gate enforcement on the redownload endpoint.

Item 12 (upstream-takedown disable path) + item 20 (multi-status
lifecycle): when a model entry's status flips to
``WITHDRAWN_NO_NEW_DOWNLOADS`` or ``LEGALLY_BLOCKED`` via the signed
remote-registry overlay, the Redownload button in Settings → Models
must honor that — it cannot silently re-fetch a model the world has
formally taken down.

Without this gate, the entire remote-registry signing chain is
cosmetic: an attacker who wants the model just clicks Redownload and
gets it back.

The gate runs at the HTTP boundary (``bp_models.py``) because that's
the user-controllable entry point. The runtime load gate
(``policy.py``) already blocks the *use* of a withdrawn or blocked
model; this test pins the corresponding *download* refusal.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import patch

import pytest

from bpp.errors import BppError
from bpp.registry import get_entry
from bpp.registry.model_registry import ModelStatus, register_entry
from bpp.web.bp_models import _redownload_model


def _mark_status(legal_entry_id: str, status: ModelStatus) -> None:
    """Mutate the bundled-baseline entry's status for the test by
    re-registering with the updated field. ``register_entry`` is
    idempotent on ``id`` (replacement is the documented behavior used
    by the signed remote-registry overlay), and the conftest restores
    the bundled baseline after each test."""
    entry = get_entry(legal_entry_id)
    assert entry is not None, (
        f"test setup error: {legal_entry_id!r} should be present in the bundled baseline"
    )
    register_entry(replace(entry, status=status))


class TestDownloadStatusGate:
    def test_legally_blocked_model_refuses_redownload(self) -> None:
        """``LEGALLY_BLOCKED`` is the strongest takedown state. A user
        clicking Redownload on the file that resolves to a blocked
        legal entry must get a clear refusal, not a silent re-fetch."""
        _mark_status("sface_yunet", ModelStatus.LEGALLY_BLOCKED)
        with pytest.raises(BppError) as excinfo:
            _redownload_model("SFace recognition")
        assert "legally blocked" in excinfo.value.user_message.lower()

    def test_withdrawn_no_new_downloads_refuses_redownload(self) -> None:
        """``WITHDRAWN_NO_NEW_DOWNLOADS`` blocks new fetches but lets
        existing local copies remain usable. The download path is the
        first gate; the load gate handles the existing-copy case."""
        _mark_status("sface_yunet", ModelStatus.WITHDRAWN_NO_NEW_DOWNLOADS)
        with pytest.raises(BppError) as excinfo:
            _redownload_model("SFace recognition")
        assert "withdrawn" in excinfo.value.user_message.lower()

    def test_available_model_proceeds_to_download(self) -> None:
        """``AVAILABLE`` is the happy path — the status gate must not
        block legitimate redownloads. We stub the download_file call so
        the test doesn't hit the network; the assertion is "we reached
        the download step at all"."""
        with (
            patch("bpp.utils.download.download_file") as mock_download,
            patch("bpp.web.bp_models._reset_model_cache"),
            patch("bpp.web.bp_models.os.replace"),
            patch("bpp.web.bp_models.os.path.exists", return_value=False),
            patch("bpp.web.bp_models.os.makedirs"),
            patch(
                "bpp.web.models_status._file_info",
                return_value={"size_bytes": 100, "exists": True},
            ),
        ):
            _redownload_model("SFace recognition")
            mock_download.assert_called_once()

    def test_unmapped_legacy_name_does_not_block(self) -> None:
        """``BlazeFace short-range`` has no legal-registry counterpart
        (it's an ancillary detector with no licensing concern). The
        status gate must NOT manufacture a block on a name it can't
        map — it falls through to the normal download path. This is a
        defensive test: a future model added to the scoring registry
        without a legal-registry entry stays unrestricted until
        someone classifies it."""
        with (
            patch("bpp.utils.download.download_file") as mock_download,
            patch("bpp.web.bp_models._reset_model_cache"),
            patch("bpp.web.bp_models.os.replace"),
            patch("bpp.web.bp_models.os.path.exists", return_value=False),
            patch("bpp.web.bp_models.os.makedirs"),
            patch(
                "bpp.web.models_status._file_info",
                return_value={"size_bytes": 100, "exists": True},
            ),
        ):
            _redownload_model("BlazeFace short-range")
            mock_download.assert_called_once()

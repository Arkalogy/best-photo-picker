"""Session-wide test configuration.

Redirects XDG_CONFIG_HOME to a tmp directory so tests never pollute
~/.config/bpp/libraries.json with ephemeral test library paths.
"""

import os

import pytest

# Disable the Batch 8 remote-registry fetch for every test run. The
# placeholder URL doesn't exist yet, and even once it does, tests that
# rely on a stable in-process registry should not depend on a live
# network call. Set in the module's import-time scope so the fetch
# never runs even if a test imports ``bpp.registry`` before any
# fixture fires.
os.environ.setdefault("BPP_DISABLE_REMOTE_REGISTRY", "1")


@pytest.fixture(autouse=True)
def _isolate_config_home(tmp_path, monkeypatch):
    """Redirect XDG_CONFIG_HOME for every test."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))


@pytest.fixture(autouse=True)
def _resync_download_chokepoint():
    """Keep the download-chokepoint bookkeeping in sync with reality
    before each test, so orchestrator-driven tests don't trip a false
    tripwire.

    The chokepoint tracks which auto-downloaders it has patched in a
    module-level set (``_patched``). Some tests legitimately reset or
    reload that state. If ``insightface`` was already imported by an
    earlier test (e.g. the buffalo_s embedder under ``tests/scoring`` or
    ``tests/registry``), the set can end up empty while
    ``insightface.utils.storage`` is still in ``sys.modules``. The next
    test that runs the face orchestrator then calls
    ``enforce_chokepoint()``, sees "module loaded but patch missing", and
    fails closed with ``BlockedAutoDownloadError`` — a test-isolation
    artifact, not a real exposure. (CI never reproduces it: insightface
    isn't installed there, so those tests skip and nothing imports it.)

    Re-running ``install_third_party_interceptions()`` re-patches the
    loaded module and repopulates the set. The guard means we only act
    when a known downloader is already imported, so this never forces a
    heavy insightface import in suites that don't otherwise need one
    (e.g. running the journal tests in isolation, or on CI).
    """
    import sys

    from bpp.registry.download_chokepoint import (
        KNOWN_AUTO_DOWNLOADERS,
        install_third_party_interceptions,
    )

    if any(entry.submodule in sys.modules for entry in KNOWN_AUTO_DOWNLOADERS):
        install_third_party_interceptions()


@pytest.fixture(autouse=True)
def _disable_health_checks(request, monkeypatch):
    """Prevent background file-health checks from marking test photos as missing.

    Test fixtures create photos in analysis.json but don't create the actual
    files on disk. Without this, WebAppState._start_file_health_checks() starts
    a background thread that immediately marks every photo as missing=1 before
    the test can query them — causing intermittent assert 0 == N failures in
    tests that switch libraries or check photo counts right after startup.

    Tests that directly exercise the health scan should be marked with
    @pytest.mark.real_health_checks to opt out of this patch.
    """
    if request.node.get_closest_marker("real_health_checks"):
        return
    from bpp.web import state

    monkeypatch.setattr(state.WebAppState, "_start_file_health_checks", lambda self: None)


@pytest.fixture(autouse=True)
def _close_db_connections():
    """Close all pooled DB connections after every test.

    The connection pool is process-global. Tests that create Flask apps
    leave open SQLite connections in the pool; subsequent tests may pick
    up a stale connection pointing at the previous test's temp DB, causing
    photo counts to return 0. Closing after each test guarantees a fresh
    connection on the next test's first get_db() call.
    """
    yield
    from bpp.db.connection import close_all_connections

    close_all_connections()


@pytest.fixture(autouse=True)
def _preaccept_permissive_attribution_entries(request, tmp_path, monkeypatch):
    """Pre-accept the permissive-attribution entries (SFace, YuNet, dlib).

    Under the Option B legal posture (only MIT bypasses the
    click-through), these Apache-2.0 / Boost models require an
    acceptance row before the runtime gate lets them load. Most
    integration tests aren't intentionally exercising the gate —
    they're testing scoring behaviour that happens to use these
    models. Without a pre-acceptance, those tests fail with
    ``Model load blocked: sface_yunet — blocked_needs_ack``.

    Tests that DO want to verify the gate's reject path opt out
    via ``@pytest.mark.no_preaccept_permissive``. The registry
    test suite manages its own acceptance log so those tests
    aren't affected here either way.
    """
    if request.node.get_closest_marker("no_preaccept_permissive"):
        return
    # Skip when the test sets its own acceptance log path (the
    # registry tests do this with BPP_ACCEPTANCE_LOG_PATH). Without
    # this guard we'd write the pre-accept row into the wrong file
    # and break the test's own gate assertions.
    if os.environ.get("BPP_ACCEPTANCE_LOG_PATH"):
        return
    log_path = tmp_path / "preaccept-acceptance.jsonl"
    monkeypatch.setenv("BPP_ACCEPTANCE_LOG_PATH", str(log_path))
    from bpp.registry.acceptance_log import AcceptanceRow, append_row
    from bpp.registry.disclaimers import (
        PERMISSIVE_ATTRIBUTION_DISCLAIMER_VERSION,
        permissive_attribution_disclaimer_sha256,
    )
    from bpp.registry.model_registry import get_entry

    # Use the real disclaimer hash so ``is_acceptance_valid_for``
    # accepts the row. Without this the row writes but the gate
    # still rejects because the ack_text_sha256 mismatches the
    # entry's current value.
    ack_sha = permissive_attribution_disclaimer_sha256()
    for entry_id in (
        "sface_yunet",
        "dlib_face_recognition_resnet_v1",
        "opencv_yunet",
    ):
        entry = get_entry(entry_id)
        # Defensive: if a future test runner imports a registry that
        # doesn't have one of these entries, skip rather than fail.
        if entry is None:
            continue
        append_row(
            AcceptanceRow(
                model_id=entry_id,
                model_sha256="",
                ack_text_version=PERMISSIVE_ATTRIBUTION_DISCLAIMER_VERSION,
                ack_text_sha256=ack_sha,
                use_context_text_version="",
                use_context_text_sha256="",
                use_context_at_acceptance="personal",
                separate_rights_asserted=False,
                terms_url=entry.terms_url,
                terms_permalink_url=entry.terms_permalink_url or "",
                terms_retrieved_at=entry.terms_retrieved_at,
                accepted_at="2026-01-01T00:00:00+00:00",
                source_of_rights_note="",
                checkbox_responses={"permissive_attribution_acknowledged": True},
                event="accept",
            ),
            path=log_path,
        )

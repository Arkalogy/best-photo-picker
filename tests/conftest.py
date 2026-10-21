"""Session-wide test configuration.

Redirects XDG_CONFIG_HOME to a tmp directory so tests never pollute
~/.config/bpp/libraries.json with ephemeral test library paths.
"""

import os

import pytest

# Disable the signed remote-registry fetch for every test run. The
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
def _isolate_install_state(tmp_path, monkeypatch):
    """Nothing held for a restart, and the installer's hold file and notes
    in this test's folder — never the real cache (or the venv's notes)."""
    from bpp.scoring import install_hold
    from bpp.web import pip_process

    held_file = tmp_path / "install-held" / "held-app.txt"
    monkeypatch.setattr(install_hold, "_installed", frozenset())
    monkeypatch.setattr(install_hold, "_written", frozenset())
    monkeypatch.setattr(install_hold, "_inherited", frozenset())
    monkeypatch.setattr(install_hold, "_held", frozenset())
    monkeypatch.setattr(install_hold, "_own_file", held_file)
    monkeypatch.setattr(install_hold, "_own_file_path", lambda: held_file)  # (become_app)
    monkeypatch.setenv(install_hold._ENV, str(held_file))
    monkeypatch.setattr(pip_process, "MARKER_DIR", tmp_path / "install-notes")
    monkeypatch.setattr(pip_process, "_notes_cache", None)
    from bpp.web import install_status

    monkeypatch.setattr(install_status, "_facts_cache", None)


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
def _reset_license_gate_state():
    """Forget cached license-gate passes and the face_recognition import.

    Some tests put a fake ``face_recognition`` in ``sys.modules``. The
    loader caches whatever it imports first, and since it only imports
    once the dlib license is accepted, that first import can happen
    inside such a test — the fake would then leak into later tests.
    """
    yield
    from bpp.scoring import face_recognition_loader, model_load_gate

    face_recognition_loader._reset_for_tests()
    # Each test has its own acceptance log; a gate pass memoized under
    # one test's log must not carry into the next.
    for gate in model_load_gate._ALL_GATES:
        gate.reset()
    from bpp.web import model_download_jobs, model_filter

    model_filter._license_blocked_cache.clear()
    # The download-job registry is process-global: a job another test
    # left running or failed would degrade /api/v1/health for the next.
    model_download_jobs._reset_for_tests()


@pytest.fixture(autouse=True)
def _preaccept_permissive_attribution_entries(request, tmp_path, monkeypatch):
    """Pre-accept every attribution-licensed model plus dlib.

    Because only MIT-licensed models bypass the
    click-through, these models (Apache-2.0 attribution; dlib is
    restricted for commercial use) require an
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
    from bpp.registry.attribution_bundle import is_attribution_entry
    from bpp.registry.model_registry import get_entry, iter_entries

    # Use each entry's own current ack text version + hash so
    # ``is_acceptance_valid_for`` accepts the row. Without this the row
    # writes but the gate still rejects because the ack_text_sha256
    # mismatches the entry's current value.
    # Every model a default analysis loads (SFace, YuNet, MediaPipe, the
    # Haar cascade), derived from the registry so a new attribution
    # model is covered automatically; plus dlib, which many face tests use.
    preaccepted = [e.id for e in iter_entries() if is_attribution_entry(e)]
    for entry_id in [*preaccepted, "dlib_face_recognition_resnet_v1"]:
        entry = get_entry(entry_id)
        # Defensive: if a future test runner imports a registry that
        # doesn't have one of these entries, skip rather than fail.
        if entry is None:
            continue
        append_row(
            AcceptanceRow(
                model_id=entry_id,
                model_sha256="",
                ack_text_version=entry.ack_text_version,
                ack_text_sha256=entry.ack_text_sha256,
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


@pytest.fixture(autouse=True)
def _restore_face_backend_registries():
    """Undo face detectors / embedders a test registered.

    Registered detectors run on every photo, so one left behind by a
    test (a spy that returns a box) would change later tests' results.
    """
    # Register the built-ins before the snapshot, so a first import
    # during a test can't be "undone" afterwards.
    import bpp.scoring.face_embed
    import bpp.scoring.face_pipeline  # noqa: F401
    from bpp.scoring.face_detector_registry import _REGISTRY as detectors
    from bpp.scoring.face_embedder_registry import _REGISTRY as embedders

    saved = (dict(detectors._store), dict(embedders._store))
    yield
    detectors._store.clear()
    detectors._store.update(saved[0])
    embedders._store.clear()
    embedders._store.update(saved[1])


class NetworkBlockedInTests(BaseException):
    """A test reached the network. Tests stub what they fetch; a test that
    truly needs the network opts in with ``@pytest.mark.network``.

    A ``BaseException``, not an ``Exception``: the model loaders catch
    ``Exception`` and degrade to "model unavailable", which would let an
    un-stubbed test pass (and sleep through the download retries)."""


#: (thread, error) for every hit from a thread other than the main one;
#: recorded here rather than through ``threading.excepthook``, which
#: pytest swaps in and out around every test phase.
_thread_hits: list[tuple[object, BaseException]] = []


def _blocked(url, *args, **kwargs):
    import threading

    target = getattr(url, "full_url", url)
    err = NetworkBlockedInTests(f"test tried to fetch {target}; stub it or mark the test network")
    if threading.current_thread() is not threading.main_thread():
        _thread_hits.append((threading.current_thread(), err))
    raise err


def _blocked_open(self, fullurl, *args, **kwargs):  # OpenerDirector.open is a method
    _blocked(fullurl)


class _NetworkBlock:
    """The suite-wide refusal, installed once per process and never taken
    down: a worker thread or child process that outlives its test (the
    derived-recovery thread outlived a whole xdist session once and pulled
    254 MB of CLIP after the last test) must stay covered. ``saved`` holds
    the originals so a ``network`` test can have them back."""

    def __init__(self) -> None:
        self.saved: list[tuple[object, str, object]] = []

    def install(self) -> None:
        import multiprocessing.process as mp_process
        import subprocess
        import sys
        import urllib.request

        real_popen = subprocess.Popen
        real_start = mp_process.BaseProcess.start

        class _PopenNoDownloads(real_popen):
            """Child processes (scoring / face workers) refuse every download."""

            def __init__(  # Popen's own parameter order, so env may arrive positionally
                self,
                args,
                bufsize=-1,
                executable=None,
                stdin=None,
                stdout=None,
                stderr=None,
                preexec_fn=None,
                close_fds=True,
                shell=False,
                cwd=None,
                env=None,
                *a,
                **kw,
            ):
                env = dict(env if env is not None else os.environ)
                env["BPP_SKIP_DOWNLOADS"] = "*"
                super().__init__(
                    args,
                    bufsize,
                    executable,
                    stdin,
                    stdout,
                    stderr,
                    preexec_fn,
                    close_fds,
                    shell,
                    cwd,
                    env,
                    *a,
                    **kw,
                )

        # multiprocessing children (spawn context) copy the parent's
        # environment when they start: carry the refusal across that
        # start, and only that start, so the parent's own download tests
        # keep their fakes.
        def _start_without_downloads(proc, *a, **kw):
            before = os.environ.get("BPP_SKIP_DOWNLOADS")
            os.environ["BPP_SKIP_DOWNLOADS"] = "*"
            try:
                return real_start(proc, *a, **kw)
            finally:
                if before is None:
                    os.environ.pop("BPP_SKIP_DOWNLOADS", None)
                else:
                    os.environ["BPP_SKIP_DOWNLOADS"] = before

        patches: list[tuple[object, str, object]] = [
            (urllib.request, "urlopen", _blocked),
            (urllib.request.OpenerDirector, "open", _blocked_open),
            (subprocess, "Popen", _PopenNoDownloads),
            (mp_process.BaseProcess, "start", _start_without_downloads),
        ]
        for name in ("torch.hub",):  # bound urlopen by name before the block
            mod = sys.modules.get(name)
            if mod is not None:
                patches += [(mod, "urlopen", _blocked), (mod, "download_url_to_file", _blocked)]
        self.saved = [(obj, attr, getattr(obj, attr)) for obj, attr, _ in patches]
        for obj, attr, new in patches:
            setattr(obj, attr, new)


@pytest.fixture
def real_face_pipeline():
    """Skip unless the real face pipeline (detect → landmarks → embed) can
    run from weights on disk; see ``tests/_models.py``."""
    from tests._models import skip_unless_face_pipeline

    skip_unless_face_pipeline()


@pytest.fixture
def sface_configured(monkeypatch):
    """``embedding_method()`` loads the SFace recognizer just to answer
    which backend is configured; without the weights that reads as a
    switch to dlib. Tests about the orchestration don't need the weights."""
    from bpp.scoring import face_embed
    from bpp.web import face_phase_classes

    monkeypatch.setattr(face_embed, "embedding_method", lambda conn=None: "sface")
    monkeypatch.setattr(face_phase_classes, "embedding_method", lambda conn=None: "sface")


@pytest.fixture
def offline_downloads(monkeypatch):
    """The app under test is offline, like a machine without network: a
    model that isn't on disk is simply off for the run (download_file
    refuses instead of fetching). For tests that run an import / analyze
    in-process and aren't about the models."""
    monkeypatch.setenv("BPP_SKIP_DOWNLOADS", "*")


@pytest.fixture(scope="session", autouse=True)
def _no_network_session():
    """No test downloads anything. A fresh clone with an empty model cache
    once pulled ~600 MB of models through the suite; now code that reaches
    the network fails naming the URL, so the test gets a stub (or, for a
    test about real weights, a skip when they're absent). Processes a test
    starts inherit the refusal through ``BPP_SKIP_DOWNLOADS=*``. CI, which
    keeps a model cache, opts out with ``BPP_TEST_ALLOW_DOWNLOADS=1`` so
    the real-weight tests run there."""
    from tests._models import downloads_allowed

    if downloads_allowed():
        return None
    block = _NetworkBlock()
    block.install()
    return block


@pytest.fixture(autouse=True)
def _no_network(request, monkeypatch, _no_network_session):
    """Per test: a ``network``-marked test gets the real network back for
    its duration; every other test fails at teardown if a thread it
    started reached the network (a thread an earlier test left running
    is blocked too, but reported only on stderr). Tests that patch
    ``urlopen`` themselves still can."""
    import threading

    block = _no_network_session
    if block is None:
        yield
        return
    if request.node.get_closest_marker("network"):
        for obj, attr, old in block.saved:
            monkeypatch.setattr(obj, attr, old)
        yield
        return
    # A background thread (model download jobs run in one) that hits the
    # block dies with the BaseException instead of reporting failure, so
    # the test would only see a stuck job: fail it by URL at teardown.
    already_running = set(threading.enumerate())  # by object: thread ids get recycled
    _thread_hits.clear()
    yield
    own = [err for thread, err in _thread_hits if thread not in already_running]
    if own:
        pytest.fail(f"a thread this test started reached the network: {own[0]}")

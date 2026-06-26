"""TDD tests for C-2: restrict /api/open-folder to library root."""

from __future__ import annotations

import os
import subprocess
from unittest.mock import MagicMock, patch

from PIL import Image

from bpp.web.app import create_app


def _app_and_dir(tmp_path):
    d = str(tmp_path)
    Image.new("RGB", (10, 10), "red").save(os.path.join(d, "x.jpg"), "JPEG")
    app = create_app(workdir=d, input_dir=d, library_path=d)
    app.config["TESTING"] = True
    return app, d


def _ok_subprocess():
    """Mock that simulates a successful OS launcher invocation."""
    m = MagicMock()
    m.run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
    return m


def _force_launcher(monkeypatch):
    """Force `_open_folder_cmd` to return a fake argv so endpoint logic
    runs cross-platform. CI Linux runners have no DISPLAY → the real
    `_open_folder_cmd` returns None and the endpoint short-circuits to
    501 before the subprocess mock kicks in. Tests here exercise the
    post-launcher path; they don't care which binary would have run."""
    monkeypatch.setattr(
        "bpp.web.bp_os_integration._open_folder_cmd",
        # Use `echo` because it's on PATH on both macOS and Linux —
        # the endpoint runs shutil.which() before the subprocess mock
        # kicks in, so a fake binary name produces a 502 even with
        # subprocess.run mocked.
        lambda path: ["echo", path],
    )


class TestOpenFolderRestriction:
    def test_library_subdir_allowed(self, tmp_path, monkeypatch):
        app, d = _app_and_dir(tmp_path)
        sub = os.path.join(d, "sub")
        os.makedirs(sub)
        _force_launcher(monkeypatch)
        with (
            app.test_client() as c,
            patch("bpp.web.bp_os_integration.subprocess", _ok_subprocess()),
        ):
            resp = c.post("/api/v1/open-folder", json={"path": sub})
            assert resp.status_code == 200

    def test_library_root_allowed(self, tmp_path, monkeypatch):
        app, d = _app_and_dir(tmp_path)
        _force_launcher(monkeypatch)
        with (
            app.test_client() as c,
            patch("bpp.web.bp_os_integration.subprocess", _ok_subprocess()),
        ):
            resp = c.post("/api/v1/open-folder", json={"path": d})
            assert resp.status_code == 200

    def test_outside_library_rejected(self, tmp_path):
        app, _d = _app_and_dir(tmp_path)
        with app.test_client() as c:
            resp = c.post("/api/v1/open-folder", json={"path": "/tmp"})
            assert resp.status_code == 403
            assert "outside" in resp.get_json()["error"].lower()

    def test_path_traversal_rejected(self, tmp_path):
        app, d = _app_and_dir(tmp_path)
        traversal = os.path.join(d, "..", "..")
        with app.test_client() as c:
            resp = c.post("/api/v1/open-folder", json={"path": traversal})
            assert resp.status_code == 403

    def test_symlink_escape_rejected(self, tmp_path):
        app, d = _app_and_dir(tmp_path)
        link = os.path.join(d, "escape")
        os.symlink("/tmp", link)
        with app.test_client() as c:
            resp = c.post("/api/v1/open-folder", json={"path": link})
            assert resp.status_code == 403

    def test_home_dir_allowed(self, tmp_path, monkeypatch):
        """User's home directory should be allowed (for export folder)."""
        app, _d = _app_and_dir(tmp_path)
        home = os.path.expanduser("~")
        _force_launcher(monkeypatch)
        with (
            app.test_client() as c,
            patch("bpp.web.bp_os_integration.subprocess", _ok_subprocess()),
        ):
            resp = c.post("/api/v1/open-folder", json={"path": home})
            assert resp.status_code == 200


class TestOpenFolderSubprocessFailure:
    """The endpoint must surface launcher failures as proper HTTP errors
    instead of returning 200 OK on a silent failure."""

    def test_nonzero_exit_returns_502(self, tmp_path, monkeypatch):
        """When the OS launcher exits non-zero (e.g., `open` can't
        find a handler), the endpoint reports it instead of pretending
        the action succeeded."""
        app, d = _app_and_dir(tmp_path)
        sub = os.path.join(d, "sub")
        os.makedirs(sub)
        _force_launcher(monkeypatch)
        bad = MagicMock()
        bad.run.return_value = subprocess.CompletedProcess(args=[], returncode=1)
        with app.test_client() as c, patch("bpp.web.bp_os_integration.subprocess", bad):
            resp = c.post("/api/v1/open-folder", json={"path": sub})
            assert resp.status_code == 502
            assert "exited with code 1" in resp.get_json()["error"]

    def test_missing_binary_returns_502(self, tmp_path, monkeypatch):
        """`shutil.which` returns None → don't even try to launch."""
        app, d = _app_and_dir(tmp_path)
        sub = os.path.join(d, "sub")
        os.makedirs(sub)
        _force_launcher(monkeypatch)
        with app.test_client() as c, patch("shutil.which", return_value=None):
            resp = c.post("/api/v1/open-folder", json={"path": sub})
            assert resp.status_code == 502
            assert "not found" in resp.get_json()["error"].lower()

    def test_launcher_timeout_returns_504(self, tmp_path, monkeypatch):
        from subprocess import TimeoutExpired

        app, d = _app_and_dir(tmp_path)
        sub = os.path.join(d, "sub")
        os.makedirs(sub)
        _force_launcher(monkeypatch)
        slow = MagicMock()
        slow.run.side_effect = TimeoutExpired(cmd="open", timeout=30)
        # Must keep TimeoutExpired as a real attribute for the except
        slow.TimeoutExpired = TimeoutExpired
        with app.test_client() as c, patch("bpp.web.bp_os_integration.subprocess", slow):
            resp = c.post("/api/v1/open-folder", json={"path": sub})
            assert resp.status_code == 504

    def test_launcher_timeout_threshold_is_generous(self):
        """R8-M3: the timeout must be ≥ 30s so slow disks / sleeping
        HDDs / busy GUI sessions don't produce spurious 504s on
        legitimate launcher operations. Locks the threshold so a
        future "minimize the timeout" change doesn't silently regress
        the UX it was bumped to fix."""
        from bpp.web.bp_os_integration import _LAUNCH_TIMEOUT_S

        assert _LAUNCH_TIMEOUT_S >= 30, (
            f"R8-M3: OS launcher timeout must be ≥ 30s "
            f"(was 5s, caused spurious 504s on slow-disk reveals). "
            f"Got {_LAUNCH_TIMEOUT_S}s."
        )


class TestOpenFolderPlatformGate:
    """Lock down platform-specific argv selection (L4 regression).

    Headless Linux (no DISPLAY) and unrecognized platforms must return
    None from `_open_folder_cmd` so the endpoint reports 501 instead
    of trying to spawn a non-existent or wrong-platform binary.
    """

    def test_macos_uses_open(self):
        from bpp.web.bp_os_integration import _open_folder_cmd

        with patch("bpp.web.bp_os_integration.sys") as mock_sys:
            mock_sys.platform = "darwin"
            assert _open_folder_cmd("/x") == ["open", "/x"]

    def test_windows_uses_explorer(self):
        from bpp.web.bp_os_integration import _open_folder_cmd

        with patch("bpp.web.bp_os_integration.sys") as mock_sys:
            mock_sys.platform = "win32"
            assert _open_folder_cmd("/x") == ["explorer", "/x"]

    def test_linux_with_display_uses_xdg_open(self):
        from bpp.web.bp_os_integration import _open_folder_cmd

        with (
            patch("bpp.web.bp_os_integration.sys") as mock_sys,
            patch.dict(os.environ, {"DISPLAY": ":0"}, clear=False),
        ):
            mock_sys.platform = "linux"
            assert _open_folder_cmd("/x") == ["xdg-open", "/x"]

    def test_headless_linux_returns_none(self):
        """No DISPLAY → no GUI launcher. Endpoint must surface 501."""
        from bpp.web.bp_os_integration import _open_folder_cmd

        env = {k: v for k, v in os.environ.items() if k != "DISPLAY"}
        with (
            patch("bpp.web.bp_os_integration.sys") as mock_sys,
            patch.dict(os.environ, env, clear=True),
        ):
            mock_sys.platform = "linux"
            assert _open_folder_cmd("/x") is None

    def test_unknown_platform_returns_none(self):
        from bpp.web.bp_os_integration import _open_folder_cmd

        with patch("bpp.web.bp_os_integration.sys") as mock_sys:
            mock_sys.platform = "freebsd"
            assert _open_folder_cmd("/x") is None

    def test_endpoint_returns_501_when_no_launcher(self, tmp_path):
        app, d = _app_and_dir(tmp_path)
        sub = os.path.join(d, "sub")
        os.makedirs(sub)
        with (
            app.test_client() as c,
            patch("bpp.web.bp_os_integration._open_folder_cmd", return_value=None),
        ):
            resp = c.post("/api/v1/open-folder", json={"path": sub})
            assert resp.status_code == 501
            assert "platform" in resp.get_json()["error"].lower()

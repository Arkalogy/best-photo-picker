"""Source-scan: Tauri failure screens must expose actionable buttons.

The "Server failed to start" (boot timeout) and "Server could not
restart" (respawn exhausted) overlays are injected as raw HTML strings
via window.eval() in src-tauri/src/main.rs. There's no webview in CI
to click them, so we pin the required button labels + the exit_app
invocation here. A refactor that drops one of the three buttons will
fail this test rather than silently shipping a dead-end error screen.
"""

from __future__ import annotations

from pathlib import Path

MAIN_RS = Path(__file__).resolve().parent.parent / "desktop" / "src-tauri" / "src" / "main.rs"


def _source() -> str:
    return MAIN_RS.read_text(encoding="utf-8")


class TestServerFailedToStartScreen:
    """Boot-timeout screen (wait_for_server returns false on first launch)."""

    @staticmethod
    def _block() -> str:
        src = _source()
        # Anchor on the eprintln that immediately follows the failed wait — the
        # function *definition* of wait_for_server appears much earlier and
        # must not be used as the search start.
        start = src.find('eprintln!("Server failed to start within 30 seconds")')
        assert start != -1, "Anchor line not found in main.rs"
        return src[start : start + 3000]

    def test_has_quit_button(self):
        assert "Quit" in self._block(), (
            "Server-failed-to-start screen must have a Quit button. "
            "Without it the user is stuck with a dead app they can't exit."
        )

    def test_has_try_again_button(self):
        assert "Try Again" in self._block(), (
            "Server-failed-to-start screen must have a Try Again button so the "
            "user can retry without relaunching from Finder."
        )

    def test_has_report_issue_button(self):
        assert "Report Issue" in self._block(), (
            "Server-failed-to-start screen must have a Report Issue button."
        )

    def test_quit_calls_exit_app(self):
        assert "exit_app" in self._block(), (
            "Quit button must invoke the registered exit_app Tauri command. "
            "A bare location.reload() or missing invoke silently does nothing."
        )


class TestServerCouldNotRestartScreen:
    """Fatal screen shown by show_fatal_error() after respawn attempts exhausted."""

    def test_has_quit_button(self):
        src = _source()
        start = src.find("show_fatal_error")
        block = src[start : start + 2000]
        assert "Quit" in block

    def test_has_try_again_button(self):
        src = _source()
        start = src.find("show_fatal_error")
        block = src[start : start + 2000]
        assert "Try Again" in block, (
            "Fatal-error screen must offer Try Again — reloading the webview "
            "re-triggers the boot sequence and may recover after a transient failure."
        )

    def test_has_report_issue_button(self):
        src = _source()
        start = src.find("show_fatal_error")
        block = src[start : start + 2000]
        assert "Report Issue" in block

    def test_quit_calls_exit_app(self):
        src = _source()
        start = src.find("show_fatal_error")
        block = src[start : start + 2000]
        assert "exit_app" in block

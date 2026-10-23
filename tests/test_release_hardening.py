"""Release pipeline hardening: the GitHub token goes only to
api.github.com (never to a look-alike host or across a redirect), the
build job holds no write token, every action is pinned to a commit, and
the release can't silently ship without the copyleft sources or with
unknown licenses."""

from __future__ import annotations

import importlib.util
import re
import sys
import urllib.request
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
WORKFLOWS = ROOT / ".github" / "workflows"


def _load(name: str):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


src = _load("third_party_sources")
inv = _load("third_party_inventory")


# ── token goes only to api.github.com ────────────────────────────────


@pytest.mark.parametrize(
    ("url", "gets_token"),
    [
        ("https://api.github.com/repos/letmaik/rawpy/contents/external", True),
        ("https://api.github.com.evil.example/steal", False),
        ("https://evil.example/https://api.github.com/x", False),
        ("http://api.github.com/repos/x", False),
        ("https://raw.githubusercontent.com/letmaik/rawpy/v1/.gitmodules", False),
        ("https://user@api.github.com.evil.example/x", False),
        ("https://user@api.github.com/x", False),
    ],
)
def test_the_token_goes_only_to_api_github_com(monkeypatch, url, gets_token):
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token")
    sent: list = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"ok"

    class _Opener:
        def open(self, req, timeout=None):
            sent.append(req)
            return _Resp()

    monkeypatch.setattr(src, "_opener", lambda: _Opener())
    assert src._get(url) == b"ok"
    has_token = sent[0].get_header("Authorization") == "Bearer secret-token"
    assert has_token is gets_token


def test_downloads_use_the_token_safe_redirect_handler():
    assert any(isinstance(h, src._SameHostRedirects) for h in src._opener().handlers)


def test_a_failed_source_fetch_is_a_clean_build_error(monkeypatch, tmp_path, capsys):
    import argparse

    # _cmd_sources imports third_party_sources at call time, so patch the
    # copy registered now: another test module (test_third_party_inventory)
    # loads the scripts too and may have replaced ours in sys.modules.
    live = sys.modules["third_party_sources"]

    def boom(dest):
        raise live.SourceFetchError("rawpy is not installed")

    monkeypatch.setattr(live, "fetch_all", boom)
    rc = inv._cmd_sources(argparse.Namespace(out=tmp_path))
    assert rc == 1
    assert "::error::" in capsys.readouterr().out


def test_a_redirect_to_another_host_drops_the_token():
    handler = src._SameHostRedirects()
    req = urllib.request.Request(
        "https://api.github.com/x", headers={"Authorization": "Bearer secret-token"}
    )
    away = handler.redirect_request(req, None, 302, "Found", {}, "https://evil.example/y")
    assert away is not None and away.get_header("Authorization") is None
    same = handler.redirect_request(req, None, 302, "Found", {}, "https://api.github.com/z")
    assert same.get_header("Authorization") == "Bearer secret-token"


# ── missing sources / unknown licenses are loud ──────────────────────


@pytest.mark.parametrize(
    ("plan", "module"), [("pillow_heif_plan", "pillow_heif"), ("rawpy_plan", "rawpy")]
)
def test_a_missing_copyleft_library_stops_the_source_fetch(monkeypatch, plan, module):
    monkeypatch.setitem(sys.modules, module, None)  # import fails
    with pytest.raises(src.SourceFetchError, match="not installed"):
        getattr(src, plan)()


def test_an_empty_source_plan_is_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(src, "pillow_heif_plan", lambda: [])
    monkeypatch.setattr(src, "rawpy_plan", lambda: [])
    with pytest.raises(src.SourceFetchError, match="nothing to fetch"):
        src.fetch_all(tmp_path)


def test_unknown_licenses_are_flagged_in_the_build_log(monkeypatch, tmp_path, capsys):
    import argparse

    fake = {
        "distributions": [
            {"name": "mystery", "license": "UNKNOWN (see https://x)", "native_libraries": []},
            {"name": "fine", "license": "MIT", "native_libraries": []},
        ],
        "python_runtime": {"version": "3.11"},
        "missing_requirements": [],
    }
    monkeypatch.setattr(inv, "build_inventory", lambda **kw: fake)
    monkeypatch.setattr(inv, "_save", lambda out, data: None)
    inv._cmd_inventory(argparse.Namespace(out=tmp_path, extras="web", root="bppicker"))
    out = capsys.readouterr().out
    assert "::warning::" in out and "mystery" in out and "UNKNOWN" in out
    assert "fine" not in out.split("::warning::", 1)[1]


# ── workflow permissions and pinning ─────────────────────────────────


def _workflow(name: str) -> dict:
    path = WORKFLOWS / name
    if not path.is_file():  # the sdist ships tests/ but not .github/
        pytest.skip(f"{path} not present")
    return yaml.safe_load(path.read_text())


def _steps(job: dict) -> list[dict]:
    return job.get("steps") or []


def test_the_release_build_job_has_no_write_token():
    wf = _workflow("release-desktop.yml")
    assert wf["permissions"] == {"contents": "read"}
    build = wf["jobs"]["build-macos-app"]
    assert build.get("permissions", {"contents": "read"}) == {"contents": "read"}
    for step in _steps(build):
        # As a command (a step summary may tell a human to use it).
        assert not re.search(r"^\s*gh release upload", step.get("run", ""), re.M), step.get("name")


def test_only_a_small_upload_job_can_write_to_the_release():
    wf = _workflow("release-desktop.yml")
    writers = {
        name: job
        for name, job in wf["jobs"].items()
        if (job.get("permissions") or {}).get("contents") == "write"
    }
    assert list(writers) == ["upload-release"]
    job = writers["upload-release"]
    assert job["needs"] == "build-macos-app"
    assert "release" in job["if"]
    runs = " ".join(s.get("run", "") for s in _steps(job))
    uses = " ".join(s.get("uses", "") for s in _steps(job))
    assert "gh release upload" in runs
    # It runs no third-party build code: no checkout, installs or builds.
    assert "actions/checkout" not in uses
    for word in ("pip install", "npm ", "pyinstaller", "tauri"):
        assert word not in runs


@pytest.mark.parametrize("path", sorted(WORKFLOWS.glob("*.yml")), ids=lambda p: p.name)
def test_every_action_is_pinned_to_a_commit_and_checkout_keeps_no_token(path):
    wf = yaml.safe_load(path.read_text())
    for job in (wf.get("jobs") or {}).values():
        for step in _steps(job):
            uses = step.get("uses")
            if not uses or uses.startswith("./"):
                continue
            assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", uses), f"{path.name}: {uses}"
            if uses.startswith("actions/checkout@"):
                assert (step.get("with") or {}).get("persist-credentials") is False, path.name

"""Security review follow-ups for the 0.1.3 release.

The loopback index page carries the owner token; with a boot secret set
only the caller that presents it (the shell's first navigation, then
the cookie that set) gets it. Paired phones never see storage error
text or the layout of volumes outside the library. The desktop shell
proves the port is ours before navigating, and opens links through a
command it owns.
"""

from __future__ import annotations

import os
import re
import tarfile
from pathlib import Path

import pytest

from bpp.web.bp_core import BOOT_COOKIE_NAME

ROOT = Path(__file__).resolve().parent.parent
LAN = {"REMOTE_ADDR": "192.168.1.5"}


@pytest.fixture
def locked_app(tmp_path):
    from bpp.web.app import create_app

    lib = str(tmp_path / "library")
    os.makedirs(lib)
    app = create_app(library_path=lib)
    app.config["TESTING"] = False
    app.extensions["bpp"].boot_secret = "boot-secret-123"
    return app


class TestLoopbackBootSecret:
    def test_index_without_the_secret_is_locked_and_carries_no_token(self, locked_app):
        ctx = locked_app.extensions["bpp"]
        with locked_app.test_client() as c:
            resp = c.get("/")
        assert resp.status_code == 403
        body = resp.get_data(as_text=True)
        assert ctx.auth_token not in body
        assert "auth-token" not in body
        assert BOOT_COOKIE_NAME not in resp.headers.get("Set-Cookie", "")

    def test_index_with_the_secret_renders_the_token_and_sets_the_cookie(self, locked_app):
        ctx = locked_app.extensions["bpp"]
        with locked_app.test_client() as c:
            resp = c.get("/?_boot=boot-secret-123")
        assert resp.status_code == 200
        assert ctx.auth_token in resp.get_data(as_text=True)
        cookie = resp.headers.get("Set-Cookie", "")
        assert cookie.startswith(f"{BOOT_COOKIE_NAME}=boot-secret-123")
        assert "HttpOnly" in cookie and "SameSite=Strict" in cookie

    def test_index_with_the_cookie_renders_the_token(self, locked_app):
        ctx = locked_app.extensions["bpp"]
        with locked_app.test_client() as c:
            c.set_cookie(BOOT_COOKIE_NAME, "boot-secret-123")
            resp = c.get("/")
        assert resp.status_code == 200
        assert ctx.auth_token in resp.get_data(as_text=True)

    def test_a_wrong_secret_is_locked(self, locked_app):
        with locked_app.test_client() as c:
            assert c.get("/?_boot=nope").status_code == 403
            c.set_cookie(BOOT_COOKIE_NAME, "nope")
            assert c.get("/").status_code == 403

    def test_no_secret_configured_keeps_the_open_loopback(self, locked_app):
        ctx = locked_app.extensions["bpp"]
        ctx.boot_secret = None
        with locked_app.test_client() as c:
            resp = c.get("/")
        assert resp.status_code == 200
        assert ctx.auth_token in resp.get_data(as_text=True)
        assert BOOT_COOKIE_NAME not in resp.headers.get("Set-Cookie", "")

    def test_boot_check_answers_only_the_right_secret(self, locked_app):
        with locked_app.test_client() as c:
            assert c.get("/boot-check?_boot=boot-secret-123").status_code == 204
            assert c.get("/boot-check?_boot=nope").status_code == 403
            assert c.get("/boot-check").status_code == 403
        locked_app.extensions["bpp"].boot_secret = None
        with locked_app.test_client() as c:
            assert c.get("/boot-check").status_code == 204


class TestServeWiring:
    def test_serve_makes_a_secret_and_tokenizes_the_url(self, monkeypatch):
        import argparse

        from bpp.commands.serve import _configure_loopback_boot

        monkeypatch.delenv("BPP_BOOT_SECRET", raising=False)
        monkeypatch.delenv("BPP_OPEN_LOOPBACK", raising=False)

        class Ctx:
            boot_secret = None

        ctx = Ctx()
        url = _configure_loopback_boot(ctx, 5001, argparse.Namespace(open_loopback=False))
        assert ctx.boot_secret and len(ctx.boot_secret) >= 24
        assert url == f"http://127.0.0.1:5001/?_boot={ctx.boot_secret}"

    def test_shell_secret_and_opt_out(self, monkeypatch):
        import argparse

        from bpp.commands.serve import _configure_loopback_boot

        class Ctx:
            boot_secret = None

        monkeypatch.setenv("BPP_BOOT_SECRET", "from-shell")
        monkeypatch.delenv("BPP_OPEN_LOOPBACK", raising=False)
        ctx = Ctx()
        url = _configure_loopback_boot(ctx, 5001, argparse.Namespace())
        assert url.endswith("_boot=from-shell")
        assert ctx.boot_secret == "from-shell"
        monkeypatch.setenv("BPP_OPEN_LOOPBACK", "1")
        ctx = Ctx()
        assert _configure_loopback_boot(ctx, 5001, argparse.Namespace()) == "http://127.0.0.1:5001"
        assert ctx.boot_secret is None
        monkeypatch.delenv("BPP_OPEN_LOOPBACK")
        ctx = Ctx()
        _configure_loopback_boot(ctx, 5001, argparse.Namespace(open_loopback=True))
        assert ctx.boot_secret is None

    def test_cli_exposes_the_opt_out_and_e2e_uses_it(self):
        from bpp.cli import build_parser

        parser = build_parser()
        for cmd in ("serve", "web"):
            ns = parser.parse_args([cmd, "--open-loopback", "--no-browser"])
            assert ns.open_loopback is True, cmd
        assert "BPP_OPEN_LOOPBACK=1" in (ROOT / "scripts" / "run_e2e.sh").read_text()


class TestBootKeyStaysOutOfTheLog:
    def test_serve_prints_a_generated_key_and_logs_none(self, monkeypatch, capsys, caplog):
        import argparse
        import logging

        from bpp.commands.serve import _configure_loopback_boot

        monkeypatch.delenv("BPP_BOOT_SECRET", raising=False)
        monkeypatch.delenv("BPP_OPEN_LOOPBACK", raising=False)

        class Ctx:
            boot_secret = None

        ctx = Ctx()
        with caplog.at_level(logging.DEBUG):
            _configure_loopback_boot(ctx, 5001, argparse.Namespace())
        assert ctx.boot_secret in capsys.readouterr().out
        assert ctx.boot_secret not in caplog.text
        monkeypatch.setenv("BPP_BOOT_SECRET", "from-shell")
        with caplog.at_level(logging.DEBUG):
            _configure_loopback_boot(Ctx(), 5001, argparse.Namespace())
        assert "from-shell" not in capsys.readouterr().out
        assert "from-shell" not in caplog.text

    def test_access_log_lines_lose_the_key(self):
        from bpp.utils.logging import RedactingFormatter

        fmt = RedactingFormatter("%(message)s")
        import logging

        rec = logging.LogRecord(
            "werkzeug",
            logging.INFO,
            "",
            0,
            '127.0.0.1 - - [d] "GET /boot-check?_boot=k3y-v4lue HTTP/1.1" 204 -',
            (),
            None,
        )
        out = fmt.format(rec)
        assert "k3y-v4lue" not in out and "_boot=[REDACTED]" in out


class TestDeviceRedactionLeavesRoutesAlone:
    def test_app_urls_are_not_paths(self, tmp_path):
        from bpp.web.lan_redaction import path_rewriter

        rw = path_rewriter(str(tmp_path / "lib"), home=str(tmp_path / "home"))
        assert rw("/api/v1/photos") == "/api/v1/photos"
        assert rw("/static/img/x.svg") == "/static/img/x.svg"
        assert rw("/Volumes/nas/y.jpg").startswith("@ext/")


class TestStorageHealthForDevices:
    def test_a_paired_phone_never_sees_the_error_text(self, tmp_path, monkeypatch):
        from bpp.web import bp_health
        from bpp.web.app import create_app
        from bpp.web.share import get_share_token, set_lan_sharing_enabled
        from bpp.web.share_devices import approve_device, find_or_create_pending_device

        lib = str(tmp_path / "library")
        os.makedirs(lib)
        app = create_app(library_path=lib)
        app.config["TESTING"] = False
        ctx = app.extensions["bpp"]
        with app.app_context():
            conn = ctx.get_conn()
            set_lan_sharing_enabled(conn, True)
            token = get_share_token(conn)
            row = find_or_create_pending_device(
                conn, fingerprint="fp-1", name="Phone", ip="192.168.1.5"
            )
            assert approve_device(conn, int(row["id"]))
        monkeypatch.setattr(
            bp_health,
            "check_storage_accessible",
            lambda path: {
                "accessible": False,
                "error": f"[Errno 2] No such file or directory: '{path}/x'",
                "latency_ms": 3,
            },
        )
        with app.test_client() as c:
            c.set_cookie("bpp_share_fp", "fp-1")
            phone = c.get(
                "/api/v1/health/storage", headers={"X-Auth-Token": token}, environ_overrides=LAN
            )
        with app.test_client() as c:
            owner = c.get("/api/v1/health/storage", headers={"X-Auth-Token": ctx.auth_token})
        assert phone.status_code == 200
        assert phone.get_json() == {
            "accessible": False,
            "error": "storage unreachable",
            "latency_ms": 3,
        }
        assert lib in owner.get_json()["error"]


class TestArchiveFilter:
    def test_tar_data_filter_is_used_wherever_python_ships_it(self):
        src = (ROOT / "bpp" / "web" / "analyze_archive.py").read_text()
        assert 'hasattr(tarfile, "data_filter")' in src
        assert "sys.version_info >= (3, 12)" not in src
        # The interpreter running the suite (3.11.4+) has it: the guarded
        # branch is live here, not only on 3.12.
        assert hasattr(tarfile, "data_filter")


class TestRepoHardening:
    @pytest.mark.parametrize(
        "name", ["ci", "e2e", "docker", "randomized", "arm64", "pages", "release-desktop"]
    )
    def test_every_workflow_states_its_token_permissions(self, name):
        text = (ROOT / ".github" / "workflows" / f"{name}.yml").read_text()
        assert re.search(r"^permissions:", text, re.M), name

    def test_publish_workflow_scopes_permissions_per_job(self):
        text = (ROOT / ".github" / "workflows" / "publish.yml").read_text()
        jobs = text.split("\njobs:\n", 1)[1]
        assert "    permissions:" in jobs

    def test_desktop_shell_boots_the_sidecar_with_a_secret_and_proves_the_port(self):
        src = (ROOT / "desktop" / "src-tauri" / "src" / "main.rs").read_text()
        assert '.env("BPP_BOOT_SECRET", boot_secret())' in src
        assert "fn server_knows_boot_secret" in src
        assert "/boot-check?_boot=" in src
        assert 'format!("http://127.0.0.1:{}/?_boot={}", port, boot_secret())' in src
        assert "http://localhost:" not in src, "every navigation carries the key on 127.0.0.1"
        assert src.count("/?_boot={}") == 2, "first load and the respawn reload"
        conf = (ROOT / "desktop" / "src-tauri" / "tauri.conf.json").read_text()
        assert '"devUrl": "http://127.0.0.1:5001"' in conf
        assert "--no-browser --open-loopback" in conf, "tauri dev starts its own server"

    def test_desktop_shell_opens_links_through_its_own_command(self):
        src = (ROOT / "desktop" / "src-tauri" / "src" / "main.rs").read_text()
        assert "opener?.openUrl" not in src
        assert "fn open_url(" in src
        assert 'url.starts_with("https://github.com/Arkalogy/")' in src
        assert "generate_handler![set_app_theme, exit_app, open_url]" in src
        # Every button that used the dead opener now invokes the command,
        # with the quotes escaped: the markup sits inside a JS
        # single-quoted string in all three overlays.
        bs = "\\"  # one backslash
        raw_site = "invoke(" + bs + "'open_url" + bs + "'"  # inside a raw string
        concat_sites = "invoke(" + bs * 2 + "'open_url" + bs * 2 + "'"  # inside "…"
        assert src.count(raw_site) == 1 and src.count(concat_sites) == 2
        assert src.count("invoke('open_url'") == 0
        assert "getrandom::getrandom(&mut buf)" in src
        assert 'getrandom = "0.2"' in (ROOT / "desktop" / "src-tauri" / "Cargo.toml").read_text()
        conf = (ROOT / "desktop" / "src-tauri" / "tauri.conf.json").read_text()
        assert "http://127.0.0.1:5001" in conf

    def test_pair_page_button_is_wired_without_the_dispatcher(self):
        src = (ROOT / "bpp" / "web" / "templates" / "pair.html").read_text()
        assert 'data-action="_bppReload"' not in src
        assert "getElementById('pair-cta').addEventListener('click'" in src

    def test_docker_and_docs_follow_the_locked_index(self):
        assert '"--open-loopback"]' in (ROOT / "Dockerfile").read_text()
        assert "--open-loopback" in (ROOT / "docs" / "API.md").read_text()
        readme = (ROOT / "README.md").read_text()
        assert "Open http://127.0.0.1:5001 in your browser" not in readme
        assert "--open-loopback" in readme
        assert "Boot key" in (ROOT / "docs" / "security.md").read_text()

    def test_index_strips_the_boot_key_from_the_address_bar(self):
        src = (ROOT / "bpp" / "web" / "templates" / "index.html").read_text()
        assert 'q.indexOf("_boot=") !== -1' in src

    def test_publish_manifest_default_is_not_in_tmp(self):
        src = (ROOT / "scripts" / "publish_registry_manifest.py").read_text()
        assert '"/tmp/registry.json"' not in src
        assert 'default=Path("registry.json")' in src

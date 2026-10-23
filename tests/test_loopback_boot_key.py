"""Security review follow-ups for the 0.2.0 release.

The loopback index page carries the owner token; with a boot secret set
only the caller that presents it (the shell's first navigation, then
the page re-presenting it from sessionStorage) gets it. Paired phones never see storage error
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

ROOT = Path(__file__).resolve().parent.parent
LAN = {"REMOTE_ADDR": "192.168.1.5"}


def _shell_source() -> str:
    """Every Rust file of the desktop shell crate, in one string."""
    src_dir = ROOT / "desktop" / "src-tauri" / "src"
    return "\n".join(p.read_text() for p in sorted(src_dir.glob("*.rs")))


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
        assert "This link has expired" in body
        assert 'sessionStorage.getItem("bpp_boot")' in body, "a reload re-presents the key"
        assert "Set-Cookie" not in resp.headers

    def test_index_with_the_secret_renders_the_token_and_sets_no_cookie(self, locked_app):
        ctx = locked_app.extensions["bpp"]
        with locked_app.test_client() as c:
            resp = c.get("/?_boot=boot-secret-123")
        assert resp.status_code == 200
        assert ctx.auth_token in resp.get_data(as_text=True)
        # Cookies ignore the port: one would reach every other 127.0.0.1
        # listener. The page keeps the key in sessionStorage instead.
        assert "Set-Cookie" not in resp.headers

    def test_a_cookie_is_never_enough(self, locked_app):
        with locked_app.test_client() as c:
            c.set_cookie("bpp_boot", "boot-secret-123")
            assert c.get("/").status_code == 403

    def test_a_wrong_secret_is_locked(self, locked_app):
        with locked_app.test_client() as c:
            assert c.get("/?_boot=nope").status_code == 403

    def test_no_secret_configured_keeps_the_open_loopback(self, locked_app):
        ctx = locked_app.extensions["bpp"]
        ctx.boot_secret = None
        with locked_app.test_client() as c:
            resp = c.get("/")
        assert resp.status_code == 200
        assert ctx.auth_token in resp.get_data(as_text=True)

    def test_boot_check_proves_the_key_without_revealing_it(self, locked_app):
        import hmac

        with locked_app.test_client() as c:
            resp = c.get("/boot-check?nonce=abc123")
        assert resp.status_code == 204
        assert (
            resp.headers["X-Boot-Proof"]
            == hmac.new(b"boot-secret-123", b"abc123", "sha256").hexdigest()
        )
        assert "boot-secret-123" not in str(resp.headers)
        with locked_app.test_client() as c:
            assert "X-Boot-Proof" not in c.get("/boot-check").headers, "no nonce, no proof"
        locked_app.extensions["bpp"].boot_secret = None
        with locked_app.test_client() as c:
            resp = c.get("/boot-check?nonce=abc123")
        assert resp.status_code == 204 and "X-Boot-Proof" not in resp.headers


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
        for cmd in ("serve", "web", "demo"):
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
        # Nothing printed until Werkzeug reports the bind: a failed bind
        # must not print a link to a server that isn't running.
        assert ctx.boot_secret not in capsys.readouterr().out
        rec = logging.LogRecord(
            "werkzeug", logging.INFO, "", 0, " * Running on http://127.0.0.1:5001", (), None
        )
        werkzeug = logging.getLogger("werkzeug")
        assert werkzeug.filter(rec) is False, "the bare URL banner is dropped"
        out = capsys.readouterr().out
        assert ctx.boot_secret in out and "Open Best Photo Picker at" in out
        assert werkzeug.filter(rec) is False
        assert ctx.boot_secret not in capsys.readouterr().out, "printed once"
        assert ctx.boot_secret not in caplog.text
        monkeypatch.setenv("BPP_BOOT_SECRET", "from-shell")
        with caplog.at_level(logging.DEBUG):
            _configure_loopback_boot(Ctx(), 5001, argparse.Namespace())
        assert werkzeug.filter(rec) is False
        assert "from-shell" not in capsys.readouterr().out, "the launcher knows its key"
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


class TestDeviceRedactionHasNoRouteExemption:
    def test_route_shaped_photo_paths_are_hidden_too(self, tmp_path):
        """A photo under a folder named like one of the app's URLs is
        still a photo; the device gets a handle, never the path."""
        from bpp.web.lan_redaction import path_rewriter

        rw = path_rewriter(str(tmp_path / "lib"), home=str(tmp_path / "home"))
        for fs_path in ("/static/private-family/x.jpg", "/api/v1/private-family/x.jpg"):
            out = rw(fs_path)
            assert out.startswith("@ext/") and "private" not in out, (fs_path, out)
        assert rw("/Volumes/nas/y.jpg").startswith("@ext/")


class TestDemoUsesTheBootKey:
    def test_demo_wires_the_launch_url_through_the_boot_helper(self):
        src = (ROOT / "bpp" / "commands" / "demo.py").read_text()
        assert '_configure_loopback_boot(app.extensions["bpp"], port, args)' in src
        assert "args=[launch_url]" in src
        assert 'args=[f"http://127.0.0.1:{port}"]' not in src


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


class TestRawPixelCap:
    def test_a_raw_header_over_the_cap_is_refused_before_unpacking(self, monkeypatch):
        import sys
        import types

        from bpp.utils import raw

        unpacked = []

        class FakeRaw:
            def __init__(self):
                self.sizes = None

            def open_file(self, path):
                big = "huge" in path
                w, h = (20_000, 20_000) if big else (6000, 4000)
                self.sizes = types.SimpleNamespace(width=w, height=h)

            def unpack(self):
                unpacked.append(self.sizes.width)

            def postprocess(self, **kw):
                return "pixels"

            def close(self):
                pass

        monkeypatch.setitem(sys.modules, "rawpy", types.SimpleNamespace(RawPy=FakeRaw))
        monkeypatch.setattr(raw, "RAWPY_AVAILABLE", True)
        with pytest.raises(raw.RawTooLarge):
            raw.decode_raw_rgb("/x/huge.cr2")
        assert unpacked == [], "the frame is never unpacked past the cap"
        assert raw.decode_raw_rgb("/x/ok.cr2") == "pixels"
        assert unpacked == [6000]


class TestLayeringFollowUps:
    def test_the_journal_registrar_lives_with_the_disk_half(self):
        from bpp.web import permanent_delete

        assert hasattr(permanent_delete, "register_permanent_delete_recovery")
        src = (ROOT / "bpp" / "web" / "bp_photos_manage.py").read_text()
        assert "register_permanent_delete_recovery" not in src

    def test_worker_failures_keep_their_traceback(self, caplog):
        import logging

        from bpp.utils.concurrency import parallel_map

        def boom(x):
            raise KeyError("foo")

        with caplog.at_level(logging.WARNING):
            out = parallel_map(boom, [1], workers=1)
        assert out == [None]
        assert "KeyError" in caplog.text and "Traceback" in caplog.text

    def test_host_refusal_names_the_host(self, tmp_path):
        from bpp.web.app import create_app

        app = create_app(library_path=str(tmp_path / "lib"))
        app.config["TESTING"] = True
        with app.test_client() as c:
            resp = c.get("/api/v1/status", headers={"Host": "evil.example:5001"})
        assert resp.status_code == 421
        assert resp.get_json().get("context", {}).get("host") == "evil.example:5001"


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
        assert 'sessionStorage.setItem("bpp_boot"' in src

    def test_publish_manifest_default_is_not_in_tmp(self):
        src = (ROOT / "scripts" / "publish_registry_manifest.py").read_text()
        assert '"/tmp/registry.json"' not in src
        assert 'default=Path("registry.json")' in src


class TestLiveRegistryGate:
    def test_release_workflows_verify_the_live_manifest(self):
        assert (ROOT / "scripts" / "verify_live_registry.py").exists()
        for wf in ("publish", "release-desktop"):
            text = (ROOT / ".github" / "workflows" / f"{wf}.yml").read_text()
            assert "python scripts/verify_live_registry.py" in text, wf
            assert "if: github.event_name == 'release'" in text, wf

    def test_the_script_fails_closed_on_an_unversioned_manifest(self, monkeypatch, capsys):
        import importlib.util
        import json

        spec = importlib.util.spec_from_file_location(
            "verify_live_registry", ROOT / "scripts" / "verify_live_registry.py"
        )
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        from bpp.registry import remote_registry

        manifest = {"schema_version": 1, "entries": [], "signatures": []}
        monkeypatch.setattr(
            remote_registry, "fetch_remote_manifest", lambda url: json.dumps(manifest).encode()
        )
        assert mod.main() == 1
        out = capsys.readouterr().out
        assert "FAIL" in out and "manifest_version" in out
        monkeypatch.setattr(remote_registry, "fetch_remote_manifest", lambda url: None)
        assert mod.main() == 1
        assert "could not fetch" in capsys.readouterr().out

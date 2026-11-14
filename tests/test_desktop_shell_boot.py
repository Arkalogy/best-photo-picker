"""The desktop shell's boot and restart lifecycle, read from the Rust
sources: the sidecar gets a per-launch secret, the webview goes only to
a listener that proves it holds that secret, a crashed sidecar's port is
reclaimed only from an orphan, and links open through a command the
shell owns."""

from __future__ import annotations

from tests.test_loopback_boot_key import ROOT, _shell_source


class TestDesktopShellBoot:
    def test_desktop_shell_boots_the_sidecar_with_a_secret_and_proves_the_port(self):
        src = _shell_source()
        assert '.env("BPP_BOOT_SECRET", boot_secret())' in src
        assert "fn server_knows_boot_secret" in src
        assert "/boot-check?nonce=" in src, "challenge, not the secret, goes over the wire"
        assert (
            "?_boot=" not in src.split("fn server_knows_boot_secret")[1].split("fn fresh_nonce")[0]
        )
        assert (
            "fn hmac_sha256_hex" in src
            and 'sha2 = "0.10"' in (ROOT / "desktop" / "src-tauri" / "Cargo.toml").read_text()
        )
        # Both loads go to 127.0.0.1, the address the probe proved:
        # "localhost" may resolve to ::1, where another process could
        # listen. (The 0.1.2 origin was localhost; its localStorage resets.)
        assert "http://localhost:" not in src
        assert src.count('"http://127.0.0.1:{}/?_boot={}"') == 1
        assert src.count("'http://127.0.0.1:{}/?_boot={}'") == 1
        conf = (ROOT / "desktop" / "src-tauri" / "tauri.conf.json").read_text()
        assert '"devUrl": "http://127.0.0.1:5001"' in conf
        assert "--no-browser --open-loopback" in conf, "tauri dev starts its own server"
        # A final failure ends the wait early, and a held port is never
        # respawned over.
        assert "startup_failure.load(Ordering::SeqCst) != STARTUP_OK" in src
        assert src.count("== STARTUP_PORT_IN_USE") >= 2

    def test_desktop_shell_reclaims_an_orphaned_sidecar_and_names_a_held_port(self):
        src = _shell_source()
        assert "fn reclaim_port_from_stale_sidecar" in src
        assert "fn listener_pid" in src and "fn process_name" in src
        assert 'ends_with("bpp-server")' in src, "only our own binary, only the pid on the port"
        assert "pkill" not in src, "never a global kill: another instance's sidecar is not ours"
        assert "reclaim_port_from_stale_sidecar(port);" in src
        assert "STARTUP_PORT_IN_USE" in src and "Address already in use" in src
        assert "Another copy is already running" in src
        assert "inv('restart_app')" in src and "fn restart_app" in src
        assert "generate_handler![set_app_theme, exit_app, restart_app, open_url]" in src

    def test_desktop_shell_opens_links_through_its_own_command(self):
        src = _shell_source()
        assert "opener?.openUrl" not in src
        assert "fn open_url(" in src
        assert 'url.starts_with("https://github.com/Arkalogy/")' in src
        assert "generate_handler![set_app_theme, exit_app, restart_app, open_url]" in src
        # Every button that used the dead opener now invokes the command
        # through a listener: every screen is drawn into the loaded page
        # (even the whole-page ones replace its body), and that page's
        # CSP has no unsafe-inline, so an inline handler is inert.
        assert "onclick=" not in src
        assert src.count("inv('open_url',{url:'https://github.com/Arkalogy/") >= 2
        assert src.count("invoke('open_url'") == 0
        assert "getrandom::getrandom(&mut buf)" in src
        assert 'getrandom = "0.2"' in (ROOT / "desktop" / "src-tauri" / "Cargo.toml").read_text()
        conf = (ROOT / "desktop" / "src-tauri" / "tauri.conf.json").read_text()
        assert "http://127.0.0.1:5001" in conf


class TestDesktopShellRestartLifecycle:
    def test_a_reload_after_a_respawn_needs_the_proof_not_just_a_listener(self):
        """A crashed sidecar's port can be taken by any local process
        before the respawn; the shell must not hand it the key."""
        src = _shell_source()
        reload_at = src.index("Server respawned successfully")
        start = src.rfind("loop {", 0, reload_at)
        monitor = src[start : src.index("consecutive_failures = 0;", reload_at)]
        assert "server_knows_boot_secret(port)" in monitor, (
            "the reload branch navigates with the key on a bare TCP connect"
        )
        # One read of the flag per tick, and the proof sits between that
        # read and the navigation, so a termination that flips the flag
        # between two reads cannot route the key past the proof.
        assert monitor.count("respawning_nav.load(") == 1
        assert "respawning_nav.swap(" not in monitor
        branch = monitor.split("respawning_nav.load(")[1]
        assert branch.index("server_knows_boot_secret(port)") < branch.index("?_boot=")

    def test_reclaim_kills_only_a_sidecar_whose_shell_is_gone(self):
        """Another running copy's live sidecar also answers the probe with
        a different secret; only a sidecar reparented to launchd (its shell
        crashed or was force-quit) is an orphan."""
        src = _shell_source()
        reclaim = src.split("fn reclaim_port_from_stale_sidecar")[1].split("\n}\n")[0]
        assert "sidecar_is_orphaned(pid)" in reclaim
        # The one-file build's bootloader sits between the listener and
        # its shell: the walk climbs past ancestors named bpp-server and
        # asks whether the first other ancestor is launchd.
        orphan = src.split("fn sidecar_is_orphaned")[1].split("\n}\n")[0]
        assert "parent_pid(" in orphan and "== 1" in orphan
        assert 'ends_with("bpp-server")' in orphan

    def test_a_foreign_listener_after_a_crash_ends_on_the_held_port_screen(self):
        src = _shell_source()
        monitor = src.split("Server respawned successfully")[0]
        monitor = monitor[monitor.rfind("loop {") :]
        assert "foreign_ticks" in monitor and "show_start_failure(&handle_nav, true)" in monitor


def test_the_signed_app_may_load_its_own_python():
    """Under the hardened runtime the server needs these entitlements or it
    can't load its Python (the 0.2.0 Mac build did not start). The bundler
    applies the file to the app, PyInstaller to the server helper app, and
    the release runs the signed server as installed from the DMG before
    anything is uploaded or notarized."""
    import json
    import plistlib

    conf = json.loads((ROOT / "desktop" / "src-tauri" / "tauri.conf.json").read_text())
    name = conf["bundle"]["macOS"]["entitlements"]
    plist = plistlib.loads((ROOT / "desktop" / "src-tauri" / name).read_bytes())
    assert plist["com.apple.security.cs.disable-library-validation"] is True
    assert plist["com.apple.security.cs.allow-unsigned-executable-memory"] is True
    spec = (ROOT / "desktop" / "bpp-server.spec").read_text()
    assert f'ENTITLEMENTS = str(DESKTOP / "src-tauri" / "{name}")' in spec
    workflow = (ROOT / ".github" / "workflows" / "release-desktop.yml").read_text()
    gate = workflow.index("Verify the signed server starts fast and loads its libraries")
    assert gate < workflow.index("Notarize and staple the DMG")
    step = workflow[gate : workflow.index("Notarize and staple the DMG")]
    assert 'server="$app/Contents/Helpers/bpp-server.app/Contents/MacOS/bpp-server"' in step
    assert 'codesign --verify --deep --strict --verbose=2 "$signed"' in step
    assert 'then signed="$app"; else signed="$app/Contents/Helpers/bpp-server.app"; fi' in step
    assert '"$server" --version' in step and "alarm 120" in step
    assert '"$server" selfcheck --require-all' in step
    assert "serve --library" in step and "curl -sf" in step
    assert '[ "$first" -gt 60 ]' in step and '[ "$second" -gt 10 ]' in step
    # timed offline (a slow model registry would fail a good build), every
    # wait bounded
    assert 'BPP_DISABLE_REMOTE_REGISTRY=1 "$server" serve' in step
    assert "curl -sf --max-time 2" in step and 'kill -9 "$pid"' in step
    assert "hdiutil detach" in step


class TestServerHelperApp:
    """The server ships as a one-folder build in a background-only helper
    app, so nothing is unpacked at launch (0.2.3's one-file server took
    30-45 s to start and the app gave up after 30)."""

    def test_the_spec_builds_a_one_folder_background_helper_app(self):
        spec = (ROOT / "desktop" / "bpp-server.spec").read_text()
        assert "exclude_binaries=True" in spec and "COLLECT(" in spec
        assert 'name="bpp-server.app"' in spec and "BUNDLE(" in spec
        assert '"LSBackgroundOnly": True' in spec and "console=True" in spec
        assert 'bundle_identifier="com.arkalogy.bpp.server"' in spec
        # the name boot.rs matches when it reclaims the port from an orphan
        assert 'name="bpp-server",' in spec
        assert "upx=True" not in spec
        assert 'IDENTITY = os.environ.get("BPP_CODESIGN_IDENTITY") or None' in spec
        assert "codesign_identity=IDENTITY" in spec

    def test_the_app_carries_and_launches_the_helper(self):
        import json

        conf = json.loads((ROOT / "desktop" / "src-tauri" / "tauri.conf.json").read_text())
        assert "externalBin" not in conf["bundle"]
        assert conf["bundle"]["macOS"]["files"] == {
            "Helpers/bpp-server.app": "helpers/bpp-server.app"
        }
        script = (ROOT / "desktop" / "build-sidecar.sh").read_text()
        assert 'HELPERS_DIR="$SCRIPT_DIR/src-tauri/helpers"' in script
        assert 'ditto "dist/bpp-server.app" "$HELPERS_DIR/bpp-server.app"' in script
        assert 'codesign --verify --deep --strict "$HELPERS_DIR/bpp-server.app"' in script
        src = _shell_source()
        assert (
            'SERVER_EXECUTABLE: &str = "../Helpers/bpp-server.app/Contents/MacOS/bpp-server"' in src
        )
        assert ".sidecar(SERVER_EXECUTABLE)" in src
        assert '.sidecar("bpp-server")' not in src

    def test_one_start_wait_drives_the_wait_the_log_and_the_screen(self):
        src = _shell_source()
        assert "pub(crate) const SERVER_START_TIMEOUT_SECS: u64 = 120;" in src
        assert 'pub(crate) const SERVER_START_TIMEOUT_TEXT: &str = "2 minutes";' in src
        assert "wait_for_server(port, SERVER_START_TIMEOUT_SECS, &startup_failure_nav)" in src
        assert "crate::boot::SERVER_START_TIMEOUT_TEXT" in src
        assert "30 seconds" not in src and "wait_for_server(port, 30" not in src
        splash = (ROOT / "desktop" / "src" / "index.html").read_text()
        assert "Still starting." in splash and "}, 8000);" in splash
        # no second copy of the wait in the splash text to drift from boot.rs
        note = splash.split('id="status-note">')[1].split("</div>")[0]
        assert not any(ch.isdigit() for ch in note), note

    def test_try_again_stops_the_server_before_the_app_restarts(self):
        """restart() on the main thread (where a sync command runs) skips
        the exit event, so the old server kept running; request_restart()
        goes through it, and the exit handler stops the server."""
        src = _shell_source()
        restart = src.split("fn restart_app(app: AppHandle) {")[1].split("}")[0]
        assert "app.request_restart();" in restart
        assert "app.restart()" not in restart
        assert "RunEvent::ExitRequested { .. } | RunEvent::Exit => stop_server_on_exit(" in src
        stop = src.split("fn stop_server_on_exit")[1].split("\n}\n")[0]
        assert "stop_child(child, port" in stop and "generation.fetch_add" in stop

    def test_a_macos_quit_stops_the_server(self):
        """⌘Q, the app menu's Quit, the Dock and logout end the app through
        applicationWillTerminate, which Tauri reports only as Exit, never
        ExitRequested: handling ExitRequested alone left the server running
        after the app was gone."""
        src = _shell_source()
        assert "RunEvent::Exit => stop_server_on_exit(app_handle)" in src

    def test_a_stop_waits_for_the_server_to_exit_not_only_the_port(self):
        """The server closes its port before its exit hooks clear the
        library's serving lock; a replacement started on a free port alone
        refused the still-held lock (Try Again, Restart Server)."""
        src = _shell_source()
        stop = src.split("pub(crate) fn stop_child")[1].split("\n}\n")[0]
        assert "!process_alive(pid) && !crate::boot::port_is_held(port)" in stop
        assert "fn process_alive(pid: u32) -> bool" in src

    def test_a_stop_never_kills_another_copys_server(self):
        """When this app's server died on a port another running copy
        holds, its stop (Quit, Try Again) must not kill that copy's server:
        only a listener that is the child or the child's child is ours."""
        src = _shell_source()
        own = src.split("pub(crate) fn own_listener")[1].split("\n}\n")[0]
        assert "pid == child || parent_pid(pid) == Some(child)" in own
        stop = src.split("pub(crate) fn stop_child")[1].split("\n}\n")[0]
        # read before the child is killed, while the parent link holds
        assert stop.index("own_listener(port, pid)") < stop.index("child.kill()")
        assert "kill_sidecar_listener" not in src

    def test_restart_server_does_not_start_a_server_while_the_app_quits(self):
        src = _shell_source()
        restart = src.split("pub(crate) fn restart_server")[1].split("\n}\n")[0]
        assert "let my_generation = generation.fetch_add(1, Ordering::SeqCst) + 1;" in restart
        assert "generation.load(Ordering::SeqCst) != my_generation" in restart
        # the old child is stopped outside the lock the exit handler takes
        taken = restart.split("guard.child.take()")[1]
        assert taken.index("};") < taken.index("stop_child(child, port, 5)")

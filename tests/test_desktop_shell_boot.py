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
        bs = "\\"
        assert (
            "invoke(" + bs * 2 + "'restart_app" + bs * 2 + "')" in src and "fn restart_app" in src
        )
        assert "generate_handler![set_app_theme, exit_app, restart_app, open_url]" in src

    def test_desktop_shell_opens_links_through_its_own_command(self):
        src = _shell_source()
        assert "opener?.openUrl" not in src
        assert "fn open_url(" in src
        assert 'url.starts_with("https://github.com/Arkalogy/")' in src
        assert "generate_handler![set_app_theme, exit_app, restart_app, open_url]" in src
        # Every button that used the dead opener now invokes the command.
        # The overlays injected into the loaded page bind listeners (its
        # CSP has no unsafe-inline); the two whole-page screens, drawn
        # before any page loaded, keep their escaped inline form.
        bs = "\\"  # one backslash
        concat_sites = "invoke(" + bs * 2 + "'open_url" + bs * 2 + "'"  # inside "…"
        assert src.count(concat_sites) == 2
        assert "inv('open_url',{url:'https://github.com/Arkalogy/" in src
        assert src.count("invoke('open_url'") == 0
        overlays = src.split("fn show_update_required")[0]
        assert "onclick=" not in overlays.split("fn show_reconnecting_overlay")[1]
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

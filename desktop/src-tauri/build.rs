fn main() {
    // Generate the allow-<command> permissions for the shell's own commands,
    // so the capability can grant them to the page served by the sidecar.
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&["set_app_theme", "exit_app", "restart_app", "open_url"]),
    ))
    .expect("failed to run tauri-build");
}

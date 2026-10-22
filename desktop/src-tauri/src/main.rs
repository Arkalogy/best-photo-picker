// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU8, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tauri::{
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    AppHandle, Manager, RunEvent, Theme, WindowEvent,
};
use tauri_plugin_shell::ShellExt;

mod boot;
mod menu;
mod screens;
mod sidecar;

use boot::{
    boot_secret, port_is_held, reclaim_port_from_stale_sidecar, server_is_alive, wait_for_server,
};
use menu::install_menu;
use screens::{hide_reconnecting_overlay, show_reconnecting_overlay, show_start_failure, show_update_required};
use sidecar::{
    spawn_sidecar, RestartState, ServerState, STARTUP_NEWER_SCHEMA, STARTUP_OK, STARTUP_PORT_IN_USE,
};

#[tauri::command]
fn set_app_theme(window: tauri::WebviewWindow, theme: String) {
    let t = match theme.as_str() {
        "light" => Some(Theme::Light),
        "dark" => Some(Theme::Dark),
        _ => None,
    };
    let _ = window.set_theme(t);
}

// Native exit_app command used by fatal-error and disconnect overlays. The injected "Quit" buttons in the
// disconnect / fatal-error overlays were calling
// `window.__TAURI__?.invoke('exit_app')` against a command that was
// never registered AND with the wrong call shape (Tauri v2 routes
// invoke through `__TAURI__.core.invoke`, not `__TAURI__.invoke`).
// Result: the buttons silently did nothing. Register the command and
// fix the call sites below so Quit actually quits.
#[tauri::command]
fn exit_app(app: AppHandle) {
    app.exit(0);
}

/// "Try Again" on a start-up failure screen: a page reload would only
/// redraw the splash (the wait thread has already given up), so start
/// the whole app over, orphan reclaim included.
#[tauri::command]
fn restart_app(app: AppHandle) {
    app.restart();
}

/// Open one of our own https links in the user's browser; the webview
/// can't reach the shell plugin directly (core:default only).
#[tauri::command]
fn open_url(app: AppHandle, url: String) -> Result<(), String> {
    if !url.starts_with("https://github.com/Arkalogy/") {
        return Err("refused".into());
    }
    app.shell().open(url, None).map_err(|e| e.to_string())
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![set_app_theme, exit_app, restart_app, open_url])
        .manage(Mutex::new(ServerState { child: None }))
        .setup(|app| {
            let handle = app.handle().clone();
            let port: u16 = 5001;

            // Shared state for respawn coordination
            let respawning = Arc::new(AtomicBool::new(false));
            let respawn_count = Arc::new(AtomicU32::new(0));
            // Diagnosis the sidecar stderr scanner writes when it spots a
            // known, non-retryable startup failure (e.g. newer-schema DB).
            let startup_failure = Arc::new(AtomicU8::new(STARTUP_OK));

            // Store restart state so menu handler can access it
            app.manage(RestartState {
                port,
                respawning: respawning.clone(),
                respawn_count: respawn_count.clone(),
                startup_failure: startup_failure.clone(),
            });

            // In release mode, spawn the sidecar binary.
            // In dev mode, beforeDevCommand starts the server.
            if cfg!(debug_assertions) {
                eprintln!("[dev] Server managed by beforeDevCommand on port {}", port);
            }

            // Reclaim the port, spawn the sidecar, wait for it, then
            // redirect and monitor — all off the main thread so the
            // splash paints at once.
            {
                let handle_nav = handle.clone();
                let respawning_nav = respawning.clone();
                let respawn_count_nav = respawn_count.clone();
                let startup_failure_nav = startup_failure.clone();
                std::thread::spawn(move || {
                    let splash_start = std::time::Instant::now();
                    let min_splash = Duration::from_millis(1500);

                    if !cfg!(debug_assertions) {
                        reclaim_port_from_stale_sidecar(port);
                        spawn_sidecar(
                            &handle_nav,
                            port,
                            respawning_nav.clone(),
                            respawn_count_nav.clone(),
                            startup_failure_nav.clone(),
                        );
                    }

                    if !wait_for_server(port, 30, &startup_failure_nav) {
                        // If the sidecar already told us WHY (newer-schema DB),
                        // show the actionable update screen instead of the
                        // generic "did not respond" timeout. The stderr scanner
                        // may land the diagnosis a beat after the TCP timeout,
                        // so give it a short grace window to settle.
                        if startup_failure_nav.load(Ordering::SeqCst) == STARTUP_OK {
                            std::thread::sleep(Duration::from_millis(500));
                        }
                        if startup_failure_nav.load(Ordering::SeqCst) == STARTUP_NEWER_SCHEMA {
                            eprintln!("Server refused to open a newer-schema DB — prompting to update");
                            show_update_required(&handle_nav);
                            return;
                        }
                        let port_held = startup_failure_nav.load(Ordering::SeqCst) == STARTUP_PORT_IN_USE
                            || port_is_held(port);
                        eprintln!(
                            "Server failed to start within 30 seconds (port held by another process: {})",
                            port_held
                        );
                        show_start_failure(&handle_nav, port_held);
                        return;
                    }

                    // Show splash for at least 1.5s
                    let elapsed = splash_start.elapsed();
                    if elapsed < min_splash {
                        std::thread::sleep(min_splash - elapsed);
                    }

                    // Redirect webview from splash screen to server
                    // 127.0.0.1, the address the probe proved: "localhost" may
                    // resolve to ::1, where the server does not listen and
                    // another process could. (0.1.2 used localhost, so the
                    // window's saved settings reset once on this upgrade.)
                    let url = format!("http://127.0.0.1:{}/?_boot={}", port, boot_secret());
                    if let Some(window) = handle_nav.get_webview_window("main") {
                        let _ = window.eval(&format!(
                            "window.location.replace('{}')",
                            url
                        ));
                    }

                    // Both modes: monitor server liveness
                    // Wait a bit for the app to fully initialize before monitoring
                    std::thread::sleep(Duration::from_secs(5));
                    let mut consecutive_failures = 0u32;
                    loop {
                        std::thread::sleep(Duration::from_secs(5));
                        if server_is_alive(port) {
                            if consecutive_failures > 0 {
                                eprintln!("[health] Server recovered");
                            }
                            // Server is back — if we were respawning, reload the page
                            if respawning_nav.swap(false, Ordering::SeqCst) {
                                eprintln!("[health] Server respawned successfully — reloading");
                                respawn_count_nav.store(0, Ordering::SeqCst);
                                hide_reconnecting_overlay(&handle_nav);
                                if let Some(window) = handle_nav.get_webview_window("main") {
                                    let _ = window.eval(&format!(
                                        "window.location.replace('http://127.0.0.1:{}/?_boot={}')",
                                        port,
                                        boot_secret()
                                    ));
                                }
                            }
                            consecutive_failures = 0;
                        } else {
                            consecutive_failures += 1;
                            eprintln!(
                                "[health] Server unreachable ({}/3)",
                                consecutive_failures
                            );
                            if consecutive_failures >= 3 {
                                if cfg!(debug_assertions) {
                                    // Dev mode: can't respawn sidecar, show overlay and
                                    // keep monitoring — dev server might restart manually.
                                    eprintln!("[health] Server down in dev mode — showing overlay");
                                    show_reconnecting_overlay(&handle_nav);
                                    respawning_nav.store(true, Ordering::SeqCst);
                                }
                                // Reset counter so health check keeps monitoring
                                // (release mode: Terminated handler handles respawn).
                                consecutive_failures = 0;
                            }
                        }
                    }
                });
            }

            install_menu(app)?;

            // Build system tray
            let show_i = MenuItem::with_id(app, "show", "Show Best Photo Picker", true, None::<&str>)?;
            let quit_i = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let tray_menu = Menu::with_items(app, &[&show_i, &quit_i])?;

            TrayIconBuilder::new()
                .icon(app.default_window_icon().unwrap().clone())
                .icon_as_template(false)
                .menu(&tray_menu)
                .tooltip("Best Photo Picker")
                .on_menu_event(move |app, event| match event.id.as_ref() {
                    "show" => {
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                    }
                    "quit" => {
                        app.exit(0);
                    }
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        let app = tray.app_handle();
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                    }
                })
                .build(app)?;

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| match event {
            RunEvent::WindowEvent {
                label,
                event: WindowEvent::CloseRequested { api, .. },
                ..
            } if label == "main" => {
                // Hide instead of close (keep tray running)
                api.prevent_close();
                if let Some(window) = app_handle.get_webview_window("main") {
                    let _ = window.hide();
                }
            }
            RunEvent::Reopen { .. } => {
                // macOS dock icon click — show the hidden window
                if let Some(window) = app_handle.get_webview_window("main") {
                    let _ = window.show();
                    let _ = window.set_focus();
                }
            }
            RunEvent::ExitRequested { .. } => {
                // Kill the sidecar on exit
                let state = app_handle.state::<Mutex<ServerState>>();
                let mut guard = state.lock().unwrap();
                if let Some(child) = guard.child.take() {
                    let _ = child.kill();
                }
            }
            _ => {}
        });
}

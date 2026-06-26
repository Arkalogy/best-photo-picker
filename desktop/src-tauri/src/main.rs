// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU8, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tauri::{
    menu::{AboutMetadata, Menu, MenuItem, PredefinedMenuItem, Submenu},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    AppHandle, Manager, RunEvent, Theme, WindowEvent,
};
use tauri_plugin_shell::ShellExt;

const MAX_RESPAWN_ATTEMPTS: u32 = 5;

// Startup-failure diagnosis codes, shared from the sidecar stderr scanner
// to the failure screens. A blind TCP-poll timeout can't tell the user WHY
// the server never came up; these let us turn a deterministic, non-retryable
// failure into a clear, actionable message instead of "did not respond".
const STARTUP_OK: u8 = 0;
// The library DB was migrated by a NEWER bpp build than this one. The
// sidecar refuses to open it (anti-data-loss guard in schema_migrate.py)
// and exits — retrying is pointless; the user must update the app.
const STARTUP_NEWER_SCHEMA: u8 = 1;

/// Scan a sidecar log line for a known, deterministic startup failure.
/// Returns the matching STARTUP_* code, or STARTUP_OK if nothing matched.
/// Matches the schema-guard RuntimeError raised in
/// `bpp/db/schema_migrate.py` ("… Refusing to open …").
fn classify_startup_failure(line: &str) -> u8 {
    if line.contains("Refusing to open")
        && (line.contains("schema") || line.contains("newer-schema"))
    {
        return STARTUP_NEWER_SCHEMA;
    }
    STARTUP_OK
}

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

struct ServerState {
    child: Option<tauri_plugin_shell::process::CommandChild>,
}

struct RestartState {
    port: u16,
    respawning: Arc<AtomicBool>,
    respawn_count: Arc<AtomicU32>,
    startup_failure: Arc<AtomicU8>,
}

fn wait_for_server(port: u16, timeout_secs: u64) -> bool {
    let start = std::time::Instant::now();
    let timeout = Duration::from_secs(timeout_secs);
    let addr = format!("127.0.0.1:{}", port);

    while start.elapsed() < timeout {
        if std::net::TcpStream::connect(&addr).is_ok() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    false
}

fn server_is_alive(port: u16) -> bool {
    let addr = format!("127.0.0.1:{}", port);
    std::net::TcpStream::connect_timeout(
        &addr.parse().unwrap(),
        Duration::from_secs(2),
    )
    .is_ok()
}

/// Show a "reconnecting" overlay in the webview.
fn show_reconnecting_overlay(handle: &AppHandle) {
    if let Some(window) = handle.get_webview_window("main") {
        let _ = window.eval(
            r#"(function(){
  if(document.getElementById('bpp-reconnect-overlay'))return;
  var o=document.createElement('div');o.id='bpp-reconnect-overlay';
  o.style.cssText='position:fixed;inset:0;z-index:99999;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,0.7);font-family:system-ui;color:#fff;backdrop-filter:blur(4px)';
  o.innerHTML='<div style="text-align:center"><div style="width:32px;height:32px;border:3px solid rgba(255,255,255,0.3);border-top-color:#fff;border-radius:50%;margin:0 auto 16px;animation:bpp-spin 0.8s linear infinite"></div><h2 style="margin:0 0 8px;font-size:18px">Server disconnected</h2><p style="margin:0 0 16px;color:rgba(255,255,255,0.7);font-size:14px">Reconnecting\u2026</p><div style="display:flex;gap:8px;justify-content:center"><button onclick="location.reload()" style="padding:8px 20px;background:#4a9eff;color:#fff;border:none;border-radius:6px;font-size:14px;cursor:pointer">Reload</button><button onclick="window.__TAURI__?.core?.invoke(\'exit_app\')" style="padding:8px 20px;background:rgba(255,255,255,0.15);color:#fff;border:none;border-radius:6px;font-size:14px;cursor:pointer">Quit</button></div></div>';
  var s=document.createElement('style');s.textContent='@keyframes bpp-spin{to{transform:rotate(360deg)}}';
  document.head.appendChild(s);document.body.appendChild(o);
})()"#
        );
    }
}

/// Show a fatal error screen when all respawn attempts are exhausted.
fn show_fatal_error(handle: &AppHandle) {
    if let Some(window) = handle.get_webview_window("main") {
        let _ = window.eval(
            r#"(function(){
  var o=document.getElementById('bpp-reconnect-overlay');
  var btn='padding:8px 20px;border:none;border-radius:6px;font-size:14px;cursor:pointer';
  if(o)o.innerHTML='<div style="text-align:center;max-width:400px"><h2 style="margin:0 0 12px;font-size:20px;color:#ff6b6b">Server could not restart</h2><p style="margin:0 0 20px;color:rgba(255,255,255,0.7);font-size:14px">The server failed to restart after several attempts.</p><div style="display:flex;gap:8px;justify-content:center;flex-wrap:wrap"><button onclick="window.__TAURI__?.core?.invoke(\'exit_app\')" style="'+btn+';background:#4a9eff;color:#fff">Quit</button><button onclick="location.reload()" style="'+btn+';background:rgba(255,255,255,0.15);color:#fff">Try Again</button><button onclick="window.__TAURI__?.opener?.openUrl(\'https://github.com/Arkalogy/best-photo-picker/issues/new?template=bug_report.md&title=Server+failed+to+restart\')" style="'+btn+';background:rgba(255,255,255,0.1);color:rgba(255,255,255,0.7)">Report Issue</button></div></div>';
})()"#
        );
    }
}

/// Show a tailored "update required" screen when the sidecar refused to
/// open the library because its schema is newer than this build understands.
/// This replaces the generic "Server failed to start" timeout screen for
/// that specific, deterministic case — retrying won't help; updating will.
/// Reassure the user their photos are safe (the guard exists to protect them).
fn show_update_required(handle: &AppHandle) {
    if let Some(window) = handle.get_webview_window("main") {
        let _ = window.eval(concat!(
            "document.body.innerHTML='<div style=\"display:flex;align-items:center;justify-content:center;height:100vh;font-family:system-ui;background:#111;color:#ccc\">",
            "<div style=\"text-align:center;max-width:440px;padding:0 24px\">",
            "<h2 style=\"margin:0 0 8px;font-size:20px;color:#fff\">Update Best Photo Picker</h2>",
            "<p style=\"margin:0 0 12px;font-size:14px;line-height:1.5;color:rgba(255,255,255,0.65)\">This photo library was created by a newer version of Best Photo Picker, so this older app won&#39;t open it.</p>",
            "<p style=\"margin:0 0 24px;font-size:13px;color:rgba(255,255,255,0.45)\">Your photos and library are safe &mdash; the app stopped on purpose to avoid damaging them. Update to the latest version to continue.</p>",
            "<div style=\"display:flex;gap:8px;justify-content:center;flex-wrap:wrap\">",
            "<button onclick=\"window.__TAURI__?.opener?.openUrl(\\'https://github.com/Arkalogy/best-photo-picker/releases/latest\\')\" style=\"padding:8px 20px;background:#4a9eff;color:#fff;border:none;border-radius:6px;font-size:14px;cursor:pointer\">Get the latest version</button>",
            "<button onclick=\"window.__TAURI__?.core?.invoke(\\'exit_app\\')\" style=\"padding:8px 20px;background:rgba(255,255,255,0.15);color:#fff;border:none;border-radius:6px;font-size:14px;cursor:pointer\">Quit</button>",
            "</div></div></div>';"
        ));
    }
}

/// Remove the reconnecting overlay (server is back).
fn hide_reconnecting_overlay(handle: &AppHandle) {
    if let Some(window) = handle.get_webview_window("main") {
        let _ = window.eval(
            "var o=document.getElementById('bpp-reconnect-overlay');if(o)o.remove();"
        );
    }
}

/// Spawn the sidecar process and wire up log forwarding + termination handling.
fn spawn_sidecar(
    handle: &AppHandle,
    port: u16,
    respawning: Arc<AtomicBool>,
    respawn_count: Arc<AtomicU32>,
    startup_failure: Arc<AtomicU8>,
) {
    let sidecar = handle
        .shell()
        .sidecar("bpp-server")
        .expect("failed to create sidecar command")
        .args(["serve", "--no-browser", "--port", &port.to_string()]);

    let (mut rx, child) = sidecar.spawn().expect("failed to spawn bpp sidecar");

    // Store child handle for cleanup
    {
        let state = handle.state::<Mutex<ServerState>>();
        let mut guard = state.lock().unwrap();
        guard.child = Some(child);
    }

    // Log sidecar output; respawn if sidecar terminates
    let handle_clone = handle.clone();
    tauri::async_runtime::spawn(async move {
        use tauri_plugin_shell::process::CommandEvent;
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(line) => {
                    let s = String::from_utf8_lossy(&line);
                    // The schema-guard error can surface on either stream
                    // depending on how the logger is wired — scan both.
                    let code = classify_startup_failure(&s);
                    if code != STARTUP_OK {
                        startup_failure.store(code, Ordering::SeqCst);
                    }
                    eprintln!("[server] {}", s);
                }
                CommandEvent::Stderr(line) => {
                    let s = String::from_utf8_lossy(&line);
                    let code = classify_startup_failure(&s);
                    if code != STARTUP_OK {
                        startup_failure.store(code, Ordering::SeqCst);
                    }
                    eprintln!("[server] {}", s);
                }
                CommandEvent::Terminated(payload) => {
                    eprintln!(
                        "[server] terminated: code={:?} signal={:?}",
                        payload.code, payload.signal
                    );
                    // A newer-schema DB is a deterministic, non-retryable
                    // failure: respawning just crashes the same way. Skip the
                    // respawn loop and show the actionable update screen now.
                    if startup_failure.load(Ordering::SeqCst) == STARTUP_NEWER_SCHEMA {
                        eprintln!("[server] DB newer than this build — not respawning; prompting to update");
                        show_update_required(&handle_clone);
                        break;
                    }
                    // Trigger respawn via the health-check loop
                    respawning.store(true, Ordering::SeqCst);
                    show_reconnecting_overlay(&handle_clone);

                    let attempt = respawn_count.fetch_add(1, Ordering::SeqCst) + 1;
                    if attempt > MAX_RESPAWN_ATTEMPTS {
                        eprintln!("[server] Max respawn attempts reached — giving up");
                        show_fatal_error(&handle_clone);
                        break;
                    }

                    // Exponential backoff: 1s, 2s, 4s, 8s, 16s
                    let delay = Duration::from_secs(1 << (attempt - 1).min(4));
                    eprintln!("[server] Respawning (attempt {}/{}) after {:?}", attempt, MAX_RESPAWN_ATTEMPTS, delay);
                    std::thread::sleep(delay);

                    spawn_sidecar(
                        &handle_clone,
                        port,
                        respawning.clone(),
                        respawn_count.clone(),
                        startup_failure.clone(),
                    );
                    break;
                }
                _ => {}
            }
        }
    });
}

/// Kill the current sidecar (if any) and spawn a fresh one.
fn restart_server(handle: &AppHandle) {
    let rs = handle.state::<RestartState>();
    let port = rs.port;
    let respawning = rs.respawning.clone();
    let respawn_count = rs.respawn_count.clone();
    let startup_failure = rs.startup_failure.clone();

    // Kill existing child
    {
        let state = handle.state::<Mutex<ServerState>>();
        let mut guard = state.lock().unwrap();
        if let Some(child) = guard.child.take() {
            eprintln!("[restart] Killing existing server");
            let _ = child.kill();
        }
    }

    // Reset respawn + diagnosis bookkeeping for the fresh attempt.
    respawn_count.store(0, Ordering::SeqCst);
    respawning.store(true, Ordering::SeqCst);
    startup_failure.store(STARTUP_OK, Ordering::SeqCst);
    show_reconnecting_overlay(handle);

    // Brief pause for port release, then spawn
    std::thread::sleep(Duration::from_millis(500));
    spawn_sidecar(handle, port, respawning, respawn_count, startup_failure);

    // Health-check loop will detect server is back and reload the page
    eprintln!("[restart] Server restart initiated");
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![set_app_theme, exit_app])
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
            if !cfg!(debug_assertions) {
                spawn_sidecar(&handle, port, respawning.clone(), respawn_count.clone(), startup_failure.clone());
            } else {
                eprintln!("[dev] Server managed by beforeDevCommand on port {}", port);
            }

            // Wait for server to be reachable, then redirect and monitor.
            {
                let handle_nav = handle.clone();
                let respawning_nav = respawning.clone();
                let respawn_count_nav = respawn_count.clone();
                let startup_failure_nav = startup_failure.clone();
                std::thread::spawn(move || {
                    let splash_start = std::time::Instant::now();
                    let min_splash = Duration::from_millis(1500);

                    if !wait_for_server(port, 30) {
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
                        eprintln!("Server failed to start within 30 seconds");
                        if let Some(window) = handle_nav.get_webview_window("main") {
                            let _ = window.eval(concat!(
                                "document.body.innerHTML='<div style=\"display:flex;align-items:center;justify-content:center;height:100vh;font-family:system-ui;background:#111;color:#ccc\">",
                                "<div style=\"text-align:center;max-width:400px\">",
                                "<h2 style=\"margin:0 0 8px;font-size:20px;color:#fff\">Server failed to start</h2>",
                                "<p style=\"margin:0 0 24px;font-size:14px;color:rgba(255,255,255,0.55)\">Best Photo Picker server did not respond within 30 seconds.</p>",
                                "<div style=\"display:flex;gap:8px;justify-content:center;flex-wrap:wrap\">",
                                "<button onclick=\"location.reload()\" style=\"padding:8px 20px;background:#4a9eff;color:#fff;border:none;border-radius:6px;font-size:14px;cursor:pointer\">Try Again</button>",
                                "<button onclick=\"window.__TAURI__?.core?.invoke(\\'exit_app\\')\" style=\"padding:8px 20px;background:rgba(255,255,255,0.15);color:#fff;border:none;border-radius:6px;font-size:14px;cursor:pointer\">Quit</button>",
                                "<button onclick=\"window.__TAURI__?.opener?.openUrl(\\'https://github.com/Arkalogy/best-photo-picker/issues/new?template=bug_report.md&title=Server+failed+to+start\\')\" style=\"padding:8px 20px;background:rgba(255,255,255,0.1);color:rgba(255,255,255,0.6);border:none;border-radius:6px;font-size:14px;cursor:pointer\">Report Issue</button>",
                                "</div></div></div>';"
                            ));
                        }
                        return;
                    }

                    // Show splash for at least 1.5s
                    let elapsed = splash_start.elapsed();
                    if elapsed < min_splash {
                        std::thread::sleep(min_splash - elapsed);
                    }

                    // Redirect webview from splash screen to server
                    let url = format!("http://localhost:{}", port);
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
                                        "window.location.replace('http://localhost:{}')",
                                        port
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

            // Build app menu bar (macOS needs this for keyboard accelerators)
            let preferences_item = MenuItem::with_id(
                app, "preferences", "Settings\u{2026}", true, Some("CmdOrCtrl+,"),
            )?;
            let app_submenu = Submenu::with_items(
                app,
                "Best Photo Picker",
                true,
                &[
                    &PredefinedMenuItem::about(
                        app,
                        Some("About Best Photo Picker"),
                        Some(AboutMetadata {
                            name: Some("Best Photo Picker".into()),
                            version: Some(env!("CARGO_PKG_VERSION").into()),
                            copyright: Some("© 2026 Arkalogy LLC".into()),
                            credits: Some("Developed with AI-assisted coding".into()),
                            icon: tauri::image::Image::from_path("icons/icon.png").ok(),
                            ..Default::default()
                        }),
                    )?,
                    &PredefinedMenuItem::separator(app)?,
                    &preferences_item,
                    &PredefinedMenuItem::separator(app)?,
                    &PredefinedMenuItem::hide(app, Some("Hide Best Photo Picker"))?,
                    &PredefinedMenuItem::hide_others(app, None)?,
                    &PredefinedMenuItem::show_all(app, None)?,
                    &PredefinedMenuItem::separator(app)?,
                    &PredefinedMenuItem::quit(app, Some("Quit Best Photo Picker"))?,
                ],
            )?;
            let open_library_item = MenuItem::with_id(
                app, "open_library", "Open Library\u{2026}", true, Some("CmdOrCtrl+O"),
            )?;
            let new_library_item = MenuItem::with_id(
                app, "new_library", "New Library\u{2026}", true, Some("CmdOrCtrl+Shift+N"),
            )?;
            let import_item = MenuItem::with_id(
                app, "import", "Import Photos\u{2026}", true, Some("CmdOrCtrl+I"),
            )?;
            let export_item = MenuItem::with_id(
                app, "export", "Export\u{2026}", true, Some("CmdOrCtrl+E"),
            )?;
            let restart_server_item = MenuItem::with_id(
                app, "restart_server", "Restart Server", true, None::<&str>,
            )?;
            let file_submenu = Submenu::with_items(
                app,
                "File",
                true,
                &[
                    &open_library_item,
                    &new_library_item,
                    &PredefinedMenuItem::separator(app)?,
                    &import_item,
                    &export_item,
                    &PredefinedMenuItem::separator(app)?,
                    &restart_server_item,
                ],
            )?;
            let search_item = MenuItem::with_id(
                app, "search", "Search", true, Some("CmdOrCtrl+K"),
            )?;
            let edit_submenu = Submenu::with_items(
                app,
                "Edit",
                true,
                &[
                    &PredefinedMenuItem::undo(app, None)?,
                    &PredefinedMenuItem::redo(app, None)?,
                    &PredefinedMenuItem::separator(app)?,
                    &PredefinedMenuItem::cut(app, None)?,
                    &PredefinedMenuItem::copy(app, None)?,
                    &PredefinedMenuItem::paste(app, None)?,
                    &PredefinedMenuItem::select_all(app, None)?,
                    &PredefinedMenuItem::separator(app)?,
                    &search_item,
                ],
            )?;
            // View menu
            let toggle_sidebar_item = MenuItem::with_id(
                app, "toggle_sidebar", "Toggle Sidebar", true, Some("CmdOrCtrl+\\"),
            )?;
            let zoom_in_item = MenuItem::with_id(
                app, "zoom_in", "Zoom In", true, Some("CmdOrCtrl+="),
            )?;
            let zoom_out_item = MenuItem::with_id(
                app, "zoom_out", "Zoom Out", true, Some("CmdOrCtrl+-"),
            )?;
            let slideshow_item = MenuItem::with_id(
                app, "slideshow", "Slideshow", true, None::<&str>,
            )?;
            let fullscreen_item = PredefinedMenuItem::fullscreen(app, None)?;
            let activity_log_item = MenuItem::with_id(
                app, "activity_log", "Activity Log", true, None::<&str>,
            )?;
            let view_submenu = Submenu::with_items(
                app,
                "View",
                true,
                &[
                    &toggle_sidebar_item,
                    &PredefinedMenuItem::separator(app)?,
                    &zoom_in_item,
                    &zoom_out_item,
                    &PredefinedMenuItem::separator(app)?,
                    &slideshow_item,
                    &PredefinedMenuItem::separator(app)?,
                    &activity_log_item,
                    &PredefinedMenuItem::separator(app)?,
                    &fullscreen_item,
                ],
            )?;
            // Window menu
            let window_submenu = Submenu::with_items(
                app,
                "Window",
                true,
                &[
                    &PredefinedMenuItem::minimize(app, None)?,
                    &PredefinedMenuItem::maximize(app, Some("Zoom"))?,
                    &PredefinedMenuItem::separator(app)?,
                    &PredefinedMenuItem::close_window(app, Some("Close Window"))?,
                ],
            )?;
            // Help menu
            let help_docs_item = MenuItem::with_id(
                app, "help_docs", "Documentation", true, None::<&str>,
            )?;
            let help_issues_item = MenuItem::with_id(
                app, "help_issues", "Report an Issue\u{2026}", true, None::<&str>,
            )?;
            let help_repo_item = MenuItem::with_id(
                app, "help_repo", "GitHub Repository", true, None::<&str>,
            )?;
            let help_updates_item = MenuItem::with_id(
                app, "help_updates", "Check for Updates\u{2026}", true, None::<&str>,
            )?;
            let help_submenu = Submenu::with_items(
                app,
                "Help",
                true,
                &[
                    &help_updates_item,
                    &PredefinedMenuItem::separator(app)?,
                    &help_docs_item,
                    &help_repo_item,
                    &PredefinedMenuItem::separator(app)?,
                    &help_issues_item,
                ],
            )?;
            let app_menu = Menu::with_items(
                app,
                &[&app_submenu, &file_submenu, &edit_submenu, &view_submenu, &window_submenu, &help_submenu],
            )?;
            app.set_menu(app_menu)?;

            // Handle app menu events
            let handle_menu = handle.clone();
            app.on_menu_event(move |app_handle, event| {
                if let Some(window) = handle_menu.get_webview_window("main") {
                    match event.id().as_ref() {
                        "search" => {
                            let _ = window.eval(
                                "if(typeof isSearchOpen==='function'){\
                                 if(isSearchOpen())hideSearch();else showSearch();}"
                            );
                        }
                        "preferences" => {
                            let _ = window.eval(
                                "if(typeof showSettings==='function')showSettings();"
                            );
                        }
                        "open_library" => {
                            let _ = window.eval(
                                "if(typeof showLibraryPicker==='function')showLibraryPicker();"
                            );
                        }
                        "new_library" => {
                            let _ = window.eval(
                                "if(typeof createNewLibrary==='function')createNewLibrary();"
                            );
                        }
                        "import" => {
                            let _ = window.eval(
                                "if(typeof showImportModal==='function')showImportModal();"
                            );
                        }
                        "export" => {
                            let _ = window.eval(
                                "if(typeof showExportModal==='function')showExportModal();"
                            );
                        }
                        "restart_server" => {
                            restart_server(app_handle);
                        }
                        // View menu
                        "toggle_sidebar" => {
                            let _ = window.eval(
                                "if(typeof toggleSidebar==='function')toggleSidebar();"
                            );
                        }
                        "zoom_in" => {
                            let _ = window.eval(
                                "if(typeof applyZoom==='function'){let z=parseInt(document.getElementById('zoom-slider')?.value||80)+10;applyZoom(Math.min(z,200),true);}"
                            );
                        }
                        "zoom_out" => {
                            let _ = window.eval(
                                "if(typeof applyZoom==='function'){let z=parseInt(document.getElementById('zoom-slider')?.value||80)-10;applyZoom(Math.max(z,40),true);}"
                            );
                        }
                        "slideshow" => {
                            let _ = window.eval(
                                "if(typeof startSlideshow==='function')startSlideshow(0);"
                            );
                        }
                        "activity_log" => {
                            let _ = window.eval(
                                "if(typeof showActivityLog==='function')showActivityLog();"
                            );
                        }
                        // Help menu
                        "help_updates" => {
                            let _ = window.eval(
                                "if(typeof checkForUpdates==='function')checkForUpdates(true);"
                            );
                        }
                        "help_docs" => {
                            let _ = app_handle.shell().open(
                                "https://github.com/Arkalogy/best-photo-picker#readme",
                                None,
                            );
                        }
                        "help_repo" => {
                            let _ = app_handle.shell().open(
                                "https://github.com/Arkalogy/best-photo-picker",
                                None,
                            );
                        }
                        "help_issues" => {
                            let _ = app_handle.shell().open(
                                "https://github.com/Arkalogy/best-photo-picker/issues/new",
                                None,
                            );
                        }
                        _ => {}
                    }
                }
            });

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

//! The server sidecar: spawning it, reading its output for the
//! failures that are final (a newer-schema library, a held port),
//! respawning it with backoff, and the menu's Restart Server.

use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU8, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tauri::{AppHandle, Manager};
use tauri_plugin_shell::ShellExt;

use crate::boot::boot_secret;
use crate::screens::{show_fatal_error, show_reconnecting_overlay, show_start_failure, show_update_required};

pub(crate) const MAX_RESPAWN_ATTEMPTS: u32 = 5;

// Startup-failure diagnosis codes, shared from the sidecar stderr scanner
// to the failure screens. A blind TCP-poll timeout can't tell the user WHY
// the server never came up; these let us turn a deterministic, non-retryable
// failure into a clear, actionable message instead of "did not respond".
pub(crate) const STARTUP_OK: u8 = 0;
// The library DB was migrated by a NEWER bpp build than this one. The
// sidecar refuses to open it (anti-data-loss guard in schema_migrate.py)
// and exits — retrying is pointless; the user must update the app.
pub(crate) const STARTUP_NEWER_SCHEMA: u8 = 1;
// Something else holds the port (a `bpp serve` the user runs, or a
// sidecar of ours that survived a force-quit and could not be reclaimed).
pub(crate) const STARTUP_PORT_IN_USE: u8 = 2;

/// Scan a sidecar log line for a known, deterministic startup failure.
/// Returns the matching STARTUP_* code, or STARTUP_OK if nothing matched.
/// Matches the schema-guard RuntimeError raised in
/// `bpp/db/schema_migrate.py` ("… Refusing to open …").
pub(crate) fn classify_startup_failure(line: &str) -> u8 {
    if line.contains("Refusing to open")
        && (line.contains("schema") || line.contains("newer-schema"))
    {
        return STARTUP_NEWER_SCHEMA;
    }
    if line.contains("Address already in use") || line.contains("address already in use") {
        return STARTUP_PORT_IN_USE;
    }
    STARTUP_OK
}

pub(crate) struct ServerState {
    pub(crate) child: Option<tauri_plugin_shell::process::CommandChild>,
}

pub(crate) struct RestartState {
    pub(crate) port: u16,
    pub(crate) respawning: Arc<AtomicBool>,
    pub(crate) respawn_count: Arc<AtomicU32>,
    pub(crate) startup_failure: Arc<AtomicU8>,
}

/// Spawn the sidecar process and wire up log forwarding + termination handling.
pub(crate) fn spawn_sidecar(
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
        .args(["serve", "--no-browser", "--port", &port.to_string()])
        // The loopback index renders the owner token only to a caller
        // that presents this secret (our first navigation), not to any
        // local process that reaches the port.
        .env("BPP_BOOT_SECRET", boot_secret());

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
                    // The port is taken by another process: respawning only
                    // fails the same way, and the overlay would paint over
                    // the "another copy is running" screen.
                    if startup_failure.load(Ordering::SeqCst) == STARTUP_PORT_IN_USE {
                        eprintln!("[server] port in use — not respawning; showing the held-port screen");
                        show_start_failure(&handle_clone, true);
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
pub(crate) fn restart_server(handle: &AppHandle) {
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

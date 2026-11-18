//! The server sidecar: spawning it, reading its output for the
//! failures that are final (a newer-schema library, a held port, a
//! library it refuses to open), respawning it with backoff, and the
//! menu's Restart Server.

use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, AtomicU8, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tauri::{AppHandle, Manager};
use tauri_plugin_shell::ShellExt;

use crate::boot::boot_secret;
use crate::screens::{
    show_fatal_error, show_library_refused, show_reconnecting_overlay, show_start_failure,
    show_update_required,
};

pub(crate) const MAX_RESPAWN_ATTEMPTS: u32 = 5;

/// The server, relative to the app's own executable (Contents/MacOS):
/// a background-only helper app the bundle carries in Contents/Helpers
/// (tauri.conf.json `bundle.macOS.files`, built by build-sidecar.sh).
/// It is a one-folder build, so nothing is unpacked when it starts. The
/// executable keeps the name `bpp-server`, which the port reclaim and
/// orphan checks in boot.rs match on.
pub(crate) const SERVER_EXECUTABLE: &str = "../Helpers/bpp-server.app/Contents/MacOS/bpp-server";

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
// The server can't open the library for a reason a retry won't change
// (a link where one of its folders should be, another bpp holding it,
// a damaged database). Its own line says why: `REFUSAL_MARKER`.
pub(crate) const STARTUP_LIBRARY_REFUSED: u8 = 3;

/// How `bpp serve` starts that line (bpp/commands/serve.py).
const REFUSAL_MARKER: &str = "BPP_LIBRARY_REFUSED:";
/// What the last such line said, for the screen that shows it.
static REFUSAL: Mutex<Option<String>> = Mutex::new(None);
/// Where it is also written, in the app's log folder: the library's own
/// log may be what could not be opened.
const REFUSAL_LOG: &str = "startup.log";

/// The reason on a refusal line, or None for any other line. Only a
/// line that starts with the marker: the server prints it so, and any
/// other line (a logged request) may carry text a sender chose.
pub(crate) fn refusal_reason(line: &str) -> Option<String> {
    let reason = line.strip_prefix(REFUSAL_MARKER)?.trim();
    if reason.is_empty() {
        None
    } else {
        Some(reason.to_string())
    }
}

/// The reason the server last gave for refusing the library.
pub(crate) fn last_refusal() -> String {
    REFUSAL.lock().ok().and_then(|r| r.clone()).unwrap_or_default()
}

pub(crate) fn clear_refusal() {
    if let Ok(mut r) = REFUSAL.lock() {
        *r = None;
    }
}

/// The app's own log file for refusals (outside every library).
pub(crate) fn refusal_log_path(handle: &AppHandle) -> Option<std::path::PathBuf> {
    handle.path().app_log_dir().ok().map(|d| d.join(REFUSAL_LOG))
}

/// `secs` since 1970 as a UTC date and time (no date library here).
pub(crate) fn utc_stamp(secs: u64) -> String {
    let (days, rest) = ((secs / 86_400) as i64, secs % 86_400);
    // Days to a civil date: H. Hinnant's algorithm.
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = yoe + era * 400 + i64::from(month <= 2);
    format!(
        "{:04}-{:02}-{:02} {:02}:{:02}:{:02} UTC",
        year,
        month,
        day,
        rest / 3_600,
        rest % 3_600 / 60,
        rest % 60
    )
}

fn note_refusal(handle: &AppHandle, reason: &str) {
    if let Ok(mut r) = REFUSAL.lock() {
        *r = Some(reason.to_string());
    }
    if let Some(path) = refusal_log_path(handle) {
        use std::io::Write;
        let secs = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        let stamp = utc_stamp(secs);
        let written = path
            .parent()
            .map(std::fs::create_dir_all)
            .unwrap_or(Ok(()))
            .and_then(|_| std::fs::OpenOptions::new().create(true).append(true).open(&path))
            .and_then(|mut f| writeln!(f, "{} the library was not opened: {}", stamp, reason));
        if let Err(e) = written {
            eprintln!("[server] could not write {}: {}", path.display(), e);
        }
    }
}

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
    /// Bumped by Restart Server before it kills the child: a Terminated
    /// handler whose child belongs to an older generation stands down
    /// instead of spawning a second replacement next to the one Restart
    /// Server spawns (and a stale dead handle can never arm anything).
    pub(crate) generation: Arc<AtomicU64>,
}

/// Spawn the sidecar process and wire up log forwarding + termination handling.
pub(crate) fn spawn_sidecar(
    handle: &AppHandle,
    port: u16,
    respawning: Arc<AtomicBool>,
    respawn_count: Arc<AtomicU32>,
    startup_failure: Arc<AtomicU8>,
    generation: Arc<AtomicU64>,
) {
    // The generation this child belongs to; bumped by Restart Server.
    let my_generation = generation.load(Ordering::SeqCst);
    let sidecar = handle
        .shell()
        .sidecar(SERVER_EXECUTABLE)
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
                CommandEvent::Stdout(line) | CommandEvent::Stderr(line) => {
                    let s = String::from_utf8_lossy(&line);
                    // The schema-guard error can surface on either stream
                    // depending on how the logger is wired — scan both.
                    let code = classify_startup_failure(&s);
                    if code != STARTUP_OK {
                        startup_failure.store(code, Ordering::SeqCst);
                    }
                    if let Some(reason) = refusal_reason(&s) {
                        note_refusal(&handle_clone, &reason);
                        startup_failure.store(STARTUP_LIBRARY_REFUSED, Ordering::SeqCst);
                    }
                    eprintln!("[server] {}", s);
                }
                CommandEvent::Terminated(payload) => {
                    eprintln!(
                        "[server] terminated: code={:?} signal={:?}",
                        payload.code, payload.signal
                    );
                    // Restart Server killed this child on purpose and spawns
                    // the replacement itself: one supervisor, one child.
                    if generation.load(Ordering::SeqCst) != my_generation {
                        eprintln!("[server] stopped on purpose — the restart spawns the replacement");
                        break;
                    }
                    // A newer-schema DB is a deterministic, non-retryable
                    // failure: respawning just crashes the same way. Skip the
                    // respawn loop and show the actionable update screen now.
                    if startup_failure.load(Ordering::SeqCst) == STARTUP_NEWER_SCHEMA {
                        eprintln!("[server] DB newer than this build — not respawning; prompting to update");
                        show_update_required(&handle_clone);
                        break;
                    }
                    // The library can't be opened, and the server said why:
                    // respawning refuses it the same way.
                    if startup_failure.load(Ordering::SeqCst) == STARTUP_LIBRARY_REFUSED {
                        eprintln!("[server] library refused — not respawning; showing why");
                        show_library_refused(&handle_clone, &last_refusal());
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
                    // Restart Server may have spawned the replacement during
                    // the backoff: then this thread's child is history and a
                    // second spawn would only fight it for the port.
                    if generation.load(Ordering::SeqCst) != my_generation {
                        eprintln!("[server] restarted during the backoff — not respawning");
                        break;
                    }

                    spawn_sidecar(
                        &handle_clone,
                        port,
                        respawning.clone(),
                        respawn_count.clone(),
                        startup_failure.clone(),
                        generation.clone(),
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
    let generation = rs.generation.clone();

    // A new generation: the old child's Terminated handler sees it and
    // stands down, whether the kill lands or the handle is stale. The
    // child is taken out under the lock but stopped after it is released,
    // so a Quit meanwhile (whose exit handler takes the same lock on the
    // main thread) is not held up by the stop.
    let my_generation = generation.fetch_add(1, Ordering::SeqCst) + 1;
    let old_child = {
        let state = handle.state::<Mutex<ServerState>>();
        let mut guard = state.lock().unwrap();
        guard.child.take()
    };
    if let Some(child) = old_child {
        eprintln!("[restart] Stopping existing server");
        stop_child(child, port, 5);
    }

    // Reset respawn + diagnosis bookkeeping for the fresh attempt.
    respawn_count.store(0, Ordering::SeqCst);
    respawning.store(true, Ordering::SeqCst);
    startup_failure.store(STARTUP_OK, Ordering::SeqCst);
    clear_refusal();
    show_reconnecting_overlay(handle);

    // Brief pause for port release, then spawn, unless the app began to
    // quit meanwhile (its exit handler bumped the generation): a server
    // started now would outlive the app.
    std::thread::sleep(Duration::from_millis(500));
    if generation.load(Ordering::SeqCst) != my_generation {
        eprintln!("[restart] The app is quitting; not starting a new server");
        return;
    }
    spawn_sidecar(handle, port, respawning, respawn_count, startup_failure, generation);

    // Health-check loop will detect server is back and reload the page
    eprintln!("[restart] Server restart initiated");
}


/// Stop a sidecar child: SIGTERM to the server, then wait up to
/// `wait_secs` for the process to exit, which also frees the port and
/// clears the library's serving lock (the server closes its port before
/// its exit hooks clear the lock, so a free port alone is too early: a
/// replacement started then refuses the still-held lock). SIGKILL is the
/// last resort. The kill also ends the port's listener when it is this
/// child's own (a server started behind a PyInstaller bootloader, as in
/// the one-file builds up to 0.2.3, keeps the port when only the
/// bootloader is killed); a listener another running copy started is
/// never touched, e.g. when this child already died on the held port.
pub(crate) fn stop_child(child: tauri_plugin_shell::process::CommandChild, port: u16, wait_secs: u64) {
    let pid = child.pid();
    let termed = std::process::Command::new("kill")
        .args(["-TERM", &pid.to_string()])
        .status()
        .map(|s| s.success())
        .unwrap_or(false);
    if termed {
        let deadline = std::time::Instant::now() + Duration::from_secs(wait_secs);
        while std::time::Instant::now() < deadline {
            if !process_alive(pid) && !crate::boot::port_is_held(port) {
                eprintln!("[sidecar] server {} exited and released port {}", pid, port);
                return;
            }
            std::thread::sleep(Duration::from_millis(200));
        }
        eprintln!("[sidecar] server {} did not exit in time — killing it", pid);
    }
    // If the child is a bootloader, kill() ends only it; the server it
    // started keeps the port. Find that server first, while it is still
    // the child's child.
    let listener = crate::boot::own_listener(port, pid);
    let _ = child.kill();
    if let Some(server) = listener.filter(|&l| l != pid) {
        std::thread::sleep(Duration::from_millis(200));
        crate::boot::kill_own_listener(server);
    }
}

/// Whether a process still exists (`kill -0`).
fn process_alive(pid: u32) -> bool {
    std::process::Command::new("kill")
        .args(["-0", &pid.to_string()])
        .stderr(std::process::Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_refusal_line_gives_its_reason() {
        let line = "BPP_LIBRARY_REFUSED: This library can't be opened: 'photos' inside it is a link.";
        assert_eq!(
            refusal_reason(line).as_deref(),
            Some("This library can't be opened: 'photos' inside it is a link.")
        );
    }

    #[test]
    fn a_logged_request_naming_the_marker_is_not_a_refusal() {
        let line = "12:00:01 [INFO ] werkzeug: 192.0.2.7 - - \"GET /BPP_LIBRARY_REFUSED: x HTTP/1.1\" 404 -";
        assert_eq!(refusal_reason(line), None);
    }

    #[test]
    fn other_lines_and_an_empty_reason_are_not_refusals() {
        assert_eq!(refusal_reason("12:00:00 [INFO ] bpp: Starting server on 127.0.0.1:5001"), None);
        assert_eq!(refusal_reason("BPP_LIBRARY_REFUSED:   "), None);
    }

    #[test]
    fn the_log_says_when_in_utc() {
        assert_eq!(utc_stamp(0), "1970-01-01 00:00:00 UTC");
        assert_eq!(utc_stamp(1_795_037_490), "2026-11-18 21:31:30 UTC");
        assert_eq!(utc_stamp(951_782_400), "2000-02-29 00:00:00 UTC");
    }

    #[test]
    fn a_refusal_is_not_mistaken_for_another_failure() {
        let line = "BPP_LIBRARY_REFUSED: Can't open /x/data: another bpp app is using it";
        assert_eq!(classify_startup_failure(line), STARTUP_OK);
    }
}

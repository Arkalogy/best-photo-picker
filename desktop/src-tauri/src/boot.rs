//! Start-up handshake with the sidecar: the per-launch boot key, the
//! challenge-response probe that proves the listener on the port is our
//! server, port reclaim after a force-quit, and the liveness check.

use std::sync::atomic::{AtomicU8, Ordering};
use std::time::Duration;

use crate::sidecar::STARTUP_OK;

/// Poll until the sidecar proves itself, or give up: at the timeout,
/// or as soon as the sidecar's own output names a final failure (a
/// newer-schema library, a port held by another process).
pub(crate) fn wait_for_server(port: u16, timeout_secs: u64, startup_failure: &AtomicU8) -> bool {
    let start = std::time::Instant::now();
    let timeout = Duration::from_secs(timeout_secs);

    while start.elapsed() < timeout {
        if server_knows_boot_secret(port) {
            return true;
        }
        if startup_failure.load(Ordering::SeqCst) != STARTUP_OK {
            return false;
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    false
}

/// The per-launch secret the sidecar gets and the webview presents:
/// 24 bytes of OS entropy, hex.
pub(crate) fn boot_secret() -> &'static str {
    use std::sync::OnceLock;
    static SECRET: OnceLock<String> = OnceLock::new();
    SECRET.get_or_init(|| {
        let mut buf = [0u8; 24];
        getrandom::getrandom(&mut buf).expect("OS entropy unavailable");
        buf.iter().map(|b| format!("{:02x}", b)).collect()
    })
}

/// Whoever answers on the port must prove it is a bpp server started
/// with our secret before the webview goes there: the shell sends a
/// fresh nonce and expects `X-Boot-Proof: HMAC-SHA256(secret, nonce)`.
/// The secret itself never goes over the wire, so a process squatting
/// on the port learns nothing and never gets the window. In a dev build
/// the server runs open (`--open-loopback`) and answers without a
/// proof, which is accepted there only.
pub(crate) fn server_knows_boot_secret(port: u16) -> bool {
    use std::io::{Read, Write};
    let addr = format!("127.0.0.1:{}", port);
    let Ok(mut stream) = std::net::TcpStream::connect_timeout(
        &addr.parse().unwrap(),
        Duration::from_secs(2),
    ) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    let nonce = fresh_nonce();
    let req = format!(
        "GET /boot-check?nonce={} HTTP/1.0\r\nHost: 127.0.0.1:{}\r\nConnection: close\r\n\r\n",
        nonce, port
    );
    if stream.write_all(req.as_bytes()).is_err() {
        return false;
    }
    let mut buf = Vec::new();
    let _ = stream.read_to_end(&mut buf);
    let head = String::from_utf8_lossy(&buf);
    if !(head.starts_with("HTTP/1.0 204") || head.starts_with("HTTP/1.1 204")) {
        return false;
    }
    let proof = head
        .lines()
        .find_map(|l| l.strip_prefix("X-Boot-Proof: ").or_else(|| l.strip_prefix("x-boot-proof: ")))
        .map(|v| v.trim().to_ascii_lowercase());
    match proof {
        Some(p) => p == hmac_sha256_hex(boot_secret().as_bytes(), nonce.as_bytes()),
        None => cfg!(debug_assertions),
    }
}

pub(crate) fn fresh_nonce() -> String {
    let mut buf = [0u8; 16];
    getrandom::getrandom(&mut buf).expect("OS entropy unavailable");
    buf.iter().map(|b| format!("{:02x}", b)).collect()
}

/// HMAC-SHA256 (RFC 2104) over sha2; the key is shorter than the block.
pub(crate) fn hmac_sha256_hex(key: &[u8], msg: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    let mut k = [0u8; 64];
    if key.len() > 64 {
        k[..32].copy_from_slice(&Sha256::digest(key));
    } else {
        k[..key.len()].copy_from_slice(key);
    }
    let ipad: Vec<u8> = k.iter().map(|b| b ^ 0x36).collect();
    let opad: Vec<u8> = k.iter().map(|b| b ^ 0x5c).collect();
    let inner = Sha256::new().chain_update(&ipad).chain_update(msg).finalize();
    let outer = Sha256::new().chain_update(&opad).chain_update(inner).finalize();
    outer.iter().map(|b| format!("{:02x}", b)).collect()
}

/// Does anything answer on the port at all (ours or not)?
pub(crate) fn port_is_held(port: u16) -> bool {
    let addr = format!("127.0.0.1:{}", port);
    std::net::TcpStream::connect_timeout(&addr.parse().unwrap(), Duration::from_millis(500)).is_ok()
}

/// A sidecar of ours that outlived a force-quit or crash still holds
/// the port with the old secret; the new sidecar could not bind and the
/// user would get a dead end. Reclaim it: only the process that listens
/// on the port, only when it is our own `bpp-server` binary, and only
/// when its shell is gone (the sidecar has been reparented to launchd).
/// Never a `bpp serve` the user runs by hand, never another running
/// copy's live sidecar: that one also fails our probe (different
/// secret), but its parent shell is alive — that case ends on the
/// held-port screen.
/// SIGKILL the sidecar server process that still holds `port` after its
/// bootloader was killed (the one-file bootloader is a separate process;
/// killing it alone leaves the server listening). Only a process named
/// bpp-server is touched.
pub(crate) fn kill_sidecar_listener(port: u16) {
    let Some(pid) = listener_pid(port) else {
        return;
    };
    let name = process_name(pid).unwrap_or_default();
    if !name.ends_with("bpp-server") && !name.ends_with("bpp-server.exe") {
        eprintln!("[sidecar] port {} is held by pid {} ({}); leaving it alone", port, pid, name);
        return;
    }
    eprintln!("[sidecar] killing orphaned server pid {} on port {}", pid, port);
    let _ = std::process::Command::new("kill").args(["-KILL", &pid.to_string()]).status();
}

pub(crate) fn reclaim_port_from_stale_sidecar(port: u16) {
    if !port_is_held(port) || server_knows_boot_secret(port) {
        return;
    }
    let Some(pid) = listener_pid(port) else {
        return;
    };
    let name = process_name(pid).unwrap_or_default();
    if !name.ends_with("bpp-server") && !name.ends_with("bpp-server.exe") {
        eprintln!(
            "[startup] port {} is held by pid {} ({}); leaving it alone",
            port, pid, name
        );
        return;
    }
    if !sidecar_is_orphaned(pid) {
        eprintln!(
            "[startup] port {} is held by bpp-server pid {} whose shell is still running; leaving it alone",
            port, pid
        );
        return;
    }
    eprintln!("[startup] port {} is held by an orphaned bpp-server (pid {}); stopping it", port, pid);
    #[cfg(unix)]
    {
        let _ = std::process::Command::new("kill").arg(pid.to_string()).status();
    }
    #[cfg(windows)]
    {
        let _ = std::process::Command::new("taskkill")
            .args(["/PID", &pid.to_string(), "/F"])
            .status();
    }
    let start = std::time::Instant::now();
    while start.elapsed() < Duration::from_secs(3) && port_is_held(port) {
        std::thread::sleep(Duration::from_millis(100));
    }
}

/// The pid listening on 127.0.0.1:port, via `lsof` (macOS / Linux).
fn listener_pid(port: u16) -> Option<u32> {
    #[cfg(unix)]
    {
        let out = std::process::Command::new("lsof")
            .args(["-nP", "-ti", &format!("tcp:{}", port), "-sTCP:LISTEN"])
            .output()
            .ok()?;
        let text = String::from_utf8_lossy(&out.stdout);
        return text.lines().find_map(|l| l.trim().parse::<u32>().ok());
    }
    #[cfg(not(unix))]
    {
        let _ = port;
        None
    }
}

/// The command name of a pid (`ps -o comm=`), when it can be read.
fn process_name(pid: u32) -> Option<String> {
    #[cfg(unix)]
    {
        let out = std::process::Command::new("ps")
            .args(["-o", "comm=", "-p", &pid.to_string()])
            .output()
            .ok()?;
        let name = String::from_utf8_lossy(&out.stdout).trim().to_string();
        return if name.is_empty() { None } else { Some(name) };
    }
    #[cfg(not(unix))]
    {
        let _ = pid;
        None
    }
}

/// Is the listening sidecar an orphan — its shell gone? The one-file
/// PyInstaller build runs as a bootloader parent plus the child that
/// binds the port, so the chain is walked past every ancestor that is
/// itself `bpp-server`; the first other ancestor must be launchd (pid 1).
fn sidecar_is_orphaned(pid: u32) -> bool {
    let mut cur = pid;
    for _ in 0..4 {
        let Some(parent) = parent_pid(cur) else {
            return false;
        };
        if parent == 1 {
            return true;
        }
        let name = process_name(parent).unwrap_or_default();
        if !name.ends_with("bpp-server") && !name.ends_with("bpp-server.exe") {
            return false;
        }
        cur = parent;
    }
    false
}

/// The parent of a process (1 is launchd / init).
fn parent_pid(pid: u32) -> Option<u32> {
    #[cfg(unix)]
    {
        let out = std::process::Command::new("ps")
            .args(["-o", "ppid=", "-p", &pid.to_string()])
            .output()
            .ok()?;
        return String::from_utf8_lossy(&out.stdout).trim().parse::<u32>().ok();
    }
    #[cfg(not(unix))]
    {
        let _ = pid;
        None
    }
}

pub(crate) fn server_is_alive(port: u16) -> bool {
    let addr = format!("127.0.0.1:{}", port);
    std::net::TcpStream::connect_timeout(
        &addr.parse().unwrap(),
        Duration::from_secs(2),
    )
    .is_ok()
}

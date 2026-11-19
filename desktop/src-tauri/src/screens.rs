//! The screens the shell draws into the webview itself: while the
//! sidecar restarts, when it cannot, when the library needs a newer
//! build, when the server refused to open the library, and when it
//! never came up at all.
//!
//! Every screen is built by one script (`SCREEN_JS`) from a plain
//! description. Its elements are made with DOM calls and styled through
//! `element.style`: the pages they are drawn on refuse inline `style`
//! attributes and `<style>` elements (the splash page's policy carries
//! hashes, which turn `unsafe-inline` off; the server's page has none),
//! and every text is set as text, never as markup.

use serde_json::{json, Value};
use tauri::{AppHandle, Manager};

const BUTTON_CSS: &str =
    "padding:8px 20px;border:none;border-radius:6px;font-size:14px;cursor:pointer;";
const PRIMARY: &str = "background:#4a9eff;color:#fff";
const SECONDARY: &str = "background:rgba(255,255,255,0.15);color:#fff";
const QUIET: &str = "background:rgba(255,255,255,0.1);color:rgba(255,255,255,0.6)";

/// Draws a screen from its description `s`: `title`, `title_css`,
/// `lines` ([{text, css}]), `buttons` ([{label, css, cmd, args} or
/// {label, css, reload: true}]), `spinner`. With `overlay` it is drawn
/// in a dimmed layer over the page (kept as it is when `keep` and it is
/// already there), else it replaces the page.
const SCREEN_JS: &str = r#"(function(s){
  var make=function(tag,css,text){var e=document.createElement(tag);e.style.cssText=css||'';if(text!==undefined)e.textContent=text;return e};
  var inv=function(c,a){window.__TAURI__&&window.__TAURI__.core&&window.__TAURI__.core.invoke(c,a)};
  var host;
  if(s.overlay){
    host=document.getElementById('bpp-reconnect-overlay');
    if(host&&s.keep)return;
    if(!host){
      host=make('div','position:fixed;inset:0;z-index:99999;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,0.7);font-family:system-ui;color:#fff;backdrop-filter:blur(4px)');
      host.id='bpp-reconnect-overlay';document.body.appendChild(host);
    }
    host.replaceChildren();
  }else{
    document.body.replaceChildren();
    document.body.style.cssText='margin:0;display:block;background:#111;color:#ccc;font-family:system-ui';
    host=make('div','display:flex;align-items:center;justify-content:center;width:100%;height:100vh');
    document.body.appendChild(host);
  }
  var box=make('div','text-align:center;max-width:520px;padding:0 24px');
  if(s.spinner){
    var sp=make('div','width:32px;height:32px;border:3px solid rgba(255,255,255,0.3);border-top-color:#fff;border-radius:50%;margin:0 auto 16px');
    if(sp.animate)sp.animate([{transform:'rotate(0deg)'},{transform:'rotate(360deg)'}],{duration:800,iterations:Infinity});
    box.appendChild(sp);
  }
  box.appendChild(make('h2','margin:0 0 12px;font-size:20px;color:#fff;'+(s.title_css||''),s.title));
  (s.lines||[]).forEach(function(l){box.appendChild(make('p','margin:0 0 12px;font-size:14px;line-height:1.5;overflow-wrap:anywhere;'+(l.css||''),l.text))});
  var row=make('div','display:flex;gap:8px;justify-content:center;flex-wrap:wrap;margin-top:12px');
  (s.buttons||[]).forEach(function(b){
    var e=make('button',b.css,b.label);
    e.addEventListener('click',function(){
      if(b.reload){if(typeof window.reloadSafely==='function'){window.reloadSafely()}else{location.reload()}}
      else{inv(b.cmd,b.args)}
    });
    row.appendChild(e);
  });
  box.appendChild(row);host.appendChild(box);
})"#;

/// The script that draws the screen described by `spec`.
pub(crate) fn screen_js(spec: &Value) -> String {
    format!("{}({});", SCREEN_JS, spec)
}

fn show(handle: &AppHandle, spec: Value) {
    if let Some(window) = handle.get_webview_window("main") {
        let _ = window.eval(&screen_js(&spec));
    }
}

fn button(label: &str, look: &str, cmd: &str) -> Value {
    json!({"label": label, "css": format!("{}{}", BUTTON_CSS, look), "cmd": cmd})
}

fn link_button(label: &str, look: &str, url: &str) -> Value {
    json!({"label": label, "css": format!("{}{}", BUTTON_CSS, look), "cmd": "open_url", "args": {"url": url}})
}

/// Show a "reconnecting" overlay in the webview.
pub(crate) fn show_reconnecting_overlay(handle: &AppHandle) {
    show(
        handle,
        json!({
            "overlay": true,
            "keep": true,
            "spinner": true,
            "title": "Server disconnected",
            "title_css": "font-size:18px;margin:0 0 8px",
            "lines": [{"text": "Reconnecting\u{2026}", "css": "color:rgba(255,255,255,0.7)"}],
            "buttons": [
                {"label": "Reload", "css": format!("{}{}", BUTTON_CSS, PRIMARY), "reload": true},
                button("Quit", SECONDARY, "exit_app"),
            ],
        }),
    );
}

/// Show a fatal error screen when all respawn attempts are exhausted.
pub(crate) fn show_fatal_error(handle: &AppHandle) {
    show(
        handle,
        json!({
            "overlay": true,
            "title": "Server could not restart",
            "title_css": "color:#ff6b6b",
            "lines": [{"text": "The server failed to restart after several attempts.", "css": "color:rgba(255,255,255,0.7)"}],
            "buttons": [
                button("Quit", PRIMARY, "exit_app"),
                button("Try Again", SECONDARY, "restart_app"),
                link_button("Report Issue", QUIET, "https://github.com/Arkalogy/best-photo-picker/issues/new?template=bug_report.md&title=Server+failed+to+restart"),
            ],
        }),
    );
}

/// Show a tailored "update required" screen when the sidecar refused to
/// open the library because its schema is newer than this build understands.
/// This replaces the generic "Server failed to start" timeout screen for
/// that specific, deterministic case — retrying won't help; updating will.
/// Reassure the user their photos are safe (the guard exists to protect them).
pub(crate) fn show_update_required(handle: &AppHandle) {
    show(
        handle,
        json!({
            "title": "Update Best Photo Picker",
            "lines": [
                {"text": "This photo library was created by a newer version of Best Photo Picker, so this older app won't open it.", "css": "color:rgba(255,255,255,0.65)"},
                {"text": "Your photos and library are safe \u{2014} the app stopped on purpose to avoid damaging them. Update to the latest version to continue.", "css": "font-size:13px;color:rgba(255,255,255,0.45)"},
            ],
            "buttons": [
                link_button("Get the latest version", PRIMARY, "https://github.com/Arkalogy/best-photo-picker/releases/latest"),
                button("Quit", SECONDARY, "exit_app"),
            ],
        }),
    );
}

/// The server refused to open the library and said why (another bpp
/// holds it, a link where one of its folders should be, a damaged
/// database): that reason, word for word, instead of a restart loop
/// and "Server could not restart".
pub(crate) fn show_library_refused(handle: &AppHandle, reason: &str) {
    let reason = if reason.is_empty() {
        "The server stopped without saying why."
    } else {
        reason
    };
    let mut lines = vec![
        json!({"text": reason, "css": "color:rgba(255,255,255,0.75)"}),
        json!({"text": "The app stopped on purpose; nothing in the library was changed. Fix what it says, then click Try Again.", "css": "font-size:13px;color:rgba(255,255,255,0.45)"}),
    ];
    if let Some(log) = crate::sidecar::refusal_log_path(handle) {
        lines.push(json!({
            "text": format!("This message is also saved in {}.", log.display()),
            "css": "font-size:12px;color:rgba(255,255,255,0.35)",
        }));
    }
    show(
        handle,
        json!({
            "title": "This library can't be opened",
            "lines": lines,
            "buttons": [button("Try Again", PRIMARY, "restart_app"), button("Quit", SECONDARY, "exit_app")],
        }),
    );
}

/// Remove the reconnecting overlay (server is back).
pub(crate) fn hide_reconnecting_overlay(handle: &AppHandle) {
    if let Some(window) = handle.get_webview_window("main") {
        let _ = window.eval(
            "var o=document.getElementById('bpp-reconnect-overlay');if(o)o.remove();"
        );
    }
}

/// The start-up timeout screen (after `SERVER_START_TIMEOUT_SECS`). ``port_held``: something
/// else answers on the port (a `bpp serve`, a sidecar we could not
/// reclaim), so the message names it and Try Again restarts the app.
pub(crate) fn show_start_failure(handle: &AppHandle, port_held: bool) {
    let (title, detail) = if port_held {
        (
            "Another copy is already running",
            "Something else is using port 5001 on this Mac: another copy of Best Photo Picker, or `bpp serve` in a Terminal window. Quit it, then click Try Again.".to_string(),
        )
    } else {
        (
            "Server failed to start",
            format!(
                "Best Photo Picker server did not respond within {}.",
                crate::boot::SERVER_START_TIMEOUT_TEXT
            ),
        )
    };
    show(
        handle,
        json!({
            "title": title,
            "lines": [{"text": detail, "css": "color:rgba(255,255,255,0.55)"}],
            "buttons": [
                button("Try Again", PRIMARY, "restart_app"),
                button("Quit", SECONDARY, "exit_app"),
                link_button("Report Issue", QUIET, "https://github.com/Arkalogy/best-photo-picker/issues/new?template=bug_report.md&title=Server+failed+to+start"),
            ],
        }),
    );
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_screen_uses_no_markup_and_no_inline_style_attributes() {
        let js = screen_js(&json!({"title": "<b>t</b>", "lines": [{"text": "x\"y'</p>"}]}));
        assert!(!js.contains("innerHTML"), "texts are set as text");
        assert!(!js.contains("style=\""), "no style attributes: the pages refuse them");
        assert!(js.contains("\"<b>t</b>\""), "the title travels as a JSON string");
    }
}

//! The screens the shell draws into the webview itself: while the
//! sidecar restarts, when it cannot, when the library needs a newer
//! build, and when it never came up at all.

use tauri::{AppHandle, Manager};

/// Show a "reconnecting" overlay in the webview.
pub(crate) fn show_reconnecting_overlay(handle: &AppHandle) {
    if let Some(window) = handle.get_webview_window("main") {
        let _ = window.eval(
            r#"(function(){
  if(document.getElementById('bpp-reconnect-overlay'))return;
  var o=document.createElement('div');o.id='bpp-reconnect-overlay';
  o.style.cssText='position:fixed;inset:0;z-index:99999;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,0.7);font-family:system-ui;color:#fff;backdrop-filter:blur(4px)';
  o.innerHTML='<div style="text-align:center"><div style="width:32px;height:32px;border:3px solid rgba(255,255,255,0.3);border-top-color:#fff;border-radius:50%;margin:0 auto 16px;animation:bpp-spin 0.8s linear infinite"></div><h2 style="margin:0 0 8px;font-size:18px">Server disconnected</h2><p style="margin:0 0 16px;color:rgba(255,255,255,0.7);font-size:14px">Reconnecting\u2026</p><div style="display:flex;gap:8px;justify-content:center"><button id="bpp-ov-reload" style="padding:8px 20px;background:#4a9eff;color:#fff;border:none;border-radius:6px;font-size:14px;cursor:pointer">Reload</button><button id="bpp-ov-quit" style="padding:8px 20px;background:rgba(255,255,255,0.15);color:#fff;border:none;border-radius:6px;font-size:14px;cursor:pointer">Quit</button></div></div>';
  var s=document.createElement('style');s.textContent='@keyframes bpp-spin{to{transform:rotate(360deg)}}';
  document.head.appendChild(s);document.body.appendChild(o);
  // Listeners, not inline handlers: the page's CSP has no unsafe-inline.
  o.querySelector('#bpp-ov-reload').addEventListener('click',function(){if(typeof window.reloadSafely==='function'){window.reloadSafely()}else{location.reload()}});
  o.querySelector('#bpp-ov-quit').addEventListener('click',function(){window.__TAURI__&&window.__TAURI__.core&&window.__TAURI__.core.invoke('exit_app')});
})()"#
        );
    }
}

/// Show a fatal error screen when all respawn attempts are exhausted.
pub(crate) fn show_fatal_error(handle: &AppHandle) {
    if let Some(window) = handle.get_webview_window("main") {
        let _ = window.eval(
            r#"(function(){
  var o=document.getElementById('bpp-reconnect-overlay');
  var btn='padding:8px 20px;border:none;border-radius:6px;font-size:14px;cursor:pointer';
  if(o)o.innerHTML='<div style="text-align:center;max-width:400px"><h2 style="margin:0 0 12px;font-size:20px;color:#ff6b6b">Server could not restart</h2><p style="margin:0 0 20px;color:rgba(255,255,255,0.7);font-size:14px">The server failed to restart after several attempts.</p><div style="display:flex;gap:8px;justify-content:center;flex-wrap:wrap"><button id="bpp-ov-quit" style="'+btn+';background:#4a9eff;color:#fff">Quit</button><button id="bpp-ov-retry" style="'+btn+';background:rgba(255,255,255,0.15);color:#fff">Try Again</button><button id="bpp-ov-report" style="'+btn+';background:rgba(255,255,255,0.1);color:rgba(255,255,255,0.7)">Report Issue</button></div></div>';
  if(!o)return;
  var inv=function(cmd,args){window.__TAURI__&&window.__TAURI__.core&&window.__TAURI__.core.invoke(cmd,args)};
  o.querySelector('#bpp-ov-quit').addEventListener('click',function(){inv('exit_app')});
  o.querySelector('#bpp-ov-retry').addEventListener('click',function(){inv('restart_app')});
  o.querySelector('#bpp-ov-report').addEventListener('click',function(){inv('open_url',{url:'https://github.com/Arkalogy/best-photo-picker/issues/new?template=bug_report.md&title=Server+failed+to+restart'})});
})()"#
        );
    }
}

/// Show a tailored "update required" screen when the sidecar refused to
/// open the library because its schema is newer than this build understands.
/// This replaces the generic "Server failed to start" timeout screen for
/// that specific, deterministic case — retrying won't help; updating will.
/// Reassure the user their photos are safe (the guard exists to protect them).
pub(crate) fn show_update_required(handle: &AppHandle) {
    if let Some(window) = handle.get_webview_window("main") {
        let _ = window.eval(concat!(
            "document.body.innerHTML='<div style=\"display:flex;align-items:center;justify-content:center;height:100vh;font-family:system-ui;background:#111;color:#ccc\">",
            "<div style=\"text-align:center;max-width:440px;padding:0 24px\">",
            "<h2 style=\"margin:0 0 8px;font-size:20px;color:#fff\">Update Best Photo Picker</h2>",
            "<p style=\"margin:0 0 12px;font-size:14px;line-height:1.5;color:rgba(255,255,255,0.65)\">This photo library was created by a newer version of Best Photo Picker, so this older app won&#39;t open it.</p>",
            "<p style=\"margin:0 0 24px;font-size:13px;color:rgba(255,255,255,0.45)\">Your photos and library are safe &mdash; the app stopped on purpose to avoid damaging them. Update to the latest version to continue.</p>",
            "<div style=\"display:flex;gap:8px;justify-content:center;flex-wrap:wrap\">",
            "<button id=\"bpp-up-get\" style=\"padding:8px 20px;background:#4a9eff;color:#fff;border:none;border-radius:6px;font-size:14px;cursor:pointer\">Get the latest version</button>",
            "<button id=\"bpp-up-quit\" style=\"padding:8px 20px;background:rgba(255,255,255,0.15);color:#fff;border:none;border-radius:6px;font-size:14px;cursor:pointer\">Quit</button>",
            "</div></div></div>';",
            // Listeners, not inline handlers: this screen replaces the loaded
            // page's body, and that page's CSP has no unsafe-inline.
            "var inv=function(c,a){window.__TAURI__&&window.__TAURI__.core&&window.__TAURI__.core.invoke(c,a)};",
            "document.getElementById('bpp-up-get').addEventListener('click',function(){inv('open_url',{url:'https://github.com/Arkalogy/best-photo-picker/releases/latest'})});",
            "document.getElementById('bpp-up-quit').addEventListener('click',function(){inv('exit_app')});"
        ));
    }
}

/// Remove the reconnecting overlay (server is back).
pub(crate) fn hide_reconnecting_overlay(handle: &AppHandle) {
    if let Some(window) = handle.get_webview_window("main") {
        let _ = window.eval(
            "var o=document.getElementById('bpp-reconnect-overlay');if(o)o.remove();"
        );
    }
}

/// The 30-second start-up timeout screen. ``port_held``: something
/// else answers on the port (a `bpp serve`, a sidecar we could not
/// reclaim), so the message names it and Try Again restarts the app.
pub(crate) fn show_start_failure(handle: &AppHandle, port_held: bool) {
        let (title, detail) = if port_held {
            (
                "Another copy is already running",
                "Something else is using port 5001 on this Mac: another copy of Best Photo Picker, or `bpp serve` in a Terminal window. Quit it, then click Try Again.",
            )
        } else {
            (
                "Server failed to start",
                "Best Photo Picker server did not respond within 30 seconds.",
            )
        };
        if let Some(window) = handle.get_webview_window("main") {
            let _ = window.eval(&format!(concat!(
                "document.body.innerHTML='<div style=\"display:flex;align-items:center;justify-content:center;height:100vh;font-family:system-ui;background:#111;color:#ccc\">",
                "<div style=\"text-align:center;max-width:400px\">",
                "<h2 style=\"margin:0 0 8px;font-size:20px;color:#fff\">{}</h2>",
                "<p style=\"margin:0 0 24px;font-size:14px;color:rgba(255,255,255,0.55)\">{}</p>",
                "<div style=\"display:flex;gap:8px;justify-content:center;flex-wrap:wrap\">",
                "<button id=\"bpp-sf-retry\" style=\"padding:8px 20px;background:#4a9eff;color:#fff;border:none;border-radius:6px;font-size:14px;cursor:pointer\">Try Again</button>",
                "<button id=\"bpp-sf-quit\" style=\"padding:8px 20px;background:rgba(255,255,255,0.15);color:#fff;border:none;border-radius:6px;font-size:14px;cursor:pointer\">Quit</button>",
                "<button id=\"bpp-sf-report\" style=\"padding:8px 20px;background:rgba(255,255,255,0.1);color:rgba(255,255,255,0.6);border:none;border-radius:6px;font-size:14px;cursor:pointer\">Report Issue</button>",
                "</div></div></div>';",
                // Listeners, not inline handlers: this screen replaces the
                // loaded page's body, and that page's CSP has no unsafe-inline.
                "var inv=function(c,a){{window.__TAURI__&&window.__TAURI__.core&&window.__TAURI__.core.invoke(c,a)}};",
                "document.getElementById('bpp-sf-retry').addEventListener('click',function(){{inv('restart_app')}});",
                "document.getElementById('bpp-sf-quit').addEventListener('click',function(){{inv('exit_app')}});",
                "document.getElementById('bpp-sf-report').addEventListener('click',function(){{inv('open_url',{{url:'https://github.com/Arkalogy/best-photo-picker/issues/new?template=bug_report.md&title=Server+failed+to+start'}})}});"
            ), title, detail));
        }
}

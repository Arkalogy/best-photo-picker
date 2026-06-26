// @ts-check
/**
 * Auth-aware HTTP plumbing used by every other JS file in the app.
 *
 * The server renders an auth token into <meta name="auth-token"> on the
 * shell template. Every API call goes through these helpers so the
 * token is attached consistently — as a header for fetch/EventSource
 * and as a `?_token=` query param for <img src> URLs (which can't send
 * headers).
 *
 * Bridged onto window via index.html's module bootstrap so existing
 * script-tag callers (`apiFetch`, `authedSrc`, `authEventSource`)
 * keep working unchanged.
 */

/**
 * Read the auth token at module-load time. The meta tag is rendered
 * server-side into the shell HTML, so it's already in the DOM by the
 * time this module's import is evaluated.
 *
 * Empty string fallback means "no auth required" — happens in tests
 * and in dev mode where the gate is disabled.
 */
export const _authToken =
  document.querySelector('meta[name="auth-token"]')?.getAttribute("content") || "";

/**
 * Append the auth token to a URL for use in `<img src>` / `<video src>`
 * — those tags can't send custom headers, so the token rides as a
 * query param.
 *
 * No-op when the token is empty (test / dev mode).
 *
 * @param {string} url
 * @returns {string}
 */
export function authedSrc(url) {
  if (!_authToken) return url;
  const sep = url.includes("?") ? "&" : "?";
  return url + sep + "_token=" + encodeURIComponent(_authToken);
}

/**
 * Auth-aware JSON fetch with a default 30s timeout and structured
 * error throwing.
 *
 * On non-2xx, throws an Error with `.status` and `.body` populated
 * from the JSON response body. Callers can `catch (e) { if (e.status
 * === 404) ... }` to branch.
 *
 * @param {string} url
 * @param {RequestInit & { signal?: AbortSignal }} [opts]
 * @returns {Promise<any>}
 */
/** Tracks whether we've already triggered a session-lost reload, to
 * avoid a reload-storm if many in-flight requests all 403 at once. */
let _sessionLostHandled = false;

export async function apiFetch(url, opts) {
  opts = opts || {};
  if (!opts.signal) opts.signal = AbortSignal.timeout(30000);
  opts.headers = Object.assign({ "X-Auth-Token": _authToken }, opts.headers || {});
  const resp = await fetch(url, opts);
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    // 403 on a LAN-loaded page = trust state changed (revoked, share
    // disabled, token rotated). Reload → server serves the appropriate
    // page (pair.html with revoked state, or full SPA if still trusted).
    // Only fires once to avoid a storm if many in-flight calls all 403.
    if (resp.status === 403 && !_sessionLostHandled) {
      _sessionLostHandled = true;
      // Tiny delay so any cleanup tasks (timers, abort signals) settle.
      setTimeout(() => {
        try { window.location.reload(); } catch { /* fallback no-op */ }
      }, 50);
    }
    const err = /** @type {Error & {status?: number, body?: any}} */ (
      new Error(body.error || `HTTP ${resp.status}`)
    );
    err.status = resp.status;
    err.body = body;
    throw err;
  }
  return resp.json();
}

/**
 * Open an authenticated Server-Sent Events connection.
 *
 * EventSource can't send headers, so the token rides as `?_token=`.
 *
 * @param {string} url
 * @returns {EventSource}
 */
export function authEventSource(url) {
  const sep = url.includes("?") ? "&" : "?";
  return new EventSource(url + sep + "_token=" + encodeURIComponent(_authToken));
}

// Trust-state recovery for media routes.
//
// apiFetch already reloads on 403 — but only triggers when the JS code
// makes an explicit fetch. If a phone gets revoked while only <img> /
// <video> requests are in flight (e.g. user is just scrolling thumbs),
// the page sits there with broken-image icons because the browser
// itself can't tell us the network failed.
//
// Fix: listen for media-element load failures globally. When one fires
// for a /thumb /photo /video URL, probe /api/status. If trust really is
// gone, that probe 403s and apiFetch's existing handler does the reload.
// If it returns 200, the failure was something else (file missing,
// network blip) and we leave the broken-icon alone.
//
// Throttled to one probe per 5s so a grid full of broken thumbs doesn't
// fire a probe storm.
let _lastMediaProbeAt = 0;

/**
 * @param {Event} event
 */
function _onMediaError(event) {
  if (_sessionLostHandled) return;
  const target = /** @type {any} */ (event.target);
  if (!target || !target.src) return;
  const tag = target.tagName;
  if (tag !== "IMG" && tag !== "VIDEO") return;
  const url = String(target.src);
  if (!/\/(thumb|photo|video)\//.test(url)) return;
  const now = Date.now();
  if (now - _lastMediaProbeAt < 5000) return;
  _lastMediaProbeAt = now;
  // apiFetch will throw on 403 — and side-effect the reload handler.
  // Swallow any error since we only care about the side effect.
  apiFetch("/api/v1/status").catch(() => {});
}

if (typeof document !== "undefined") {
  // useCapture=true: <img> / <video> error events don't bubble, so the
  // listener has to ride the capture phase to see them.
  document.addEventListener("error", _onMediaError, true);
}

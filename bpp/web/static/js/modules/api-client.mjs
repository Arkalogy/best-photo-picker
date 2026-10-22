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

import { reloadSafely } from "./boot-proof.mjs";
import { toastError } from "./toast.mjs";

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
 * The library this page was rendered for. Every request carries it, so
 * a tab left open across a library switch cannot act on the other
 * library's rows (the server answers 409 instead).
 */
export const _libraryId =
  document.querySelector('meta[name="library-id"]')?.getAttribute("content") || "";

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
 * The (authed) URL of one face's crop: built here, nowhere else.
 * @param {string} thumbHash
 * @param {number | string} faceIndex
 * @returns {string}
 */
export function faceCropUrl(thumbHash, faceIndex) {
  return authedSrc(
    `/api/v1/faces/crop/${encodeURIComponent(thumbHash)}/${encodeURIComponent(String(faceIndex))}`
  );
}

/**
 * The pet crop URL for a detection, the counterpart of faceCropUrl.
 * @param {string} thumbHash
 * @param {number|string} detectionIndex
 */
export function petCropUrl(thumbHash, detectionIndex) {
  return authedSrc(
    `/api/v1/pets/crop/${encodeURIComponent(thumbHash)}/${encodeURIComponent(String(detectionIndex))}`
  );
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

/** Timeout for requests that do a whole-library pass (recompute, recluster). */
export const LONG_OP_TIMEOUT_MS = 10 * 60 * 1000;

export async function apiFetch(url, opts) {
  opts = opts || {};
  if (!opts.signal) opts.signal = AbortSignal.timeout(30000);
  opts.headers = Object.assign(
    { "X-Auth-Token": _authToken },
    _libraryId ? { "X-Library-Id": _libraryId } : {},
    opts.headers || {}
  );
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
      // The reload carries the boot key only to a listener that proved
      // it is this app's server (see boot-proof.mjs): a 403 is also what
      // a stranger on the port would answer.
      setTimeout(() => {
        reloadSafely();
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
export function _onMediaError(event) {
  if (_sessionLostHandled) return;
  const target = /** @type {any} */ (event.target);
  if (!target || !target.src) return;
  const tag = target.tagName;
  if (tag !== "IMG" && tag !== "VIDEO") return;
  const url = String(target.src);
  if (!/\/(thumb|photo|video|crop)\//.test(url)) return;
  if (tag === "IMG" && (/\/photo\//.test(url) || _CROP_URL.test(url))) explainPhotoFailure(url);
  const now = Date.now();
  if (now - _lastMediaProbeAt < 5000) return;
  _lastMediaProbeAt = now;
  // apiFetch will throw on 403 — and side-effect the reload handler.
  // Swallow any error since we only care about the side effect.
  apiFetch("/api/v1/status").catch(() => {});
}

/** Full-size photos (or a photo's crops) whose load failure was already explained this session. */
const _explainedPhotos = new Set();
/** A face or pet crop URL: `/api/v1/faces/crop/<hash>/<index>` or `/api/v1/pets/crop/<hash>/<index>`. */
const _CROP_URL = /\/api\/v1\/(faces|pets)\/crop\//;

/**
 * Explain why a full-size photo failed to load, once per photo: an
 * `<img>` can't show the server's error body, so ask again and toast its
 * reason + recovery (e.g. "RAW support isn't available — reinstall the
 * desktop app"). Silent when the second try works (a network blip — the
 * image isn't downloaded again, and a later real failure still gets
 * explained) or when trust was lost (the status probe's reload handles it).
 *
 * @param {string} url - the failed `<img>` src
 */
export async function explainPhotoFailure(url) {
  const base = typeof location !== "undefined" && location.origin ? location.origin : "http://localhost";
  const u = new URL(url, base);
  // Crops fail together (an unplugged drive, RAW support missing: every
  // person card at once): explain face crops once per session and pet
  // crops once, not once per card.
  const crop = _CROP_URL.exec(u.pathname);
  const key = crop ? `crop:${crop[1]}` : u.pathname;
  const action = crop ? (crop[1] === "faces" ? "show this face" : "show this pet") : "show this photo";
  if (_explainedPhotos.has(key)) return;
  _explainedPhotos.add(key);
  let resp;
  try {
    resp = await fetch(u.pathname + u.search, {
      headers: { "X-Auth-Token": _authToken },
      signal: AbortSignal.timeout(30000),
    });
  } catch {
    _explainedPhotos.delete(key); // offline / timed out: nothing to explain yet
    return;
  }
  if (resp.ok) {
    _explainedPhotos.delete(key);
    resp.body?.cancel().catch(() => {});
    return;
  }
  if (resp.status === 403) return;
  const body = await resp.json().catch(() => ({}));
  const err = /** @type {Error & {status?: number, body?: any}} */ (
    new Error(body.error || `HTTP ${resp.status}`)
  );
  err.status = resp.status;
  err.body = body;
  toastError(action, err, body.context?.recovery || "try again");
}

if (typeof document !== "undefined") {
  // useCapture=true: <img> / <video> error events don't bubble, so the
  // listener has to ride the capture phase to see them.
  document.addEventListener("error", _onMediaError, true);
}

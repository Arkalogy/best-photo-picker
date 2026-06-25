// @ts-check
/**
 * View-aware fetch guard.
 *
 * The app keeps `window.currentView` as a string identifying the active
 * view (library / album / people / calendar / etc.). Loaders kick off
 * `apiFetch(...)` calls and write results back to DOM/globals when they
 * resolve. If the user switches views mid-fetch, those late writes
 * paint stale data into the new view's containers.
 *
 * This module exposes:
 *
 *  - `installViewGuard()` — installs a setter on `window.currentView` so
 *    every assignment bumps an internal token and aborts the shared
 *    AbortController. Idempotent; safe to call multiple times.
 *
 *  - `viewFetch(url, opts)` — drop-in replacement for `apiFetch` that
 *    captures the current view token at call time, attaches the per-view
 *    AbortSignal to the fetch, and returns `null` if (a) the request was
 *    aborted by a view switch, or (b) the response arrived after the
 *    view changed. Callers do `const data = await viewFetch(...); if
 *    (!data) return;` and stay race-safe with no further bookkeeping.
 *
 *  - `currentViewToken()` / `viewStillCurrent(token)` — escape hatches for
 *    loaders that do work *after* a non-fetch await (e.g. image decode,
 *    setTimeout) and need a manual guard.
 *
 * The 30s default-timeout behaviour of `apiFetch` is preserved via
 * `AbortSignal.any([viewSignal, AbortSignal.timeout(30_000)])`.
 */

import { apiFetch } from "./api-client.mjs";

let _viewToken = 0;
/** @type {AbortController | null} */
let _controller = null;

function _bump() {
  _viewToken += 1;
  if (_controller && !_controller.signal.aborted) {
    _controller.abort();
  }
  _controller = null;
}

function _activeController() {
  if (!_controller || _controller.signal.aborted) {
    _controller = new AbortController();
  }
  return _controller;
}

/** @returns {number} */
export function currentViewToken() {
  return _viewToken;
}

/**
 * @param {number} token
 * @returns {boolean}
 */
export function viewStillCurrent(token) {
  return token === _viewToken;
}

/**
 * Wrap `apiFetch` so a mid-flight view switch silently bails the caller.
 * Returns `null` on abort or stale response; throws on real HTTP errors
 * exactly like `apiFetch`.
 *
 * @param {string} url
 * @param {RequestInit & { timeoutMs?: number }} [opts]
 * @returns {Promise<any | null>}
 */
export async function viewFetch(url, opts) {
  const token = _viewToken;
  const viewSignal = _activeController().signal;
  const timeoutMs = (opts && opts.timeoutMs) ?? 30000;
  const signals = [viewSignal];
  if (typeof AbortSignal !== "undefined" && typeof AbortSignal.timeout === "function") {
    signals.push(AbortSignal.timeout(timeoutMs));
  }
  // @ts-ignore — AbortSignal.any is widely available in Chromium/WebKit 2023+
  const signal = AbortSignal.any ? AbortSignal.any(signals) : viewSignal;
  const merged = { ...(opts || {}), signal };
  // Strip our extension before forwarding to apiFetch.
  delete /** @type {any} */ (merged).timeoutMs;
  try {
    const data = await apiFetch(url, merged);
    return viewStillCurrent(token) ? data : null;
  } catch (err) {
    if (err && (err.name === "AbortError" || err.name === "TimeoutError")) return null;
    throw err;
  }
}

/**
 * Install the property setter that ties `window.currentView` writes to
 * the internal token. Idempotent.
 *
 * @param {Window & typeof globalThis} [win]
 */
export function installViewGuard(win) {
  const w = /** @type {any} */ (win || globalThis);
  if (w.__viewGuardInstalled) return;
  let view = w.currentView ?? "library";
  Object.defineProperty(w, "currentView", {
    configurable: true,
    enumerable: true,
    get() {
      return view;
    },
    set(next) {
      if (next !== view) {
        view = next;
        _bump();
      } else {
        view = next;
      }
    },
  });
  w.__viewGuardInstalled = true;
}

/**
 * Test-only reset. Not exported through the runtime bridge.
 */
export function _resetForTests() {
  _viewToken = 0;
  if (_controller && !_controller.signal.aborted) _controller.abort();
  _controller = null;
}

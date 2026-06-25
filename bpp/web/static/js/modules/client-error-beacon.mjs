// @ts-check
/**
 * Beacon uncaught client-side JS errors to the server so they land in the
 * Activity log (Settings → Activity) instead of only the browser console.
 *
 * Hardened against the obvious self-inflicted failures, because this runs
 * from inside the global error boundary:
 *   - NEVER throws or rejects (raw fetch + `.catch`, wrapped in try/catch),
 *     so it can't re-enter window.onerror / unhandledrejection in a loop.
 *   - Throttled + deduped: at most BEACON_MAX_PER_WINDOW in BEACON_WINDOW_MS,
 *     and an exact repeat of the previous message is dropped — a render-loop
 *     bug can't flood the log or hammer the endpoint.
 *   - Raw fetch (not apiFetch): apiFetch throws on non-2xx and runs a 403
 *     session-lost reload, neither of which is wanted here.
 */

export const BEACON_WINDOW_MS = 10000;
export const BEACON_MAX_PER_WINDOW = 5;

/** @type {number[]} */
let _times = [];
let _lastMsg = "";

/** Test-only: reset the throttle state between cases. */
export function _resetBeaconThrottle() {
  _times = [];
  _lastMsg = "";
}

/**
 * @param {{message: string, source?: string, lineno?: number, colno?: number, stack?: string}} payload
 * @param {() => number} [now] - injectable clock for tests.
 * @returns {boolean} whether a beacon was sent (false = throttled/deduped/failed-guard)
 */
export function beaconClientError(payload, now = Date.now) {
  try {
    const t = now();
    _times = _times.filter((x) => t - x < BEACON_WINDOW_MS);
    if (payload.message === _lastMsg && _times.length > 0) return false; // dedupe repeat
    if (_times.length >= BEACON_MAX_PER_WINDOW) return false; // storm cap
    _times.push(t);
    _lastMsg = payload.message;
    const token =
      document.querySelector('meta[name="auth-token"]')?.getAttribute("content") || "";
    fetch("/api/v1/client-error", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Auth-Token": token },
      body: JSON.stringify(payload),
      keepalive: true,
    }).catch(() => {});
    return true;
  } catch {
    // The beacon must never surface its own error.
    return false;
  }
}

// @ts-check
/**
 * Library-level settings cache.
 *
 * The server persists settings in a SQLite key-value table (see
 * `bpp/db/settings.py`). On boot we load the whole bag once via
 * `/api/v1/settings`. Subsequent reads come from the in-memory cache;
 * writes update the cache + fire a PUT in the background.
 *
 * Bridged onto window via index.html's module bootstrap so the
 * existing classic callers — primarily app.js's initApp() bootstrap
 * and the settings modal — keep working unchanged.
 */

import { apiFetch } from "./api-client.mjs";
import { toastError } from "./toast.mjs";

/**
 * In-memory cache of settings keyed by name. Mutated by loadSettings
 * and saveSetting. Exported for tests; production code should always
 * go through getSetting / saveSetting.
 *
 * @type {Record<string, string>}
 */
export let _dbSettings = {};

/**
 * Replace the cache contents — used internally by tests and
 * loadSettings(). The export is named with an underscore to
 * discourage direct use from app code.
 *
 * @param {Record<string, string>} fresh
 */
export function _setDbSettings(fresh) {
  _dbSettings = fresh;
}

/**
 * Fetch settings from the server and replace the cache. Swallows
 * errors — startup must be able to continue with an empty cache so
 * the rest of bootstrap can run with `getSetting(key, fallback)`.
 *
 * @returns {Promise<void>}
 */
export async function loadSettings() {
  try {
    /** @type {any} */
    const resp = await apiFetch("/api/v1/settings");
    _dbSettings = resp || {};
  } catch {
    _dbSettings = {};
  }
}

/**
 * Synchronous read from the cache. Returns the fallback when the key
 * is missing — every call site supplies a default so clients are
 * resilient to a fresh DB.
 *
 * @template T
 * @param {string} key
 * @param {T} fallback
 * @returns {string | T}
 */
export function getSetting(key, fallback) {
  const v = _dbSettings[key];
  return v !== undefined ? v : fallback;
}

/**
 * Optimistic write. Updates the in-memory cache immediately and
 * fires a PUT in the background — we don't await the network round
 * trip because settings writes are fire-and-forget UI plumbing.
 *
 * @param {string} key
 * @param {unknown} value
 */
export function saveSetting(key, value) {
  _dbSettings[key] = String(value);
  // Fire-and-forget: the cache is already updated so reads stay correct
  // this session, but per the error-toast policy a failed PUT must name
  // the action rather than fall through silently — the user's change
  // won't survive a restart, and they deserve to know.
  apiFetch("/api/v1/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ [key]: value }),
  }).catch((e) => {
    toastError("save that setting", e);
  });
}

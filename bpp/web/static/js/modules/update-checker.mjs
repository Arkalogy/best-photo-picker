// @ts-check
/**
 * Update checker — polls `/api/v1/update/check` for new GitHub releases
 * and surfaces a dismissable banner when one's available.
 *
 * Persistence:
 *   localStorage["bpp_check_updates"] = "true" | "false"   (master toggle)
 *   localStorage["bpp_update_dismissed"] = <version>       (skip banner for this rev)
 *
 * Bridged onto window — checkForUpdates / dismissUpdateBanner /
 * toggleCheckUpdates / manualUpdateCheck / initUpdateChecker /
 * showUpdateBanner / updateSettingsLabel are all referenced from
 * inline `` in `index.html` (banner dismiss, settings
 * toggle, "Check Now" button) and from app.js's bootstrap.
 */

import { apiFetch } from "./api-client.mjs";
import { toast } from "./toast.mjs";

const KEY_ENABLED = "bpp_check_updates";
const KEY_DISMISSED = "bpp_update_dismissed";

/**
 * @typedef {Object} UpdateInfo
 * @property {"ok" | "error"} [status]
 * @property {boolean} available
 * @property {string} [latest]
 * @property {string} [current]
 * @property {string} [url]
 * @property {string} [error]
 * @property {string} [error_message]
 */

/**
 * Hit /api/update/check. When `force` is true the user explicitly
 * asked to check, so we ignore the master toggle and the
 * "dismissed" memo, surface a "you're current" toast on negative
 * results, and surface a real error toast on failure.
 *
 * Returns a Promise that resolves when the check completes (success
 * or handled error) so callers like manualUpdateCheck can wait for
 * the actual finish instead of guessing with a timeout.
 *
 * @param {boolean} [force]
 * @returns {Promise<void>}
 */
export function checkForUpdates(force) {
  const enabled = localStorage.getItem(KEY_ENABLED) !== "false";
  if (!enabled && !force) return Promise.resolve();

  const url = force ? "/api/v1/update/check?force=1" : "/api/v1/update/check";
  return apiFetch(url)
    .then(/** @param {UpdateInfo} data */ (data) => {
      // Server reported an error (network down, 404, rate limit, etc.)
      // — don't lie to the user with "up to date".
      if (data.status === "error") {
        updateSettingsLabel(data);
        if (force) {
          toast(data.error_message || "Couldn't check for updates", true);
        }
        return;
      }
      if (data.available) {
        const dismissed = localStorage.getItem(KEY_DISMISSED);
        if (!force && dismissed === data.latest) return;
        showUpdateBanner(data);
      } else if (force) {
        toast("You're on the latest version (v" + data.current + ")");
      }
      updateSettingsLabel(data);
    })
    .catch(() => {
      // Transport-level failure (apiFetch couldn't reach the server).
      // Distinct from a server-side update-check failure.
      if (force) toast("Couldn't reach the local server to check for updates", true);
    });
}

/** @param {UpdateInfo} data */
export function showUpdateBanner(data) {
  const banner = document.getElementById("update-banner");
  const text = document.getElementById("update-banner-text");
  const link = /** @type {HTMLAnchorElement | null} */ (
    document.getElementById("update-banner-link")
  );
  if (!banner || !text || !link) return;
  text.textContent = "v" + data.latest + " is available";
  link.href = data.url || "#";
  banner.classList.remove("hidden");
}

export function dismissUpdateBanner() {
  const banner = document.getElementById("update-banner");
  banner?.classList.add("hidden");
  const text = document.getElementById("update-banner-text")?.textContent || "";
  const ver = text.match(/v([\d.]+)/);
  if (ver) localStorage.setItem(KEY_DISMISSED, ver[1]);
}

/** @param {boolean} checked */
export function toggleCheckUpdates(checked) {
  localStorage.setItem(KEY_ENABLED, checked ? "true" : "false");
}

export async function manualUpdateCheck() {
  const btn = /** @type {HTMLButtonElement | null} */ (
    document.getElementById("check-updates-btn")
  );
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Checking…";
  }
  try {
    // Await the real promise so the button reflects actual fetch
    // state, not a 3-second guess. Cached responses now restore the
    // button in ~50ms; slow GitHub fetches keep it disabled until the
    // check actually finishes, preventing parallel-click races.
    await checkForUpdates(true);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "Check Now";
    }
  }
}

/** @param {UpdateInfo} data */
export function updateSettingsLabel(data) {
  const label = document.getElementById("update-status-label");
  if (!label) return;
  if (data.status === "error") {
    // Honest about not knowing — never claim "up to date" when the
    // check actually failed.
    label.textContent =
      "Couldn't check (v" + (data.current || "?") + " installed)";
    label.title = data.error_message || "";
    return;
  }
  label.title = "";
  if (data.available) {
    label.textContent =
      "v" + data.latest + " available (you have v" + data.current + ")";
  } else {
    label.textContent = "Up to date (v" + data.current + ")";
  }
}

export function initUpdateChecker() {
  const toggle = /** @type {HTMLInputElement | null} */ (
    document.getElementById("check-updates-toggle")
  );
  const enabled = localStorage.getItem(KEY_ENABLED) !== "false";
  if (toggle) toggle.checked = enabled;

  // Check after 5s delay on startup
  if (enabled) setTimeout(() => checkForUpdates(false), 5000);
}

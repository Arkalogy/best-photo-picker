// @ts-check
/**
 * Face-recognition pip install flow.
 *
 * Extracted from analysis.mjs during the v0.1 cleanup. Owns the two
 * helper functions called from the "Reanalyze" path when the user
 * doesn't have `bppicker[faces]` installed yet:
 *
 *   * _promptFaceInstall — confirm overlay with "Skip faces" /
 *     "Install faces" buttons (the latter only if the install path is
 *     viable, e.g. not a frozen Tauri sidecar).
 *   * _doFaceInstall — runs the install via /api/v1/install/faces,
 *     streams stdout into the overlay, resolves true on success,
 *     "restart" when it worked but needs an app restart to take effect.
 *
 * Re-exported from analysis.mjs.
 */

import { apiFetch } from "./api-client.mjs";
import { esc } from "./text-format.mjs";
import { appConfirm, _setConfirmResolve, _showConfirmOverlay } from "./dialogs.mjs";
import {
  followInstall,
  installAllowedNow,
  showInstallDone,
  toastInstallError,
} from "./install-done.mjs";

export function _promptFaceInstall() {
  /** @type {any} */
  const win = window;
  return new Promise((resolve) => {
    _setConfirmResolve(resolve);
    // While any install waits for a restart, analysis is refused (with or
    // without faces): pip may have upgraded libraries its processes load.
    const analysisBlocked = !!win.installRestartPending?.length;
    const skipBtn = analysisBlocked
      ? ""
      : `<button data-action="resolveConfirm" data-arg0="continue">Skip faces</button>`;
    const installBtn = win.faceInstallable
      ? `<button class="primary" id="confirm-ok" data-action="resolveConfirm" data-arg0="install">Install faces</button>`
      : "";
    const sub = win.faceInstallable
      ? "Install now to detect and group people in your photos, or continue without face grouping."
      : win.faceWaitingForRestart
        ? "Restart Best Photo Picker to finish installing face recognition."
        : win.faceLicenseNeeded
          ? "Face recognition is installed but its license isn't accepted — review it in Settings → Models, then try again."
          : win.installBlocked
            ? `Face recognition can't be installed right now: ${esc(win.installBlocked)}.`
            : win.installRestartPending?.length
              ? "Restart Best Photo Picker to finish a recent install, then you can install face recognition."
              : "To enable: pip install bppicker[faces], then restart.";
    const title = win.interruptedInstalls?.includes("faces")
      ? "The face recognition install didn't finish."
      : "Face grouping is not installed.";
    const html = `<p>${title}</p>
      <p class="confirm-sub">${sub}</p>
      <div class="confirm-actions">
        <button data-action="resolveConfirm" data-arg0="false">Cancel</button>
        ${skipBtn}
        ${installBtn}
      </div>`;
    _showConfirmOverlay(html);
  });
}

export async function _doFaceInstall() {
  /** @type {any} */
  const win = window;
  if (!(await installAllowedNow())) return false;
  const ok = await appConfirm(
    "Install face recognition?",
    "This will download and install a Python package from PyPI. Only proceed if you trust your network."
  );
  if (!ok) return false;
  try {
    await apiFetch("/api/v1/install/faces", { method: "POST" });
  } catch (e) {
    toastInstallError("install face recognition", e);
    return false;
  }

  return new Promise((resolve) => {
    _setConfirmResolve(null);
    const html = `<p>Installing face recognition...</p>
      <div id="install-log" style="max-height:180px;overflow-y:auto;font-family:monospace;font-size:11px;background:var(--bg-tertiary);padding:8px;border-radius:6px;margin:8px 0;white-space:pre-wrap;color:var(--text-secondary)"></div>
      <div class="confirm-actions">
        <button disabled id="install-close-btn" data-action="resolveConfirm" data-arg0="false">Close</button>
      </div>`;
    _showConfirmOverlay(html);

    const logEl = document.getElementById("install-log");
    const closeBtn = /** @type {HTMLButtonElement | null} */ (
      document.getElementById("install-close-btn")
    );
    followInstall("faces", (line) => {
      if (logEl) {
        logEl.textContent += line + "\n";
        logEl.scrollTop = logEl.scrollHeight;
      }
    }).then(
      (/** @type {any} */ done) => {
        win.resolveConfirm?.(false);
        if (showInstallDone("Face recognition", "faces", done)) {
          win.faceWaitingForRestart = true;
          resolve("restart"); // usable after a restart (and analysis waits too)
        } else {
          resolve(true); // it was there already: nothing written, nothing waits
        }
      },
      (/** @type {any} */ err) => {
        if (logEl) {
          logEl.textContent += "\nError: " + err.message;
          if (err.restartRequired) {
            logEl.textContent += "\nRestart Best Photo Picker, then try again.";
          }
        }
        if (closeBtn) {
          closeBtn.disabled = false;
          closeBtn.textContent = "Close";
        }
        _setConfirmResolve(() => resolve(false));
      }
    );
  });
}

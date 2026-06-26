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
 *     streams stdout into the overlay, resolves true on success.
 *
 * Re-exported from analysis.mjs.
 */

import { apiFetch, authEventSource } from "./api-client.mjs";
import { appConfirm, _setConfirmResolve, _showConfirmOverlay } from "./dialogs.mjs";
import { parseSSE } from "./format-helpers.mjs";
import { toast, toastError } from "./toast.mjs";

export function _promptFaceInstall() {
  /** @type {any} */
  const win = window;
  return new Promise((resolve) => {
    _setConfirmResolve(resolve);
    const installBtn = win.faceInstallable
      ? `<button class="primary" id="confirm-ok" data-action="resolveConfirm" data-arg0="install">Install faces</button>`
      : "";
    const sub = win.faceInstallable
      ? "Install now to detect and group people in your photos, or continue without face grouping."
      : "To enable: pip install bppicker[faces], then restart.";
    const html = `<p>Face grouping is not installed.</p>
      <p class="confirm-sub">${sub}</p>
      <div class="confirm-actions">
        <button data-action="resolveConfirm" data-arg0="false">Cancel</button>
        <button data-action="resolveConfirm" data-arg0="continue">Skip faces</button>
        ${installBtn}
      </div>`;
    _showConfirmOverlay(html);
  });
}

export async function _doFaceInstall() {
  /** @type {any} */
  const win = window;
  const ok = await appConfirm(
    "Install face recognition?",
    "This will download and install a Python package from PyPI. Only proceed if you trust your network."
  );
  if (!ok) return false;
  let resp;
  try {
    resp = await apiFetch("/api/v1/install/faces", { method: "POST" });
  } catch (e) {
    toastError("install face recognition", e);
    return false;
  }
  if (resp.error) {
    toastError("install face recognition", new Error(resp.error));
    return false;
  }
  if (resp.status === "already_installed") return true;

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
    const src = authEventSource("/api/v1/install/faces/progress");
    src.onmessage = (ev) => {
      const msg = /** @type {any} */ (parseSSE(ev.data));
      if (!msg) return;
      if (msg.type === "log") {
        if (logEl) {
          logEl.textContent += msg.message + "\n";
          logEl.scrollTop = logEl.scrollHeight;
        }
      } else if (msg.type === "done") {
        src.close();
        win.resolveConfirm?.(false);
        toast("Face recognition installed successfully");
        resolve(true);
      } else if (msg.type === "error") {
        src.close();
        if (logEl) logEl.textContent += "\nError: " + msg.message;
        if (closeBtn) {
          closeBtn.disabled = false;
          closeBtn.textContent = "Close";
        }
        _setConfirmResolve(() => resolve(false));
      }
    };
    src.onerror = () => {
      src.close();
      if (closeBtn) {
        closeBtn.disabled = false;
        closeBtn.textContent = "Close";
      }
      _setConfirmResolve(() => resolve(false));
    };
  });
}

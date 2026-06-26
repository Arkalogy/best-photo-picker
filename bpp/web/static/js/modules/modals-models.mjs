// @ts-check
/**
 * Settings → Models tab: enable/disable models, redownload, uninstall,
 * install optional packages.
 *
 * Extracted from modals.mjs during the v0.1 cleanup. ~520 LOC of the
 * settings modal that managed the per-feature install + lifecycle.
 *
 * Re-exported from modals.mjs.
 */

import { apiFetch } from "./api-client.mjs";
import { appConfirm } from "./dialogs.mjs";
import { toast, toastError } from "./toast.mjs";
import { authEventSource } from "./api-client.mjs";
import { parseSSE } from "./format-helpers.mjs";
import { hideStatusProgress, showStatusProgress } from "./analysis-status.mjs";
import { loadModelsList } from "./modals-models-list.mjs";
import { escapeAttr } from "./text-format.mjs";
export { loadModelsList };



/**
 * @param {HTMLInputElement} checkbox
 */
export async function toggleModel(checkbox) {
  if (typeof checkbox === "string" || checkbox === undefined) checkbox = this;
  const key = checkbox.dataset.toggleKey;
  const enabled = checkbox.checked;
  try {
    await apiFetch("/api/v1/models/toggle", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key, enabled }),
    });
    const row = checkbox.closest(".model-row");
    const labelEl = row?.querySelector(".model-name");
    const label = (labelEl?.textContent || "").trim();
    if (enabled) {
      toast(label + " enabled — will run on next analysis");
    } else {
      toast(label + " disabled — skipped during analysis (faster, lower quality)");
    }
  } catch (e) {
    checkbox.checked = !enabled;
    toastError("toggle the model", e);
  }
}

/**
 * @param {HTMLButtonElement} btn
 * @param {Array<{ name: string, exists?: boolean, size_bytes?: number }> | string[]} files
 * @param {string} label
 * @param {string} desc
 */
export async function redownloadFeature(btn, files, label, desc) {
  if (btn === undefined || typeof btn === "string") {
    // Called via dispatcher: this=button, arg0=label, arg1=desc
    const _btn = btn, _files = files;
    // @ts-ignore — dispatcher path swaps args; tsc can't follow the back-compat
    btn = this; files = JSON.parse((/** @type {any} */ (this)).dataset.files || "[]");
    // @ts-ignore
    label = /** @type {string} */ (_btn); desc = /** @type {string} */ (_files);
  }
  // Back-compat: callers may still pass a plain string[] of names.
  /** @type {Array<{ name: string, exists: boolean, size_bytes: number }>} */
  const normalized = (files || []).map((f) =>
    typeof f === "string"
      ? { name: f, exists: false, size_bytes: 0 }
      : { name: f.name, exists: !!f.exists, size_bytes: f.size_bytes || 0 }
  );
  if (normalized.length === 0) return;

  const fmtBytes = (n) => {
    if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
    if (n >= 1024) return `${(n / 1024).toFixed(0)} KB`;
    return `${n} B`;
  };
  const rows = normalized
    .map((f) => {
      const sizeStr = f.exists ? fmtBytes(f.size_bytes) : "missing — will fetch";
      return `<li>
        <span class="ml-name">${escapeAttr(f.name)}</span>
        <span class="ml-meta">${sizeStr}</span>
      </li>`;
    })
    .join("");
  const present = normalized.filter((f) => f.exists);
  const allPresent = present.length === normalized.length;
  const action = allPresent ? "Re-download" : "Download";
  const verb = allPresent
    ? "Existing files will be replaced with a fresh copy from upstream."
    : "Missing files will be fetched from upstream.";
  const bodyHTML = `<ul class="ml-consent-list">${rows}</ul>
    <p class="confirm-sub ml-consent-foot">${verb}
      Cached at ~/.cache/bpp. SHA-256 verified before use.</p>`;
  const ok = await appConfirm(
    action + " " + (label || "model") + "?",
    null,
    { okLabel: action, bodyHTML },
  );
  if (!ok) return;

  btn.disabled = true;
  btn.classList.add("downloading");
  const detail = desc ? " (" + desc + ")" : "";
  try {
    for (const f of normalized) {
      await apiFetch("/api/v1/models/redownload", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: f.name }),
        signal: AbortSignal.timeout(180000),
      });
    }
    toast((label || "Model") + " downloaded" + detail);
    const overlay = document.getElementById("settings-overlay");
    if (overlay && overlay.classList.contains("visible")) loadModelsList();
  } catch (e) {
    toastError("download " + (label || "this model"), e);
  } finally {
    btn.disabled = false;
    btn.classList.remove("downloading");
    btn.innerHTML = "&#x21bb;";
  }
}

/**
 * Delete a feature's model files from disk to free space. The confirm
 * dialog lists each file with its on-disk size so the user knows
 * exactly what they're removing. Files can be re-downloaded later via
 * the redownload button or auto-fetched on the next analyze.
 *
 * @param {HTMLButtonElement} btn
 * @param {Array<{ name: string, exists: boolean, size_bytes: number }>} files
 * @param {string} label
 */
export async function uninstallFeature(btn, files, label) {
  if (btn === undefined || typeof btn === "string") {
    const _btn = btn;
    // @ts-ignore — dispatcher path
    btn = this; files = JSON.parse((/** @type {any} */ (this)).dataset.files || "[]");
    // @ts-ignore
    label = /** @type {string} */ (_btn);
  }
  const present = (files || []).filter((f) => f && f.exists);
  if (present.length === 0) return;
  const totalBytes = present.reduce((s, f) => s + (f.size_bytes || 0), 0);
  const fmtBytes = (n) => {
    if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
    if (n >= 1024) return `${(n / 1024).toFixed(0)} KB`;
    return `${n} B`;
  };
  const rows = present
    .map(
      (f) =>
        `<li>
          <span class="ml-name">${escapeAttr(f.name)}</span>
          <span class="ml-meta">${fmtBytes(f.size_bytes || 0)}</span>
        </li>`
    )
    .join("");
  const bodyHTML = `<ul class="ml-consent-list">${rows}</ul>
    <p class="confirm-sub ml-consent-foot">Frees ${fmtBytes(totalBytes)}.
      Re-download from this panel or auto-fetch on the next analyze.</p>`;

  const ok = await appConfirm(
    "Delete " + (label || "model") + "?",
    null,
    { okLabel: "Delete", okClass: "danger", bodyHTML },
  );
  if (!ok) return;

  btn.disabled = true;
  btn.classList.add("downloading");
  let totalFreed = 0;
  try {
    for (const f of present) {
      const resp = await apiFetch("/api/v1/models/uninstall", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: f.name }),
      });
      totalFreed += resp.bytes_freed || 0;
    }
    toast((label || "Model") + " deleted — freed " + fmtBytes(totalFreed));
    const overlay = document.getElementById("settings-overlay");
    if (overlay && overlay.classList.contains("visible")) loadModelsList();
  } catch (e) {
    toastError("uninstall " + (label || "this model"), e);
  } finally {
    btn.disabled = false;
    btn.classList.remove("downloading");
    btn.innerHTML = "&#x2716;";
  }
}

/**
 * @param {HTMLButtonElement} btn
 * @param {string} key
 * @param {string} label
 */
export async function installPackage(btn, key, label) {
  if (btn === undefined || typeof btn === "string") {
    const _btn = btn, _key = key;
    // @ts-ignore — dispatcher path
    btn = this; key = /** @type {string} */ (_btn); label = /** @type {string} */ (_key);
  }
  // Pull the actual pip specs from the server so the consent dialog
  // shows exactly what will be installed — package names, versions,
  // host. Mirrors the model-download / uninstall transparency.
  /** @type {string[]} */
  let packages = [];
  let host = "pypi.org";
  try {
    const info = await apiFetch("/api/v1/install/" + key + "/info");
    packages = info.packages || [];
    host = info.host || "pypi.org";
  } catch {
    // Fall through with empty list — degraded UX, still ask for consent.
  }
  const rows =
    packages.length > 0
      ? packages
          .map(
            (p) => `<li>
              <span class="ml-name">${escapeAttr(p)}</span>
              <span class="ml-meta">${escapeAttr(host)}</span>
            </li>`
          )
          .join("")
      : "";
  const list = rows ? `<ul class="ml-consent-list">${rows}</ul>` : "";
  const bodyHTML =
    list +
    `<p class="confirm-sub ml-consent-foot">Runs <code>pip install</code>
      in the current Python environment. The package and its
      dependencies are fetched from ${escapeAttr(host)}.</p>`;
  const ok = await appConfirm("Install " + label + "?", null, {
    okLabel: "Install",
    bodyHTML,
  });
  if (!ok) return;
  btn.disabled = true;
  btn.classList.add("installing");
  btn.textContent = "Resolving…";
  /** @type {any} */
  const win = window;
  // Also poke the global status bar so progress is visible if the user
  // closes the Settings modal — it's hidden behind the modal blur while
  // settings is open, but visible everywhere else.
  win.showStatusProgress?.("Installing " + label + "…", 0);
  try {
    const resp = await apiFetch("/api/v1/install/" + key, { method: "POST" });
    if (resp.status === "already_installed") {
      toast(label + " is already installed");
      loadModelsList();
      return;
    }
    await new Promise((resolve, reject) => {
      const src = authEventSource("/api/v1/install/" + key + "/progress");
      src.onmessage = (ev) => {
        const msg = /** @type {any} */ (parseSSE(ev.data));
        if (!msg) return;
        if (msg.type === "log") {
          // pip prints "Collecting X", "Downloading X (.. MB)",
          // "Installing collected packages: ...", "Successfully ..." —
          // pull a short summary and surface it on the button + status bar.
          const line = String(msg.message || "");
          const m =
            line.match(/^Collecting\s+(\S+)/) ||
            line.match(/^\s*Downloading\s+(\S+)/) ||
            line.match(/^Installing collected packages:\s*(.+)$/) ||
            line.match(/^Successfully installed\s+(.+)$/);
          if (m) {
            const verb = line.startsWith("Successfully")
              ? "Finalising"
              : line.trimStart().startsWith("Downloading")
                ? "Downloading"
                : line.startsWith("Installing")
                  ? "Installing"
                  : "Resolving";
            const stage = verb + " " + m[1].split(/[\s-]/)[0] + "…";
            btn.textContent = stage;
            win.showStatusProgress?.(stage, 0);
          }
        } else if (msg.type === "done") {
          src.close();
          toast(label + " installed successfully");
          resolve(undefined);
        } else if (msg.type === "error") {
          src.close();
          reject(new Error(msg.message || "Install failed"));
        }
      };
      src.onerror = () => {
        src.close();
        reject(new Error("Connection lost"));
      };
    });
    loadModelsList();
  } catch (e) {
    toastError("install " + (label || "this feature"), e);
  } finally {
    btn.disabled = false;
    btn.classList.remove("installing");
    btn.textContent = "Install";
    win.hideStatusProgress?.();
  }
}

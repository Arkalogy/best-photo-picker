// @ts-check
/**
 * Misc UI utilities: the export modal flow (toggle quality field, run
 * export, open the destination folder), the danger-zone "clear library"
 * + analysis-cache + recompute-hashes endpoints, and `show()` / `hide()`
 * primitives that flip the `hidden` class on a DOM node by id.
 *
 * Cross-file callees (`getExportPaths`, `hideSettings`, `validateInput`,
 * `loadAlbumList`, `showEmptyLibrary`, `renderGrid`) and the photos /
 * favorites / overrides / selectedPaths globals stay classic-side and are
 * looked up on `window`.
 */

import { _authToken, apiFetch, authEventSource } from "./api-client.mjs";
import { appConfirm } from "./dialogs.mjs";
import { reviewSensitiveBeforeExport } from "./sensitive.mjs";
import { esc, escapeJsAttr } from "./text-format.mjs";
import { toast, toastError } from "./toast.mjs";

// L-S3: large exports (100+ photos with format conversion) used to run
// synchronously inside the Flask request — the UI showed nothing for
// 30-60s and looked frozen. Above this threshold the export modal now
// switches to the streaming worker path (/api/v1/export/start +
// /api/v1/export/progress SSE) for per-photo progress. Smaller batches
// keep the synchronous path so the back-compat tests / sync callers
// don't change shape unnecessarily.
const _EXPORT_STREAM_THRESHOLD = 25;

/**
 * Stream large export jobs via /api/v1/export/start + the SSE
 * /api/v1/export/progress stream. Returns the same shape doExport
 * gets from the synchronous /api/v1/export — { count, failed, outdir,
 * disk_error } — so the toast / status-line render code stays one
 * implementation. Updates ``statusEl`` with per-photo progress
 * ("Exporting 23 of 100…") as events stream.
 *
 * @param {Record<string, any>} body
 * @param {HTMLElement | null} statusEl
 * @returns {Promise<{count: number, failed: number, outdir: string, disk_error: any}>}
 */
async function _streamExport(body, statusEl) {
  // 1) Kick off the worker; 202 means streaming has begun.
  const startResp = await apiFetch("/api/v1/export/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const total = startResp.total || 0;
  if (statusEl) statusEl.textContent = `Exporting 0 of ${total}…`;

  // 2) Open the SSE stream and update the status line per event. The
  // promise resolves on the 'done' event with the same payload the
  // synchronous endpoint returns directly.
  return new Promise((resolve, reject) => {
    const es = authEventSource("/api/v1/export/progress");
    es.onmessage = (ev) => {
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (msg.type === "export_progress") {
        if (statusEl) {
          statusEl.textContent = `Exporting ${msg.current} of ${msg.total}…`;
        }
      } else if (msg.type === "done") {
        es.close();
        resolve({
          count: msg.count,
          failed: msg.failed,
          outdir: msg.outdir,
          disk_error: msg.disk_error,
        });
      } else if (msg.type === "error" || msg.type === "cancelled") {
        es.close();
        reject(new Error(msg.message || msg.type));
      }
    };
    es.onerror = () => {
      es.close();
      reject(new Error("Lost connection to export stream"));
    };
  });
}

export function toggleExportQuality() {
  const fmtEl = /** @type {HTMLSelectElement | null} */ (document.getElementById("export-format"));
  const qField = /** @type {HTMLElement | null} */ (
    document.getElementById("export-quality-field")
  );
  if (!fmtEl || !qField) return;
  qField.style.display = fmtEl.value === "jpeg" ? "flex" : "none";
}

/** Revert the post-success UI (hidden Export button + 'Done' label)
 *  back to the pre-export state. Called by the input listener
 *  installed in _armPostSuccessRevert and by showExportModal on next
 *  open. Also clears the status line so the green 'Exported N photos'
 *  doesn't linger over a re-configured export. */
function _resetExportModalToReady() {
  const btn = document.getElementById("btn-do-export");
  if (btn instanceof HTMLElement) {
    // Project UI rule: don't set style.display = "" (would fall back
    // to CSS, which is currently 'inline-block' from .btn-primary but
    // could change). Explicit 'block' restores from the post-success
    // 'none' to a visible state, behaving the same in the flex parent.
    btn.style.display = "block";
    /** @type {HTMLButtonElement} */ (btn).disabled = false;
  }
  const cancelBtn = document.querySelector(
    '#export-modal-overlay [data-action="hideExportModal"]'
  );
  if (cancelBtn) cancelBtn.textContent = "Cancel";
  const statusEl = document.getElementById("export-status");
  if (statusEl) {
    statusEl.textContent = "";
    statusEl.className = "export-status export-status-center";
  }
}

/** After a successful export the Export button is hidden and Cancel
 *  reads 'Done'. If the user then changes ANY field (scope, outdir,
 *  format, max-size, quality), they're configuring a new export — flip
 *  the UI back so they can re-fire. One-shot: removes itself on the
 *  first change. */
function _armPostSuccessRevert() {
  const modal = document.getElementById("export-modal-overlay");
  if (!modal) return;
  /** @param {Event} _e */
  const handler = (_e) => {
    modal.removeEventListener("input", handler, true);
    modal.removeEventListener("change", handler, true);
    _resetExportModalToReady();
  };
  modal.addEventListener("input", handler, true);
  modal.addEventListener("change", handler, true);
}

export async function doExport() {
  /** @type {any} */
  const win = window;
  const outdirEl = /** @type {HTMLInputElement | null} */ (document.getElementById("export-dir"));
  const outdir = (outdirEl?.value || "").trim();
  if (!outdir) {
    toast("Enter an output folder path.", true);
    return;
  }

  // Sensitive-photo review gate (P0): runs BEFORE the busy state flips
  // on, so a cancel leaves the export modal exactly as it was. A
  // selection with no flagged photos passes through with no dialog.
  const _allPaths = win.getExportPaths ? [...win.getExportPaths()] : [];
  const _review = await reviewSensitiveBeforeExport(_allPaths);
  if (!_review.proceed) return;

  const statusEl = document.getElementById("export-status");
  if (statusEl) {
    statusEl.textContent = "Exporting…";
    statusEl.className = "export-status";
  }
  const btn = /** @type {HTMLButtonElement | null} */ (document.getElementById("btn-do-export"));
  if (btn) btn.disabled = true;

  const fmt = /** @type {HTMLSelectElement | null} */ (
    document.getElementById("export-format")
  )?.value;
  const maxSizeVal = /** @type {HTMLInputElement | null} */ (
    document.getElementById("export-max-size")
  )?.value;
  const quality = parseInt(
    /** @type {HTMLInputElement | null} */ (document.getElementById("export-quality"))?.value ||
      "0",
    10
  );

  const exportPaths = _review.paths;
  // ZIP-bundle mode: one best-photos.zip instead of a loose folder.
  // The server skips the gallery in this mode (it can't ride inside the
  // single file), so don't request one.
  const zipBundle = /** @type {HTMLInputElement | null} */ (
    document.getElementById("export-zip-toggle")
  )?.checked;
  // Copy method from the registry-populated dropdown (copy/hardlink/symlink
  // + plugin modes). The zip checkbox overrides it (zip is its own mode).
  const copyMode =
    /** @type {HTMLSelectElement | null} */ (document.getElementById("export-mode"))?.value ||
    "copy";
  /** @type {Record<string, any>} */
  const body = {
    outdir,
    selected_paths: exportPaths,
    gallery: !zipBundle,
    fmt,
    quality,
    mode: zipBundle ? "zip" : copyMode,
  };
  if (maxSizeVal) body.max_size = parseInt(maxSizeVal, 10);

  // L-S3: stream large exports so the user sees per-photo progress
  // instead of a 30-60s blank pause. Small batches keep the
  // synchronous path — same return shape, no UI flicker.
  const useStreaming = exportPaths.length >= _EXPORT_STREAM_THRESHOLD;

  let data;
  try {
    if (useStreaming) {
      data = await _streamExport(body, statusEl);
    } else {
      data = await apiFetch("/api/v1/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    }
  } catch (e) {
    if (btn) btn.disabled = false;
    if (statusEl) {
      const err = /** @type {any} */ (e);
      statusEl.textContent = "Error: " + (err.message || "unknown");
      statusEl.className = "export-status error";
    }
    toastError("export your photos", e);
    return;
  }

  // Post-success: hide the Export button so the user can't re-fire the
  // same export by accident, and rename 'Cancel' → 'Done' so closing
  // the modal reads as completion, not abort. showExportModal()
  // restores both on the next open. If the user changes ANY field
  // (scope, outdir, format, size, quality) the post-success UI reverts
  // — they're configuring a NEW export, so re-firing is intentional.
  if (btn instanceof HTMLElement) btn.style.display = "none";
  const cancelBtn = document.querySelector(
    '#export-modal-overlay [data-action="hideExportModal"]'
  );
  if (cancelBtn) cancelBtn.textContent = "Done";
  _armPostSuccessRevert();

  let msg = `Exported ${data.count} photo${data.count !== 1 ? "s" : ""}`;
  if (data.failed > 0) msg += ` (${data.failed} failed)`;
  if (statusEl) {
    statusEl.innerHTML =
      `<span class="export-result-msg">${esc(msg)}</span>` +
      `<button class="btn-open-folder" data-action="_openExportFolder" data-arg0="${escapeJsAttr(data.outdir)}">Open Folder</button>`;
    statusEl.className = "export-status success";
  }
  // Categorised disk error takes precedence over the generic "N failed"
  // toast — when the loop aborted it's because every subsequent photo
  // would have failed the same way, and the user needs that context to
  // actually fix it (free space / pick a writable folder) rather than
  // thinking 14 photos were corrupt.
  if (data.disk_error) {
    /** @type {Record<string, string>} */
    const labels = {
      no_space: "Disk full",
      permission: "Permission denied",
      read_only_fs: "Folder is read-only",
    };
    const label = labels[data.disk_error.category] || "Filesystem error";
    const at = data.disk_error.first_failed_index;
    toast(`${label} — export stopped at photo ${at}. Free space or pick another folder and retry.`, true);
  } else if (data.failed > 0) {
    // Non-disk failures (corrupt source, bad filename, format
    // conversion error) — surface a 'View log' shortcut. The per-photo
    // details are in Settings → Activity; without this affordance the
    // user has nowhere to go from a 'N failed' toast. Matches the
    // pattern Bug #8 (activity-log warning toast) and the window
    // onerror handler in app.mjs use.
    toast(`${data.failed} photo${data.failed !== 1 ? "s" : ""} failed to export`, "error", { /* toast-ok: summary, not an error pattern */
      action: {
        label: "View log",
        fn: () => {
          const win = /** @type {any} */ (window);
          if (typeof win.showActivityLog === "function") win.showActivityLog();
        },
      },
    });
  } else {
    toast(msg);
  }
}

/**
 * @param {string} path
 */
export async function _openExportFolder(path) {
  try {
    await apiFetch("/api/v1/open-folder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
  } catch (e) {
    toastError("open the export folder", e);
  }
}

/**
 * Show element by id (remove `hidden` class).
 *
 * @param {string} id
 */
export function show(id) {
  document.getElementById(id)?.classList.remove("hidden");
}

/**
 * Hide element by id (add `hidden` class).
 *
 * @param {string} id
 */
export function hide(id) {
  document.getElementById(id)?.classList.add("hidden");
}

export function validateClearConfirm() {
  const inp = /** @type {HTMLInputElement | null} */ (
    document.getElementById("clear-confirm-input")
  );
  const btn = document.getElementById("btn-clear-library");
  if (!inp || !btn) return;
  if (inp.value.trim().toLowerCase() === "delete") {
    btn.classList.add("enabled");
  } else {
    btn.classList.remove("enabled");
  }
}

export async function clearLibrary() {
  /** @type {any} */
  const win = window;
  const inp = /** @type {HTMLInputElement | null} */ (
    document.getElementById("clear-confirm-input")
  );
  if (!inp || inp.value.trim().toLowerCase() !== "delete") return;
  const btn = /** @type {HTMLButtonElement | null} */ (
    document.getElementById("btn-clear-library")
  );
  if (btn) {
    btn.textContent = "Deleting...";
    btn.classList.remove("enabled");
  }
  try {
    const resp = await fetch("/api/v1/library", {
      method: "DELETE",
      headers: { "Content-Type": "application/json", "X-Auth-Token": _authToken },
      body: JSON.stringify({ confirmation: "delete" }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      toast(data.error || "Failed to clear", true);
      return;
    }
    win.hideSettings?.();
    toast(`Cleared: ${data.photos_deleted} photos removed`);
    inp.value = "";
    validateClearConfirm();
    win.photos = [];
    win.selectedPaths = new Set();
    win.overrides = {};
    win.favorites = new Set();
    win.renderGrid?.();
    win.loadAlbumList?.();
    win.showEmptyLibrary?.();
  } catch (e) {
    toastError("clear the library", e);
  } finally {
    if (btn) btn.textContent = "Delete All";
  }
}

export async function recomputeHashes() {
  try {
    const data = await apiFetch("/api/v1/compute-hashes", { method: "POST" });
    if (data.status === "done") {
      toast("All photos already have hashes — duplicates are up to date");
    } else {
      toast(`Computing hashes for ${data.missing} photos in the background`);
    }
  } catch (e) {
    toastError("start hash computation", e);
  }
}

export async function clearAnalysisCache() {
  const ok = await appConfirm(
    "Clear the scoring cache? Next analysis will re-run all ML models from scratch. This does not delete photos."
  );
  if (!ok) return;
  try {
    const data = await apiFetch("/api/v1/analysis-cache", { method: "DELETE" });
    if (data.status === "no_cache") {
      toast("No cache to clear");
      return;
    }
    toast("Analysis cache cleared — re-analyze to rescore with latest models");
  } catch (e) {
    toastError("clear the cache", e);
  }
}

/* ── Native file picker ── */
export async function openBrowser() {
  /** @type {any} */
  const win = window;
  try {
    const data = await apiFetch("/api/v1/pick", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: "folder" }),
    });
    if (data.path) {
      const inp = /** @type {HTMLInputElement | null} */ (document.getElementById("input-dir"));
      if (inp) inp.value = data.path;
      win.validateInput?.();
    }
  } catch (e) {
    toastError("open the folder browser", e);
  }
}

export async function openExportBrowser() {
  try {
    const data = await apiFetch("/api/v1/pick", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: "folder" }),
    });
    if (data.path) {
      const inp = /** @type {HTMLInputElement | null} */ (document.getElementById("export-dir"));
      if (inp) {
        inp.value = data.path;
        // Programmatic value assignment does NOT fire 'input' in any
        // browser, so the modal's #export-dir listener (which enables /
        // disables the Export button based on emptiness) wouldn't see
        // the new value and the button would stay disabled after Browse.
        // Dispatching synthesises the event so all bound listeners react.
        inp.dispatchEvent(new Event("input", { bubbles: true }));
      }
    }
  } catch (e) {
    toastError("open the folder browser", e);
  }
}

/** @deprecated kept for HTML callers — import flow now uses a modal. */
export function validateInput() {
  /* no-op */
}
